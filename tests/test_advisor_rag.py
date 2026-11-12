import json
import socket
from unittest.mock import patch
import numpy as np
import pytest
from src.advisor import rag as rc
from src.advisor import service
from src.advisor.context import build_alert_context, what_if_loss_ratio, hypothesis_context
from src.llm_advisor import analyze


@pytest.fixture(autouse=True)
def no_live_ollama():
    with (
        patch.object(rc, "_post", side_effect=AssertionError("测试禁止真实 Ollama")),
        patch.object(rc._OPENER, "open", side_effect=AssertionError("测试禁止网络")),
    ):
        yield


@pytest.fixture
def kb(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    index = tmp_path / "index"
    monkeypatch.setattr(rc, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(rc, "INDEX_DIR", index)
    # default arguments in core are intentionally overridden at call sites below
    monkeypatch.setattr(service, "sync_knowledge", lambda: None)
    orig = rc.iter_chunks
    monkeypatch.setattr(rc, "iter_chunks", lambda: orig(knowledge))
    service.get_index.cache_clear()
    yield knowledge, index
    service.get_index.cache_clear()


def vectors(texts, **kwargs):
    return np.array([[1.0, 0.0] if "R008" in t else [0.0, 1.0] for t in texts], dtype=np.float32)


def test_chunking_markdown_and_overlap(tmp_path):
    sections = rc._md_sections("# 一级\n正文\n## 二级\n第二段")
    assert sections == [("一级", "正文"), ("一级 > 二级", "第二段")]
    pieces = rc._split("甲" * 1500, size=600, overlap=80)
    assert len(pieces) == 3 and pieces[0][-80:] == pieces[1][:80]


def test_xlsx_rule_per_row(tmp_path):
    from openpyxl import Workbook

    p = tmp_path / "rules.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["规则ID", "说明"])
    ws.append(["R001", "甲"])
    ws.append(["R002", "乙"])
    wb.save(p)
    result = rc._xlsx_records(p)
    assert len(result) == 2 and "R001" in result[0][1] and "R002" not in result[0][1]


def test_index_incremental_and_hybrid(kb):
    knowledge, index = kb
    (knowledge / "a.md").write_text("# 频度\nR008 出险频度恶化")
    (knowledge / "b.md").write_text("# 费用\n费用率")
    with patch.object(rc, "embed", side_effect=vectors) as embed:
        assert service.rebuild_index()["reembedded"] == 2
        embed.reset_mock()
        assert service.rebuild_index()["reembedded"] == 0
        assert not embed.called
        (knowledge / "a.md").write_text("# 频度\nR008 出险频度恶化调整")
        assert service.rebuild_index()["reembedded"] == 1
        found = rc.KnowledgeIndex(index).search("R008", k=1)
        assert found[0]["source"] == "a.md"


def test_skill_loader(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: A\nversion: 1\npriority: 20\n---\n后")
    (tmp_path / "b.md").write_text("---\npriority: 1\n---\n先")
    (tmp_path / "c.md").write_text("---\nenabled: false\n---\n禁用")
    text, meta = rc.load_skills(tmp_path)
    assert text == "先\n\n后" and len(meta) == 2 and meta[0]["version"] == "1"


def test_context_and_what_if():
    ctx = build_alert_context(
        {"预期成本率": 95.0, "赔款": 1200.0, "缺失": float("nan")}, {"yellow": 90}, {"频度贡献": 2.0}
    )
    assert ctx["预期成本率"] == "95.0%" and ctx["赔款"] == "1,200 元"
    assert ctx["缺失"] == "数据不足" and ctx["频度贡献"] == "+2.0 个百分点"
    assert hypothesis_context("赔付率下降 3.5 个百分点后成本率是多少", ctx)["假设后成本率"] == "91.5%"
    assert hypothesis_context("赔付率下降3个百分点", {})["假设分析"].startswith("数据不足")
    with pytest.raises(ValueError):
        what_if_loss_ratio(95, -1)


def test_local_only_guard(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda self, address: "local")
    rc.enforce_local_only()
    with socket.socket() as sock:
        with pytest.raises(ConnectionError):
            sock.connect(("8.8.8.8", 53))
        assert sock.connect(("127.0.0.1", 11434)) == "local"


def test_external_ollama_rejected(monkeypatch):
    monkeypatch.setattr(rc, "OLLAMA_BASE", "https://example.com")
    # original function bypasses the fixture mock only to verify preflight; never reaches opener
    with patch.object(rc, "_post", wraps=ORIGINAL_POST):
        with pytest.raises(ConnectionError):
            rc._post("/api/chat", {})


ORIGINAL_POST = rc._post


def response(answer="黄色预警 [系统]"):
    return {
        "answer": answer,
        "evidence": [{"claim": "成本率95%", "source": "系统"}],
        "suggestions": [],
        "uncertainty": [],
        "refused": False,
    }


def test_structured_compat_and_hypothesis_context():
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(
            rc, "chat_stream", return_value=iter([{"message": {"content": json.dumps(response())}}])
        ) as stream,
    ):
        result = analyze({"预期成本率": 95}, "黄色阈值90%")
        assert result["risk_summary"] == "黄色预警 [系统]" and result["evidence"] == ["成本率95% [系统]"]
        assert stream.call_args.kwargs["fmt"] == rc.ANSWER_SCHEMA
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(rc, "chat_stream", return_value=iter([{"message": {"content": "结果 [系统]"}}])) as stream,
    ):
        service.ask_advisor("赔付率下降3个百分点后成本率是多少", {"预期成本率": "95.0%"})
        assert "92.0%" in stream.call_args.args[0][-1]["content"]


def test_stream_citations_numbers_and_history():
    index = type(
        "Index",
        (),
        {"search": lambda *a, **k: [{"cite": "K1", "source": "规则.md", "section": "阈值", "cos": 1.0, "text": "90%"}]},
    )()
    with (
        patch.object(service, "get_index", return_value=index),
        patch.object(
            rc,
            "chat_stream",
            return_value=iter([{"message": {"content": "90% [K1]"}}, {"message": {"content": " 77% [K9]"}}]),
        ) as stream,
    ):
        history = [{"role": "user", "content": str(i)} for i in range(8)]
        iterator, meta = service.stream_advisor("为什么", {}, history=history)
        assert meta["raw"] == ""
        assert "".join(iterator) == "90% [K1] 77% [K9]"
        assert meta["invalid_citations"] == ["K9"] and meta["untraced_numbers"] == ["77"]
        assert len(stream.call_args.args[0]) == 6
        assert meta["hits"][0]["text"] == "90%" and "total_s" in meta["timing"]


@pytest.mark.parametrize("raw", ["bad json", "{}", '{"answer": 1}'])
def test_bad_json_degrades(raw):
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(rc, "chat_stream", return_value=iter([{"message": {"content": raw}}])),
    ):
        result = service.ask_advisor("q", json_mode=True)
        assert "error" in result


def test_ollama_unavailable_degrades():
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(rc, "chat_stream", side_effect=OSError("offline")),
    ):
        result = service.ask_advisor("q")
        assert "offline" in result["error"]
        assert "".join(service.stream_advisor("q")[0]).endswith("offline")


def test_health_missing_and_stale(kb):
    result = service.advisor_health()
    assert not result["online"] and result["stale"]


def test_compat_model_timeout_and_threshold_override():
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(
            rc, "chat_stream", return_value=iter([{"message": {"content": json.dumps(response())}}])
        ) as stream,
    ):
        analyze({"预期成本率": 95}, "黄色阈值=88%", model="local-test", timeout=7)
        assert stream.call_args.kwargs["model"] == "local-test" and stream.call_args.kwargs["timeout"] == 7
        assert "88.0%" in stream.call_args.args[0][-1]["content"]


def test_structured_evidence_invalid_citation():
    payload = response()
    payload["evidence"][0]["source"] = "K99"
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(rc, "chat_stream", return_value=iter([{"message": {"content": json.dumps(payload)}}])),
    ):
        assert service.ask_advisor("q", json_mode=True)["invalid_citations"] == ["K99"]


def test_health_models_and_hash_staleness(kb, tmp_path, monkeypatch):
    knowledge, index = kb
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "config").mkdir()
    monkeypatch.setattr(rc, "ROOT", root)
    (knowledge / "a.md").write_text("R008")
    with patch.object(rc, "embed", side_effect=vectors):
        service.rebuild_index()
    from unittest.mock import MagicMock

    response = MagicMock()
    response.read.return_value = json.dumps({"models": [{"name": rc.CHAT_MODEL}, {"name": rc.EMBED_MODEL}]}).encode()
    with patch.object(rc._OPENER, "open") as opener:
        opener.return_value.__enter__.return_value = response
        result = service.advisor_health()
        assert result["online"] and result["chat_model"] and result["embed_model"] and not result["stale"]
        (root / "docs/new.md").write_text("新增文档")
        assert service.advisor_health()["stale"]

"""第 3 轮顾问修复：来源加权、镜像去重、假设赔付率、友好报错、模型名标签。"""

import socket
import urllib.error
from unittest.mock import patch

import numpy as np
import pytest

from src.advisor import rag as rc
from src.advisor import service
from src.advisor.context import hypothesis_context, what_if_loss_ratio


@pytest.fixture(autouse=True)
def no_live_ollama():
    with (
        patch.object(rc, "_post", side_effect=AssertionError("测试禁止真实 Ollama")),
        patch.object(rc._OPENER, "open", side_effect=AssertionError("测试禁止网络")),
    ):
        yield


@pytest.fixture
def kb(tmp_path, monkeypatch):
    knowledge, index = tmp_path / "knowledge", tmp_path / "index"
    knowledge.mkdir()
    monkeypatch.setattr(rc, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(rc, "INDEX_DIR", index)
    monkeypatch.setattr(service, "sync_knowledge", lambda: None)
    orig = rc.iter_chunks
    monkeypatch.setattr(rc, "iter_chunks", lambda: orig(knowledge))
    service.get_index.cache_clear()
    yield knowledge, index
    service.get_index.cache_clear()


def same_vec(texts, **kwargs):
    return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)


def test_source_weight_product_and_default():
    w = {"调研问卷": 0.5, "平台规则/": 1.2}
    assert rc.source_weight("业务输入/业务规则调研问卷.xlsx", w) == 0.5
    assert rc.source_weight("平台规则/核心指标口径速查.md", w) == pytest.approx(1.2)
    assert rc.source_weight("指标口径/大创资料摘要.md", w) == 1.0
    assert rc.source_weight("x", {}) == 1.0


def test_questionnaire_downweighted_and_mirror_deduplicated(kb):
    knowledge, index = kb
    (knowledge / "业务输入").mkdir()
    (knowledge / "镜像").mkdir()
    q = "# 问卷\n累计出险频度 是否包含 RBNR 请教您"
    (knowledge / "业务输入" / "调研问卷.md").write_text(q)
    (knowledge / "镜像" / "调研问卷.md").write_text(q)
    (knowledge / "定义.md").write_text("# 定义\n累计出险频度 含 RBNR 不含 IBNR")
    with patch.object(rc, "embed", side_effect=same_vec):
        service.rebuild_index()
        idx = rc.KnowledgeIndex(index)
        plain = idx.search("累计出险频度 RBNR 请教", k=5, source_weights={})
        weighted = idx.search("累计出险频度 RBNR 请教", k=5, source_weights={"调研问卷": 0.5})
    # 两份内容相同的问卷只保留一条
    assert sum("调研问卷" in h["source"] for h in plain) == 1
    assert len(plain) == 2
    assert weighted[0]["source"] == "定义.md"
    assert [h["cite"] for h in weighted] == ["K1", "K2"]


def test_hypothesis_also_gives_loss_ratio():
    ctx = {"预期成本率": "108.4%", "预期赔付率": "81.4%"}
    out = hypothesis_context("如果预期赔付率下降 5 个百分点，成本率是多少", ctx)
    assert out["假设后成本率"] == "103.4%"
    assert out["假设后预期赔付率"] == "76.4%"
    assert "假设后预期赔付率" not in what_if_loss_ratio(95, 1)
    assert "假设后预期赔付率" not in what_if_loss_ratio(95, 1, "数据不足")


def test_friendly_errors():
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    assert "未启动" in service.friendly_error(refused)
    missing = urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 404, "Not Found", {}, None)
    assert "未安装模型" in service.friendly_error(missing)
    assert "超时" in service.friendly_error(urllib.error.URLError(socket.timeout("timed out")))
    assert service.friendly_error(OSError("offline")) == "本地模型调用失败：offline"


def test_model_names_accept_latest_tag():
    names = service._model_names([{"name": "bge-m3-local:latest"}, {"name": "qwen3.5:4b"}])
    assert "bge-m3-local" in names and "qwen3.5:4b" in names and "qwen3.5" not in names


def test_max_per_source_caps_one_document(kb):
    knowledge, index = kb
    (knowledge / "长文.md").write_text("# 一\n出险频度 甲\n# 二\n出险频度 乙\n# 三\n出险频度 丙")
    (knowledge / "短文.md").write_text("# 四\n出险频度 丁")
    with patch.object(rc, "embed", side_effect=same_vec):
        service.rebuild_index()
        idx = rc.KnowledgeIndex(index)
        capped = idx.search("出险频度", k=3, source_weights={}, max_per_source=2)
        free = idx.search("出险频度", k=4, source_weights={}, max_per_source=0)
    assert sum(h["source"] == "长文.md" for h in capped) == 2
    assert any(h["source"] == "短文.md" for h in capped)
    assert sum(h["source"] == "长文.md" for h in free) == 3


def test_lookup_mismatch_ignores_version_labels():
    facts = [{"label": "橙色阈值", "value": "95%", "source": "config/alert_rules.yaml"}]
    raw = "橙色阈值为 95% [系统查表]。11.1 修复后新增 R011，11.1反馈已处理。"
    assert service.lookup_mismatch(raw, facts, "橙色阈值是多少？", None) == []
    assert service.lookup_mismatch("橙色阈值为 96%", facts, "橙色阈值是多少？", None)

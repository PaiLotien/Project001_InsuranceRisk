"""本地答案缓存：命中、各类失效、只缓存干净回答、预热不外连。"""

import json
from unittest.mock import patch

import pytest

from src.advisor import cache as ac
from src.advisor import rag as rc
from src.advisor import service

HIT = [{"cite": "K1", "source": "规则.md", "section": "阈值", "cos": 1.0, "text": "黄色阈值 90%"}]


@pytest.fixture(autouse=True)
def no_live_ollama():
    with (
        patch.object(rc, "_post", side_effect=AssertionError("测试禁止真实 Ollama")),
        patch.object(rc._OPENER, "open", side_effect=AssertionError("测试禁止网络")),
    ):
        yield


@pytest.fixture
def env(tmp_path, monkeypatch):
    index_dir, skills_dir = tmp_path / "index", tmp_path / "skills"
    index_dir.mkdir()
    skills_dir.mkdir()
    (index_dir / "manifest.json").write_text(json.dumps({"files": {"a.md": "h1"}, "embed_model": "e"}))
    (skills_dir / "s.md").write_text("# 角色\n顾问")
    monkeypatch.setattr(rc, "INDEX_DIR", index_dir)
    monkeypatch.setattr(rc, "SKILLS_DIR", skills_dir)
    monkeypatch.setitem(rc.CFG, "cache_enabled", True)
    index = type("Index", (), {"search": lambda *a, **k: [dict(h) for h in HIT]})()
    with patch.object(service, "get_index", return_value=index):
        yield index_dir, skills_dir


def fake_stream(text="黄色阈值是 90% [K1]"):
    return patch.object(rc, "chat_stream", side_effect=lambda *a, **k: iter([{"message": {"content": text}}]))


def test_normalize_question():
    assert ac.normalize_question(" R005 的黄色阈值是多少？ ") == ac.normalize_question("r005的黄色阈值是多少?")
    assert ac.normalize_question("黄色阈值") != ac.normalize_question("红色阈值")


def test_cache_hit_skips_model(env):
    with fake_stream() as stream:
        first = service.ask_advisor("R005 黄色阈值是多少？")
        second = service.ask_advisor("R005 黄色阈值是多少")  # 标点不同，规范化后相同
    assert stream.call_count == 1
    assert not first["cached"] and second["cached"]
    assert second["raw"] == first["raw"] and second["citations"] == ["K1"]
    assert second["timing"]["cache"] == "hit"
    assert ac.AnswerCache().stats()["hits"] == 1


def test_stream_mode_hit_yields_full_answer(env):
    with fake_stream():
        "".join(service.stream_advisor("阈值？")[0])
        iterator, meta = service.stream_advisor("阈值？")
        assert "".join(iterator) == "黄色阈值是 90% [K1]" and meta["cached"]


def test_invalidated_by_context_kb_skill_and_mode(env):
    index_dir, skills_dir = env
    with fake_stream() as stream:
        service.ask_advisor("阈值？", {"预期成本率": "95.0%"})
        service.ask_advisor("阈值？", {"预期成本率": "95.0%"})
        assert stream.call_count == 1
        service.ask_advisor("阈值？", {"预期成本率": "96.0%"})  # 系统数据变化
        assert stream.call_count == 2
        (index_dir / "manifest.json").write_text(json.dumps({"files": {"a.md": "h2"}, "embed_model": "e"}))
        service.ask_advisor("阈值？", {"预期成本率": "96.0%"})  # 知识库变化
        assert stream.call_count == 3
        (skills_dir / "s.md").write_text("# 角色\n顾问 v2")
        service.ask_advisor("阈值？", {"预期成本率": "96.0%"})  # Skill 变化
        assert stream.call_count == 4
        service.ask_advisor("阈值？", {"预期成本率": "96.0%"})
        assert stream.call_count == 4


def test_history_and_disabled_bypass_cache(env, monkeypatch):
    with fake_stream() as stream:
        service.ask_advisor("阈值？")
        service.ask_advisor("阈值？", history=[{"role": "user", "content": "上一轮"}])
        assert stream.call_count == 2
        service.ask_advisor("阈值？", use_cache=False)
        assert stream.call_count == 3
        monkeypatch.setitem(rc.CFG, "cache_enabled", False)
        service.ask_advisor("阈值？")
        assert stream.call_count == 4


def test_unclean_answers_not_cached(env):
    with fake_stream("阈值 77% [K9]") as stream:
        service.ask_advisor("阈值？")
        result = service.ask_advisor("阈值？")
    assert stream.call_count == 2 and not result["cached"]
    with patch.object(rc, "chat_stream", side_effect=OSError("offline")):
        assert "error" in service.ask_advisor("别的问题")
    assert ac.AnswerCache().stats()["entries"] == 0


def test_purge_except_current_version(tmp_path):
    cache = ac.AnswerCache(tmp_path / "c.db")
    cache.put("k1", "v1", "q", {"raw": "a"})
    cache.put("k2", "v2", "q", {"raw": "b"})
    assert cache.purge_except("v2") == 1
    assert cache.get("k1") is None and cache.get("k2") == {"raw": "b"}
    assert cache.clear() == 1


def test_rebuild_index_purges_old_entries(env, tmp_path, monkeypatch):
    stale = ac.AnswerCache()
    stale.put("old", "old-version", "q", {"raw": "x"})
    knowledge = tmp_path / "kb"
    knowledge.mkdir()
    (knowledge / "a.md").write_text("R008 规则")
    monkeypatch.setattr(rc, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(rc, "INDEX_DIR", tmp_path / "fresh_index")
    monkeypatch.setattr(service, "sync_knowledge", lambda: None)
    orig = rc.iter_chunks
    monkeypatch.setattr(rc, "iter_chunks", lambda: orig(knowledge))
    import numpy as np

    with patch.object(rc, "embed", side_effect=lambda texts, **k: np.ones((len(texts), 2), dtype=np.float32)):
        manifest = service.rebuild_index()
    assert manifest["cache_purged"] == 1 and stale.stats()["entries"] == 0


def test_prompt_is_compact_and_system_prefix_stable(env, monkeypatch):
    monkeypatch.setitem(rc.CFG, "hit_chars", 5)
    msgs_text = service._messages("问", HIT, {"a": 1}, None, False)
    msgs_json = service._messages("问", HIT, {"a": 1}, None, True)
    assert msgs_text[0] == msgs_json[0]  # system 前缀与模式无关，可复用 KV 缓存
    assert '{"a":1}' in msgs_text[-1]["content"] and "黄色阈值…\n" in msgs_text[-1]["content"]
    assert service.JSON_INSTRUCTION in msgs_json[-1]["content"]


def test_chat_options_threads_and_keep_alive(monkeypatch):
    captured = {}

    class Resp:
        def __enter__(self):
            return iter([b'{"message":{"content":"ok"},"done":true}'])

        def __exit__(self, *a):
            return False

    def post(path, body, timeout=300):
        captured.update(body)
        return Resp()

    monkeypatch.setitem(rc.CFG, "num_thread", 6)
    monkeypatch.setitem(rc.CFG, "keep_alive", "2h")
    with patch.object(rc, "_post", side_effect=post):
        list(rc.chat_stream([{"role": "user", "content": "hi"}], num_ctx=4096))
    assert captured["options"]["num_thread"] == 6 and captured["options"]["num_ctx"] == 4096
    assert captured["keep_alive"] == "2h" and captured["think"] is False


def test_warmup_reports_failure_without_raising():
    with patch.object(rc, "embed", side_effect=OSError("offline")):
        result = service.warmup()
    assert not result["ok"] and "offline" in result["error"]


def test_transient_ollama_500_retried_once(env):
    import urllib.error

    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 500, "err", None, None)
        return iter([{"message": {"content": "黄色阈值是 90% [K1]"}}])

    with patch.object(rc, "chat_stream", side_effect=flaky):
        result = service.ask_advisor("阈值？", use_cache=False)
    assert len(calls) == 2 and "error" not in result and result["timing"]["retried"]
    with patch.object(rc, "chat_stream", side_effect=ValueError("bad")) as stream:
        assert "error" in service.ask_advisor("阈值？", use_cache=False)
    assert stream.call_count == 1

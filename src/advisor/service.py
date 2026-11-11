"""本地检索、流式回答、引用及数字检查的统一入口。"""

import hashlib
import json
import re
import shutil
import time
import streamlit as st
from contextvars import ContextVar

CALL_OPTIONS = ContextVar("advisor_call_options", default={})
from pathlib import Path
import numpy as np
from src.advisor import rag as rc
from src.advisor.context import hypothesis_context


@st.cache_resource(show_spinner=False)
def get_index():
    return rc.KnowledgeIndex()


get_index.cache_clear = get_index.clear


def sync_knowledge():
    for source, target in [("docs", "项目文档"), ("config", "配置规则")]:
        dest = rc.KNOWLEDGE_DIR / target
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(rc.ROOT / source, dest)


def rebuild_index():
    if rc.CFG["local_only"]:
        rc.enforce_local_only()
    sync_knowledge()
    out = rc.INDEX_DIR
    out.mkdir(parents=True, exist_ok=True)
    chunks = list(rc.iter_chunks())
    cache = {}
    if (out / "manifest.json").exists():
        old = rc.KnowledgeIndex(out)
        cache = {c["id"]: v for c, v in zip(old.chunks, old.vecs)}
    for c in chunks:
        c["embed_text"] = f"{c['source']}｜{c['section']}\n{c['text']}"
        c["id"] = hashlib.sha256((rc.EMBED_MODEL + c["embed_text"]).encode()).hexdigest()
    todo = [c for c in chunks if c["id"] not in cache]
    if todo:
        for c, v in zip(todo, rc.embed([c["embed_text"] for c in todo], model=rc.EMBED_MODEL)):
            cache[c["id"]] = v
    mat = np.stack([cache[c["id"]] for c in chunks]) if chunks else np.empty((0, 0))
    for c in chunks:
        c.pop("embed_text")
    manifest = {
        "embed_model": rc.EMBED_MODEL,
        "chunks": len(chunks),
        "dim": mat.shape[1],
        "reembedded": len(todo),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": {
            str(p.relative_to(rc.KNOWLEDGE_DIR)): rc.file_hash(p)
            for p in rc.KNOWLEDGE_DIR.rglob("*")
            if p.is_file() and p.suffix.lower() in rc.SUPPORTED
        },
    }
    (out / "chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks))
    np.save(out / "embeddings.npy", mat)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    get_index.cache_clear()
    return manifest


def _messages(question, hits, context, history, json_mode):
    skill, _ = rc.load_skills()
    system = skill + "\n引用规则：只引用提供的 [K编号] 或 [系统]。假设分析只采用系统计算结果。"
    if json_mode:
        system += "\n返回 answer/evidence[{claim,source}]/suggestions/uncertainty/refused，严格遵循 JSON Schema。"
    knowledge = "\n".join(f"[{h['cite']}] {h['source']}｜{h['section']}\n{h['text']}" for h in hits)
    return (
        [{"role": "system", "content": system}]
        + (history or [])[-rc.CFG["history_turns"] * 2 :]
        + [
            {
                "role": "user",
                "content": f"【知识库片段】{knowledge}\n【系统计算结果】{json.dumps(context, ensure_ascii=False)}\n【问题】{question}",
            }
        ]
    )


def _finish(meta, raw, context, question, start, json_mode):
    meta["raw"] = raw
    valid = {h["cite"] for h in meta["hits"]} | {"系统"}
    cited = set(re.findall(r"\[(K\d+|系统)\]", raw))
    if json_mode:
        data = json.loads(raw)
        for key in rc.ANSWER_SCHEMA["required"]:
            if key not in data:
                raise ValueError("结构化回答缺少字段：" + key)
        if not isinstance(data["answer"], str) or not isinstance(data["refused"], bool):
            raise ValueError("结构化回答类型错误")
        for key in ["evidence", "suggestions", "uncertainty"]:
            if not isinstance(data[key], list):
                raise ValueError("结构化回答类型错误：" + key)
        if not all(isinstance(v, str) for key in ["suggestions", "uncertainty"] for v in data[key]):
            raise ValueError("建议和待核查必须为文本数组")
        if not all(
            isinstance(e, dict) and isinstance(e.get("claim"), str) and isinstance(e.get("source"), str)
            for e in data["evidence"]
        ):
            raise ValueError("证据结构错误")
        for e in data["evidence"]:
            e["source"] = e["source"].strip("[] ")
            cited.add(e["source"])
        meta["json"] = data
    meta["citations"] = sorted(cited)
    meta["invalid_citations"] = sorted(cited - valid)
    pool = question + " " + json.dumps(context, ensure_ascii=False) + " " + " ".join(h["text"] for h in meta["hits"])
    numbers = lambda s: set(re.findall(r"(?<![\w])[-+]?\d+(?:\.\d+)?", re.sub(r"K\d+", "", s)))
    meta["untraced_numbers"] = sorted(numbers(raw) - numbers(pool))
    meta["timing"]["total_s"] = round(time.perf_counter() - start, 3)


def _stream(question, context=None, *, history=None, k=None, json_mode=False):
    meta = {"raw": "", "hits": [], "citations": [], "invalid_citations": [], "untraced_numbers": [], "timing": {}}

    def generate():
        start = time.perf_counter()
        pieces = []
        ctx = hypothesis_context(question, context)
        try:
            if rc.CFG["local_only"]:
                rc.enforce_local_only()
            try:
                meta["hits"] = get_index().search(question, k=k or rc.CFG["top_k"], min_cos=rc.CFG["min_cos"])
            except FileNotFoundError:
                meta["index_warning"] = "知识库尚未构建，仅依据系统结果"
            meta["timing"]["retrieval_s"] = round(time.perf_counter() - start, 3)
            messages = _messages(question, meta["hits"], ctx, history, json_mode)
            for chunk in rc.chat_stream(
                messages,
                fmt=rc.ANSWER_SCHEMA if json_mode else None,
                temperature=rc.CFG["temperature"],
                num_ctx=rc.CFG["num_ctx"],
                num_predict=rc.CFG["num_predict"],
                model=CALL_OPTIONS.get().get("model") or rc.CHAT_MODEL,
                timeout=CALL_OPTIONS.get().get("timeout", 90),
            ):
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    if not pieces:
                        meta["timing"]["ttft_s"] = round(time.perf_counter() - start, 3)
                    pieces.append(piece)
                    yield piece
            _finish(meta, "".join(pieces), ctx, question, start, json_mode)
        except Exception as exc:
            meta["raw"] = "".join(pieces)
            meta["error"] = f"本地模型调用失败：{exc}"
            meta["timing"]["total_s"] = round(time.perf_counter() - start, 3)
            if not json_mode:
                yield meta["error"]

    return generate(), meta


def stream_advisor(question, context=None, *, history=None):
    return _stream(question, context, history=history)


def ask_advisor(question, context=None, *, json_mode=False, history=None, k=None):
    iterator, meta = _stream(question, context, history=history, k=k, json_mode=json_mode)
    for _ in iterator:
        pass
    return meta


def advisor_health():
    result = {"online": False, "chat_model": False, "embed_model": False, "stale": True, "manifest": {}}
    try:
        if rc.CFG["local_only"]:
            rc.enforce_local_only()
        from urllib.parse import urlparse

        if rc.CFG["local_only"] and urlparse(rc.OLLAMA_BASE).hostname not in ("127.0.0.1", "::1", "localhost"):
            raise ConnectionError("Ollama 必须在本机")
        with rc._OPENER.open(rc.OLLAMA_BASE + "/api/tags", timeout=2) as response:
            names = {m["name"] for m in json.load(response)["models"]}
        result.update(online=True, chat_model=rc.CHAT_MODEL in names, embed_model=rc.EMBED_MODEL in names)
    except Exception as exc:
        result["error"] = str(exc)
    try:
        manifest = json.loads((rc.INDEX_DIR / "manifest.json").read_text())
        result["manifest"] = manifest
        current = {
            str(p.relative_to(rc.KNOWLEDGE_DIR)): rc.file_hash(p)
            for p in rc.KNOWLEDGE_DIR.rglob("*")
            if p.is_file() and p.suffix.lower() in rc.SUPPORTED
        }
        stale = current != manifest["files"] or manifest["embed_model"] != rc.EMBED_MODEL
        for source, target in [("docs", "项目文档"), ("config", "配置规则")]:
            expected = {
                f"{target}/{p.relative_to(rc.ROOT / source)}": rc.file_hash(p)
                for p in (rc.ROOT / source).rglob("*")
                if p.is_file() and p.suffix.lower() in rc.SUPPORTED
            }
            stale = (
                stale
                or any(manifest["files"].get(k) != v for k, v in expected.items())
                or set(k for k in manifest["files"] if k.startswith(target + "/")) != set(expected)
            )
        result["stale"] = stale
    except (OSError, KeyError, ValueError):
        pass
    return result

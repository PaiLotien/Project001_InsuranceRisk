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
from src.advisor.cache import AnswerCache, kb_version, make_key, skill_version
from src.advisor.context import hypothesis_context
from src.advisor.lookup import CITE as LOOKUP_CITE, lookup_facts, render_facts

LOOKUP_INSTRUCTION = (
    "【系统查表】以下数值由系统从结构化数据精确查出。涉及这些指标时必须逐字引用对应数值并标注 [系统查表]，"
    "不得改写、换算或推算，也不得用知识库片段里其他年份、地区的数值代替；结果为“未查到”时直接说明数据中没有，不要估算。"
)

JSON_INSTRUCTION = (
    "返回 answer/evidence[{claim,source}]/suggestions/uncertainty/refused，严格遵循 JSON Schema。"
    "answer 一句话结论；evidence 最多 3 条，source 只填 K编号（如 K2）或 系统；suggestions 最多 3 条；"
    "uncertainty 最多 2 条；每条一句话。"
)


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
    try:
        manifest["cache_purged"] = AnswerCache().purge_except(_cache_version())
    except Exception:  # 缓存清理失败不影响索引
        manifest["cache_purged"] = 0
    return manifest


def _compact(context) -> str:
    return json.dumps(context, ensure_ascii=False, separators=(",", ":")) if context else "无"


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n", text)).strip()
    return text if not limit or len(text) <= limit else text[:limit].rstrip() + "…"


def _system_prompt() -> str:
    """system 消息保持固定（与问题、模式无关），让 Ollama 复用前缀 KV 缓存。"""
    skill, _ = rc.load_skills()
    return skill + "\n引用规则：只引用提供的 [K编号] 或 [系统]。假设分析只采用系统计算结果。"


def _messages(question, hits, context, history, json_mode, facts=None):
    limit = int(rc.CFG.get("hit_chars", 0) or 0)
    knowledge = "\n".join(f"[{h['cite']}] {h['source']}｜{h['section']}\n{_clip(h['text'], limit)}" for h in hits)
    tail = ("\n" + JSON_INSTRUCTION) if json_mode else ""
    lookup = f"\n{LOOKUP_INSTRUCTION}\n{render_facts(facts)}" if facts else ""
    return (
        [{"role": "system", "content": _system_prompt()}]
        + (history or [])[-rc.CFG["history_turns"] * 2 :]
        + [
            {
                "role": "user",
                "content": f"【知识库片段】\n{knowledge}\n【系统计算结果】{_compact(context)}{lookup}\n【问题】{question}{tail}",
            }
        ]
    )


def _nums(text: str) -> set[float]:
    text = re.sub(r"\[[^\]]*\]|K\d+|R\d{3}|C\d{3}|DQ\d+", " ", str(text))
    # 版本 / 批次标签不是数值（如“11.1 修复”“11.1反馈”“2020 版”“第3批”）
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:版|反馈|修复|批)|第\s*\d+\s*批", " ", text)
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return {round(float(n), 4) for n in re.findall(r"(?<![\d.])\d+(?:\.\d+)?", text)}


def lookup_mismatch(raw, facts, question, context) -> list[str]:
    """查表命中时，回答里的数字必须来自查表结果、系统结果或问题本身；返回不符的数字。

    另外检查查表主值是否被引用：只有一条带数值的事实且回答没有出现该数值时，记为“未引用查表值”。
    """
    hits = [f for f in facts or [] if not f["value"].startswith(("未查到", "官方表未列示"))]
    if not hits:
        return []
    allowed = _nums(render_facts(facts)) | _nums(question) | _nums(json.dumps(context or {}, ensure_ascii=False))
    answer = _nums(raw)
    ignore = {float(i) for i in range(0, 13)} | {float(y) for y in range(2000, 2031)}  # 序号、月份、年份不计
    bad = sorted(answer - allowed - ignore)
    out = [f"{n:g}" for n in bad]
    # 主值：只有一条事实、且其值在括号说明之前恰好一个数时才检查（如“9409 亿元”）
    plain = lambda v: re.sub(r"（[^）]*）", "", v)
    numeric = [f for f in hits if len(_nums(plain(f["value"]))) == 1]
    if len(numeric) == 1 and len(hits) <= 2:
        primary = next(iter(_nums(plain(numeric[0]["value"]))))
        if primary not in answer:
            out.append(f"未引用查表值 {primary:g}")
    return out


def _cache_version() -> str:
    return make_key("", None, kb=kb_version(rc.INDEX_DIR), skill=skill_version(rc.SKILLS_DIR),
                    model=rc.CHAT_MODEL, json_mode=False)[1]


def _cache_key(question, context, json_mode, k, facts=None):
    model = CALL_OPTIONS.get().get("model") or rc.CHAT_MODEL
    extra = {"k": k or rc.CFG["top_k"], "hit_chars": rc.CFG.get("hit_chars"), "num_predict": rc.CFG["num_predict"],
             "lookup": facts or [], "source_weights": rc.CFG.get("source_weights") or {},
             "max_per_source": rc.CFG.get("max_per_source")}
    return make_key(question, context, kb=kb_version(rc.INDEX_DIR), skill=skill_version(rc.SKILLS_DIR),
                    model=model, json_mode=json_mode, extra=extra)


def _transient(exc) -> bool:
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, (ConnectionError, urllib.error.URLError, TimeoutError))


def friendly_error(exc) -> str:
    """把底层异常翻译成运维人员能直接照做的中文提示（保留原始信息便于排查）。"""
    import socket as _socket
    import urllib.error

    detail = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return f"本地模型调用失败：本机未安装模型 {rc.CHAT_MODEL}（或向量模型 {rc.EMBED_MODEL}），请先用 ollama pull / ollama create 安装。（{detail}）"
        if exc.code >= 500:
            return f"本地模型调用失败：本机模型服务内部错误，已自动重试一次仍失败，请稍后再试或重启 ollama serve。（{detail}）"
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (TimeoutError, _socket.timeout)) or isinstance(exc, (TimeoutError, _socket.timeout)):
        return f"本地模型调用失败：响应超时，CPU 推理较慢或模型正在加载，请稍后重试。（{detail}）"
    if isinstance(reason, ConnectionRefusedError) or "Connection refused" in detail:
        return f"本地模型调用失败：本机模型服务未启动（{rc.OLLAMA_BASE}），请先运行 ollama serve。（{detail}）"
    if "离线保护" in detail:
        return f"本地模型调用失败：{detail}"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return f"本地模型调用失败：模型输出格式不符合要求，请重试。（{detail}）"
    return f"本地模型调用失败：{detail}"


def _model_names(models) -> set[str]:
    """Ollama 列表里的名称带 :latest 标签，配置里通常不带，两种写法都算已安装。"""
    names = set()
    for m in models:
        for key in ("name", "model"):
            if m.get(key):
                names.add(m[key])
                names.add(m[key].removesuffix(":latest"))
    return names


CACHED_FIELDS = ("raw", "hits", "citations", "invalid_citations", "untraced_numbers", "json", "lookup", "lookup_mismatch")


def _finish(meta, raw, context, question, start, json_mode):
    meta["raw"] = raw
    facts = meta.get("lookup") or []
    valid = {h["cite"] for h in meta["hits"]} | {"系统"} | ({LOOKUP_CITE} if facts else set())
    cited = set(re.findall(r"\[(K\d+|系统查表|系统)\]", raw))
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
    pool += " " + render_facts(facts)
    meta["untraced_numbers"] = sorted(numbers(raw) - numbers(pool))
    meta["lookup_mismatch"] = lookup_mismatch(raw, facts, question, context)
    meta["timing"]["total_s"] = round(time.perf_counter() - start, 3)


def _stream(question, context=None, *, history=None, k=None, json_mode=False, use_cache=None, frame=None):
    meta = {"raw": "", "hits": [], "citations": [], "invalid_citations": [], "untraced_numbers": [], "timing": {},
            "cached": False, "lookup": [], "lookup_mismatch": []}
    if use_cache is None:
        use_cache = bool(rc.CFG.get("cache_enabled", True)) and not history

    def generate():
        start = time.perf_counter()
        pieces = []
        ctx = hypothesis_context(question, context)
        cache = key = version = None
        try:
            if rc.CFG["local_only"]:
                rc.enforce_local_only()
            if rc.CFG.get("lookup_enabled", True):
                found = lookup_facts(question, ctx, frame)
                meta["lookup"], meta["lookup_no_hit"] = found["facts"], found["no_hit"]
            if use_cache:
                try:
                    cache = AnswerCache()
                    key, version = _cache_key(question, ctx, json_mode, k, meta["lookup"])
                    hit = cache.get(key)
                except Exception:
                    cache, hit = None, None
                if hit is not None:
                    meta.update({f: hit[f] for f in CACHED_FIELDS if f in hit})
                    meta["cached"] = True
                    meta["timing"] = {"ttft_s": round(time.perf_counter() - start, 3), "cache": "hit"}
                    meta["timing"]["total_s"] = meta["timing"]["ttft_s"]
                    yield meta["raw"]
                    return
            try:
                meta["hits"] = get_index().search(question, k=k or rc.CFG["top_k"], min_cos=rc.CFG["min_cos"])
            except FileNotFoundError:
                meta["index_warning"] = "知识库尚未构建，仅依据系统结果"
            meta["timing"]["retrieval_s"] = round(time.perf_counter() - start, 3)
            messages = _messages(question, meta["hits"], ctx, history, json_mode, meta["lookup"])
            call = dict(
                fmt=rc.ANSWER_SCHEMA if json_mode else None,
                temperature=rc.CFG["temperature"],
                num_ctx=rc.CFG["num_ctx"],
                num_predict=rc.CFG["num_predict"],
                model=CALL_OPTIONS.get().get("model") or rc.CHAT_MODEL,
                timeout=CALL_OPTIONS.get().get("timeout", 90),
            )
            for attempt in range(2):
                try:
                    for chunk in rc.chat_stream(messages, **call):
                        piece = chunk.get("message", {}).get("content", "")
                        if piece:
                            if not pieces:
                                meta["timing"]["ttft_s"] = round(time.perf_counter() - start, 3)
                            pieces.append(piece)
                            yield piece
                        if chunk.get("done"):
                            for src, dst in [("prompt_eval_count", "prompt_tokens"), ("eval_count", "output_tokens")]:
                                if src in chunk:
                                    meta["timing"][dst] = chunk[src]
                    break
                except Exception as exc:
                    # 本机 Ollama 偶发 500（推理进程重启）且尚未输出任何内容时，重试一次
                    if attempt or pieces or not _transient(exc):
                        raise
                    meta["timing"]["retried"] = True
            _finish(meta, "".join(pieces), ctx, question, start, json_mode)
            if (cache is not None and key and not meta["invalid_citations"] and not meta["untraced_numbers"]
                    and not meta["lookup_mismatch"]):
                try:
                    cache.put(key, version, question, {f: meta[f] for f in CACHED_FIELDS if f in meta})
                except Exception:
                    pass
        except Exception as exc:
            meta["raw"] = "".join(pieces)
            meta["error"] = friendly_error(exc)
            meta["timing"]["total_s"] = round(time.perf_counter() - start, 3)
            if not json_mode:
                yield meta["error"]

    return generate(), meta


def stream_advisor(question, context=None, *, history=None, frame=None):
    return _stream(question, context, history=history, frame=frame)


def ask_advisor(question, context=None, *, json_mode=False, history=None, k=None, use_cache=None, frame=None):
    iterator, meta = _stream(question, context, history=history, k=k, json_mode=json_mode, use_cache=use_cache,
                             frame=frame)
    for _ in iterator:
        pass
    return meta


def warmup(timeout: int = 180) -> dict:
    """预热：把对话模型和向量模型载入内存，并用固定 system 前缀预填充 KV 缓存。

    只连接本机 Ollama；失败不抛出（看板照常可用）。
    """
    start = time.perf_counter()
    result = {"ok": False}
    try:
        if rc.CFG["local_only"]:
            rc.enforce_local_only()
        rc.embed(["预热"], model=rc.EMBED_MODEL)
        messages = [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": "你好"}]
        for _ in rc.chat_stream(messages, num_predict=1, num_ctx=rc.CFG["num_ctx"], timeout=timeout,
                                temperature=rc.CFG["temperature"]):
            pass
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    result["seconds"] = round(time.perf_counter() - start, 2)
    return result


@st.cache_resource(show_spinner=False)
def start_warmup():
    """看板启动时在后台线程预热一次（每个进程一次）。"""
    import threading

    import os

    if not rc.CFG.get("warmup_on_start", True) or "PYTEST_CURRENT_TEST" in os.environ:
        return None
    holder: dict = {}
    thread = threading.Thread(target=lambda: holder.update(warmup()), daemon=True, name="irds-advisor-warmup")
    thread.start()
    return holder


def cache_stats() -> dict:
    try:
        return AnswerCache().stats()
    except Exception:
        return {"entries": 0, "hits": 0}


def clear_cache() -> int:
    return AnswerCache().clear()


def advisor_health():
    result = {"online": False, "chat_model": False, "embed_model": False, "stale": True, "manifest": {}}
    try:
        if rc.CFG["local_only"]:
            rc.enforce_local_only()
        from urllib.parse import urlparse

        if rc.CFG["local_only"] and urlparse(rc.OLLAMA_BASE).hostname not in ("127.0.0.1", "::1", "localhost"):
            raise ConnectionError("Ollama 必须在本机")
        with rc._OPENER.open(rc.OLLAMA_BASE + "/api/tags", timeout=2) as response:
            names = _model_names(json.load(response)["models"])
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

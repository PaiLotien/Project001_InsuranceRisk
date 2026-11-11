"""IRDS 本地 AI 顾问核心：知识库加载/切块、本地向量化、混合检索、Skill 加载、本地 Qwen 调用。

全部依赖：Python 标准库 + numpy + openpyxl（xlsx）+ python-docx（docx，可选）+ pdftotext（pdf，可选）。
所有网络请求只发往本机 Ollama（127.0.0.1），并显式绕过代理。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from src.advisor.settings import settings

CFG = settings()
ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = Path(CFG["knowledge_dir"])
INDEX_DIR = Path(CFG["index_dir"])
SKILLS_DIR = Path(CFG["skills_dir"])
OLLAMA_BASE = CFG["ollama_base"]
CHAT_MODEL = CFG["chat_model"]
EMBED_MODEL = CFG["embed_model"]
CHUNK_CHARS = CFG["chunk_chars"]
CHUNK_OVERLAP = CFG["chunk_overlap"]
SUPPORTED = {".md", ".txt", ".yaml", ".yml", ".xlsx", ".docx", ".pdf"}

# ---------------------------------------------------------------- 离线保护

_AUDIT: list[str] = []


def enforce_local_only() -> list[str]:
    """运行时禁止任何非回环地址的网络连接；返回审计列表（记录所有连接目标）。"""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    if getattr(socket.socket.connect, "_irds_guard", False):
        return _AUDIT
    orig = socket.socket.connect

    def guarded(self, address):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else str(address)
        _AUDIT.append(str(host))
        if isinstance(address, tuple) and host not in ("127.0.0.1", "::1", "localhost"):
            raise ConnectionError(f"IRDS 离线保护：拒绝连接外部地址 {host}")
        return orig(self, address)

    guarded._irds_guard = True  # type: ignore[attr-defined]
    socket.socket.connect = guarded  # type: ignore[method-assign]
    return _AUDIT


# ---------------------------------------------------------------- Ollama

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(path: str, body: dict, timeout: int = 300):
    if CFG["local_only"]:
        enforce_local_only()
        from urllib.parse import urlparse

        if urlparse(OLLAMA_BASE).hostname not in ("127.0.0.1", "::1", "localhost"):
            raise ConnectionError("IRDS 离线保护：Ollama 必须在本机")
    req = urllib.request.Request(
        OLLAMA_BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return _OPENER.open(req, timeout=timeout)


def embed(texts: list[str], model: str = EMBED_MODEL, batch: int = 16) -> np.ndarray:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        body = {"model": model, "input": texts[i : i + batch], "truncate": True, "keep_alive": CFG.get("keep_alive", "30m")}
        with _post("/api/embed", body) as r:
            out.extend(json.load(r)["embeddings"])
    arr = np.asarray(out, dtype=np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
    return arr


def chat_stream(
    messages: list[dict],
    *,
    model: str = CHAT_MODEL,
    fmt: Any = None,
    temperature: float = 0.1,
    num_predict: int = 600,
    num_ctx: int = 8192,
    timeout: int = 600,
    num_thread: int | None = None,
) -> Iterator[dict]:
    """流式调用 /api/chat，逐块 yield Ollama 返回的 JSON 行。fmt 可为 JSON Schema。"""
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "think": False,
        "messages": messages,
        "keep_alive": CFG.get("keep_alive", "30m"),
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
    }
    threads = num_thread if num_thread is not None else CFG.get("num_thread") or os.cpu_count()
    if threads:
        body["options"]["num_thread"] = int(threads)
    if fmt is not None:
        body["format"] = fmt
    with _post("/api/chat", body, timeout=timeout) as r:
        for line in r:
            if line.strip():
                yield json.loads(line)


# ---------------------------------------------------------------- 文档加载与切块


def _md_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题切节，返回 (标题路径, 正文)。"""
    sections, path, buf = [], [], []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(path), body))

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            buf = []
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return sections or [("", text)]


def _split(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= size:
        return [text] if text else []
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):  # 尽量在段落/句号处断开
            cut = max(text.rfind("\n", start + size // 2, end), text.rfind("。", start + size // 2, end))
            if cut > start:
                end = cut + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in parts if p]


def _xlsx_records(path: Path) -> list[tuple[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [r for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
        if not rows:
            continue
        # 表头 = 第一行里非空单元格最多的前 3 行之一
        hi = max(range(min(3, len(rows))), key=lambda i: sum(c not in (None, "") for c in rows[i]))
        header = [str(c).strip() if c not in (None, "") else f"列{j + 1}" for j, c in enumerate(rows[hi])]
        pre = " ".join(str(c) for r in rows[:hi] for c in r if c not in (None, ""))
        group: list[str] = []
        for r in rows[hi + 1 :]:
            cells = [
                f"{header[j] if j < len(header) else f'列{j + 1}'}：{str(c).strip()}"
                for j, c in enumerate(r)
                if c not in (None, "")
            ]
            if cells:
                group.append("；".join(cells))
        # 每行一块，保持规则完整并保留表名和表头上下文
        for i in range(0, len(group), 1):
            out.append((f"{ws.title}{(' / ' + pre) if pre else ''}", "\n".join(group[i : i + 1])))
    return out


def load_document(path: Path) -> list[tuple[str, str]]:
    """返回 [(节标题, 文本)]。"""
    suf = path.suffix.lower()
    if suf in (".md", ".txt"):
        return _md_sections(path.read_text(encoding="utf-8", errors="ignore"))
    if suf in (".yaml", ".yml"):
        return [("", path.read_text(encoding="utf-8", errors="ignore"))]
    if suf == ".xlsx":
        return _xlsx_records(path)
    if suf == ".docx":
        import docx

        d = docx.Document(str(path))
        lines = []
        for p in d.paragraphs:
            t = p.text.strip()
            if not t:
                continue
            if p.style is not None and p.style.name.lower().startswith(("heading", "标题")):
                lvl = re.findall(r"\d", p.style.name)
                t = "#" * int(lvl[0] if lvl else 1) + " " + t
            lines.append(t)
        for tbl in d.tables:
            for row in tbl.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        return _md_sections("\n".join(lines))
    if suf == ".pdf":
        txt = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout
        return [(f"第{i + 1}页", pg) for i, pg in enumerate(txt.split("\f")) if pg.strip()]
    return []


def iter_chunks(kb_dir: Path = KNOWLEDGE_DIR) -> Iterator[dict]:
    for path in sorted(kb_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED or path.name.startswith(("~$", ".")):
            continue
        rel = path.relative_to(kb_dir).as_posix()
        for section, text in load_document(path):
            for k, piece in enumerate(_split(text) if path.suffix.lower() != ".xlsx" else [text]):
                yield {"source": rel, "section": section, "part": k, "text": piece}


# ---------------------------------------------------------------- 词法检索（中文字二元组 BM25）


def _tokens(text: str) -> list[str]:
    text = text.lower()
    toks = re.findall(r"[a-z0-9_.%]+", text)
    han = re.findall(r"[\u4e00-\u9fff]", text)
    toks += [a + b for a, b in zip(han, han[1:])]
    return toks


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.tf = [Counter(_tokens(d)) for d in docs]
        self.len = np.array([sum(t.values()) for t in self.tf], dtype=np.float32)
        self.avg = float(self.len.mean()) if len(docs) else 1.0
        df = Counter(w for t in self.tf for w in t)
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query: str) -> np.ndarray:
        q = set(_tokens(query))
        s = np.zeros(len(self.tf), dtype=np.float32)
        for i, tf in enumerate(self.tf):
            norm = self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
            s[i] = sum(self.idf[w] * tf[w] * (self.k1 + 1) / (tf[w] + norm) for w in q if w in tf)
        return s


# ---------------------------------------------------------------- 索引


class KnowledgeIndex:
    def __init__(self, index_dir: Path = INDEX_DIR):
        self.dir = index_dir
        self.chunks = [json.loads(l) for l in (index_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.vecs = np.load(index_dir / "embeddings.npy")
        self.meta = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
        self.bm25 = BM25([c["section"] + " " + c["text"] for c in self.chunks])

    def search(self, query: str, k: int = 5, *, alpha_rrf: int = 60, min_cos: float = 0.35) -> list[dict]:
        qv = embed([query], model=self.meta["embed_model"])[0]
        cos = self.vecs @ qv
        lex = self.bm25.scores(query)
        r_dense = np.argsort(-cos)
        r_lex = np.argsort(-lex)
        fused: dict[int, float] = {}
        for rank, i in enumerate(r_dense[:50]):
            fused[int(i)] = fused.get(int(i), 0) + 1 / (alpha_rrf + rank)
        for rank, i in enumerate(r_lex[:50]):
            if lex[i] > 0:
                fused[int(i)] = fused.get(int(i), 0) + 1 / (alpha_rrf + rank)
        order = sorted(fused, key=lambda i: -fused[i])
        hits = []
        for i in order:
            if cos[i] < min_cos and lex[i] <= 0:
                continue
            c = dict(self.chunks[i])
            c.update(score=round(fused[i], 4), cos=round(float(cos[i]), 3))
            hits.append(c)
            if len(hits) >= k:
                break
        for n, h in enumerate(hits, 1):
            h["cite"] = f"K{n}"
        return hits


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------- Skill


def load_skills(skills_dir: Path = SKILLS_DIR) -> tuple[str, list[dict]]:
    """读取 skills/*.md：可选 YAML 风格 front-matter（name/version/enabled/priority），正文直接拼进 system prompt。"""
    blocks, metas = [], []
    for p in sorted(skills_dir.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        meta: dict[str, str] = {"name": p.stem, "enabled": "true", "priority": "50"}
        m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            raw = raw[m.end() :]
        if meta.get("enabled", "true").lower() in ("false", "0", "no"):
            continue
        meta["file"] = p.name
        metas.append(meta)
        blocks.append((int(meta.get("priority", 50)), raw.strip()))
    blocks.sort(key=lambda x: x[0])
    return "\n\n".join(b for _, b in blocks), metas


# ---------------------------------------------------------------- 结构化输出 Schema

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"claim": {"type": "string"}, "source": {"type": "string"}},
                "required": ["claim", "source"],
            },
            "maxItems": 3,
        },
        # 条数上限由 Ollama 语法约束强制执行，限制输出长度（CPU 上生成是主要耗时）
        "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "uncertainty": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "refused": {"type": "boolean"},
    },
    "required": ["answer", "evidence", "suggestions", "uncertainty", "refused"],
}

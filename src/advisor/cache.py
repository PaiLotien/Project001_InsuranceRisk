"""本地答案缓存：问题（规范化）+ 系统数据指纹 + 知识库/Skill/模型版本 → 已校验的回答。

只存本机 SQLite（data/runtime/advisor_cache.db），不外发。任何一项版本变化都会让键改变；
重建索引时还会主动清理旧版本条目。多轮对话（带历史）不走缓存。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data/runtime/advisor_cache.db"
_PUNCT = re.compile(r"[\s\u3000，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]<>《》…—-]+")


def normalize_question(question: str) -> str:
    text = unicodedata.normalize("NFKC", str(question or "")).lower()
    return _PUNCT.sub("", text)


def _digest(obj) -> str:
    raw = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def data_fingerprint(context) -> str:
    """系统计算结果（预警行、阈值、数据来源）的指纹；数据或阈值一变即失效。"""
    return _digest(context or {})[:16]


def kb_version(index_dir: Path) -> str:
    """索引版本 = manifest 中各文件哈希 + 向量模型；索引不存在时返回 none。"""
    try:
        manifest = json.loads((Path(index_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    return _digest({"files": manifest.get("files", {}), "embed": manifest.get("embed_model")})[:16]


def skill_version(skills_dir: Path) -> str:
    files = {p.name: p.read_bytes().hex() for p in sorted(Path(skills_dir).glob("*.md"))}
    return _digest(files)[:16]


def make_key(question, context, *, kb: str, skill: str, model: str, json_mode: bool, extra=None) -> tuple[str, str]:
    """返回 (缓存键, 版本标签)。版本标签用于批量清理旧条目。"""
    version = f"{kb}:{skill}:{model}"
    key = _digest([normalize_question(question), data_fingerprint(context), version, bool(json_mode), extra or {}])
    return key, version


class AnswerCache:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or DB_PATH)

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS advisor_cache (key TEXT PRIMARY KEY, version TEXT NOT NULL, "
            "question TEXT, payload TEXT NOT NULL, created REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0)"
        )
        return db

    def get(self, key: str):
        if not self.path.exists():
            return None
        with self._db() as db:
            row = db.execute("SELECT payload FROM advisor_cache WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE advisor_cache SET hits = hits + 1 WHERE key=?", (key,))
        return json.loads(row[0])

    def put(self, key: str, version: str, question: str, payload: dict) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO advisor_cache (key, version, question, payload, created, hits) VALUES (?,?,?,?,?,0)",
                (key, version, question, json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def purge_except(self, version: str) -> int:
        """删除不属于当前版本的条目（知识库、Skill 或模型变化后调用）。"""
        if not self.path.exists():
            return 0
        with self._db() as db:
            return db.execute("DELETE FROM advisor_cache WHERE version != ?", (version,)).rowcount

    def clear(self) -> int:
        if not self.path.exists():
            return 0
        with self._db() as db:
            return db.execute("DELETE FROM advisor_cache").rowcount

    def stats(self) -> dict:
        if not self.path.exists():
            return {"entries": 0, "hits": 0}
        with self._db() as db:
            n, hits = db.execute("SELECT COUNT(*), COALESCE(SUM(hits),0) FROM advisor_cache").fetchone()
        return {"entries": n, "hits": hits}

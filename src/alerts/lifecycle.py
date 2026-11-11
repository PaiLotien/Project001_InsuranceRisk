"""SQLite 预警处置状态机，所有时刻统一使用 UTC Unix 秒。"""

import json
import math
import sqlite3
import time
import uuid
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data/runtime/irds.db"
STATES = ["新建", "已确认", "处置中", "已关闭"]


class AlertLifecycle:
    def __init__(self, path=DEFAULT_DB):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS lifecycle (id TEXT PRIMARY KEY,status TEXT,owner TEXT,deadline REAL,escalated INTEGER,records TEXT)"
            )

            columns = {r[1] for r in db.execute("PRAGMA table_info(lifecycle)")}
            if "alert_ref" not in columns:
                db.execute("ALTER TABLE lifecycle ADD COLUMN alert_ref TEXT NOT NULL DEFAULT ''")

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def create(self, owner, deadline, *, now=None, alert_ref=""):
        if not owner or not owner.strip():
            raise ValueError("责任人必填")
        if not math.isfinite(float(deadline)):
            raise ValueError("处置时限必须为有限时间")
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO lifecycle (id,status,owner,deadline,escalated,records,alert_ref) VALUES (?,?,?,?,?,?,?)",
                (
                    ident,
                    STATES[0],
                    owner,
                    float(deadline),
                    0,
                    json.dumps(
                        [{"status": STATES[0], "at": now if now is not None else time.time(), "note": "创建"}],
                        ensure_ascii=False,
                    ),
                    alert_ref,
                ),
            )
        return ident

    def get(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT * FROM lifecycle WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        result = dict(row)
        result["records"] = json.loads(result["records"])
        return result

    def transition(self, ident, status, note, *, now=None):
        if not note.strip():
            raise ValueError("处置记录必填")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lifecycle WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            if row["status"] == "已关闭" or status != STATES[STATES.index(row["status"]) + 1]:
                raise ValueError("非法状态流转")
            records = json.loads(row["records"])
            records.append(
                {"status": status, "at": now if now is not None else time.time(), "note": note, "owner": row["owner"]}
            )
            db.execute(
                "UPDATE lifecycle SET status=?,records=? WHERE id=?",
                (status, json.dumps(records, ensure_ascii=False), ident),
            )
        return self.get(ident)

    def escalate_overdue(self, *, now=None):
        timestamp = now if now is not None else time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM lifecycle WHERE status!='已关闭' AND deadline<? AND escalated=0", (timestamp,)
            ).fetchall()
            for row in rows:
                records = json.loads(row["records"])
                records.append({"status": row["status"], "at": timestamp, "note": "超时升级", "owner": row["owner"]})
                db.execute(
                    "UPDATE lifecycle SET escalated=1,records=? WHERE id=?",
                    (json.dumps(records, ensure_ascii=False), row["id"]),
                )
            return len(rows)

    def list_cases(self):
        """按截止时间返回全部处置单，供处置跟踪页展示。"""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM lifecycle ORDER BY deadline").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["records"] = json.loads(item["records"])
            result.append(item)
        return result

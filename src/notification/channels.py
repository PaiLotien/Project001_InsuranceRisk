"""Apprise 渠道路由；默认只记录本地 outbox，不创建网络客户端。"""

import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import apprise
import yaml

ROOT = Path(__file__).resolve().parents[2]
LEVELS = {"yellow": 1, "orange": 2, "red": 3}


def dispatch(alerts, *, config=None, runtime=None, now=None):
    config = config or yaml.safe_load((ROOT / "config/notification_rules.yaml").read_text())["delivery"]
    runtime = Path(runtime or ROOT / "data/runtime")
    runtime.mkdir(parents=True, exist_ok=True)
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    groups = {}
    for alert in alerts:
        key = tuple(alert[field] for field in config.get("group_by", ["branch", "batch"]))
        groups.setdefault(key, []).append(alert)
    results = []
    with sqlite3.connect(runtime / "irds.db") as db:
        db.execute("CREATE TABLE IF NOT EXISTS deliveries (fingerprint TEXT PRIMARY KEY, sent REAL)")
        db.execute("BEGIN IMMEDIATE")
        for key, items in groups.items():
            inhibit = config.get(
                "inhibit", {"source": "red", "targets": ["yellow", "orange"], "equal": ["branch", "batch", "object"]}
            )
            equal = inhibit.get("equal", ["branch", "batch", "object"])
            inhibited = {tuple(i[field] for field in equal) for i in items if i["level"] == inhibit["source"]}
            items = [
                i
                for i in items
                if i["level"] not in inhibit["targets"] or tuple(i[field] for field in equal) not in inhibited
            ]
            if not items:
                continue
            items = sorted(items, key=lambda i: json.dumps(i, sort_keys=True, ensure_ascii=False))
            level = max((i["level"] for i in items), key=LEVELS.get)
            destinations = config["routes"].get(level, [])
            signature = {
                "alerts": items,
                "routes": destinations,
                "dry_run": config.get("dry_run", True),
                "channels": [config["channels"][c]["url"] for c in destinations],
            }
            fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            previous = db.execute("SELECT sent FROM deliveries WHERE fingerprint=?", (fingerprint,)).fetchone()
            if previous and now - previous[0] < config["repeat_interval"]:
                continue
            payload = {
                "branch": items[0]["branch"],
                "batch": items[0]["batch"],
                "level": level,
                "alerts": items,
                "channels": config["routes"].get(level, []),
                "dry_run": config.get("dry_run", True),
            }
            if not payload["dry_run"]:
                client = apprise.Apprise()
                for channel in payload["channels"]:
                    url = config["channels"][channel]["url"]
                    if not client.add(url):
                        raise ValueError("无效渠道配置：" + channel)
                if not client.notify(
                    title=f"IRDS {items[0]['branch']} {level}", body=json.dumps(items, ensure_ascii=False)
                ):
                    raise RuntimeError("渠道发送失败，未标记已发送")
            with (runtime / "outbox.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,?)", (fingerprint, now))
            results.append(payload)
    return results

"""本地 SQLite 阈值设置。"""
import sqlite3
from pathlib import Path
DB_PATH = Path(__file__).resolve().parents[2] / "data/runtime/irds.db"
def load_user_thresholds(path=None):
    path = Path(path or DB_PATH)
    if not path.exists(): return {}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS user_thresholds (level TEXT PRIMARY KEY, value REAL NOT NULL)")
        return dict(db.execute("SELECT level, value FROM user_thresholds"))
def save_user_thresholds(values, path=None):
    if not 0 <= values['yellow'] < values['orange'] < values['red']: raise ValueError("阈值须满足黄色＜橙色＜红色")
    path = Path(path or DB_PATH); path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS user_thresholds (level TEXT PRIMARY KEY, value REAL NOT NULL)")
        db.executemany("INSERT OR REPLACE INTO user_thresholds VALUES (?, ?)", values.items())
def reset_thresholds(path=None):
    path = Path(path or DB_PATH)
    if path.exists():
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS user_thresholds (level TEXT PRIMARY KEY, value REAL NOT NULL)")
            db.execute("DELETE FROM user_thresholds")

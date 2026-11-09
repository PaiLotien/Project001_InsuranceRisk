"""历史快照、批量导入和趋势；演示历史与用户文件分开保存。"""

import re
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from src.alerts.thresholds import apply_alert_rules
from src.parser.data_source import unique_records

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_PATH = ROOT / ".irds/history/snapshots.csv"
METRICS = ("预期成本率", "预期赔付率", "成本率1", "单均保费_元", "累计出险频度", "保费占比", "签单保费_万元")
SNAPSHOT_COLUMNS = ["日期", "机构", "车型", "期间", "能源类型", *METRICS, "预警等级", "来源"]
KEYS = ["日期", "机构", "车型", "期间", "能源类型"]


def build_snapshot(df, source_label="", snapshot_date=None):
    if df.empty:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    out = apply_alert_rules(df, df.attrs.get("yellow_threshold"))
    if "车型" in out:
        out = out[out["车型"].ne("合计")].copy()
    if "表类型" in out:
        out = out[out["表类型"].eq("主表")].copy()
    snapshot = pd.DataFrame(index=out.index)
    if snapshot_date is not None:
        dates = pd.Series(snapshot_date, index=out.index)
    elif "统计日期" in out:
        dates = out["统计日期"]
    elif "日期" in out:
        dates = out["日期"]
    else:
        dates = pd.Series(date.today(), index=out.index)
    dates = pd.to_datetime(dates, errors="coerce")
    if dates.isna().any():
        raise ValueError("快照日期缺失或格式无效，请修正后再保存")
    snapshot["日期"] = dates.dt.strftime("%Y-%m-%d")
    for column, default in (("机构", "全辖"), ("车型", "未分类"), ("期间", "当日"), ("能源类型", "")):
        snapshot[column] = out[column].fillna(default).astype(str) if column in out else default
    for column in METRICS:
        snapshot[column] = pd.to_numeric(out[column], errors="coerce") if column in out else float("nan")
    snapshot["预警等级"] = out["预警等级"]
    snapshot["来源"] = source_label
    return unique_records(snapshot[SNAPSHOT_COLUMNS], KEYS)


def load_history(path=DEFAULT_HISTORY_PATH):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    history = pd.read_csv(path, keep_default_na=False)
    for column in SNAPSHOT_COLUMNS:
        if column not in history:
            history[column] = "当日" if column == "期间" else ""
    for column in METRICS:
        history[column] = pd.to_numeric(history[column], errors="coerce")
    if history[["预期赔付率", "成本率1"]].notna().all(axis=1).any():
        complete = history["预期赔付率"].notna() & history["成本率1"].notna()
        history.loc[complete, "预期成本率"] = apply_alert_rules(history.loc[complete])["预期成本率"]
    return history[SNAPSHOT_COLUMNS].drop_duplicates(KEYS, keep="last").reset_index(drop=True)


def _write_history(history, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".csv", delete=False, mode="w", encoding="utf-8-sig") as file:
        temp_path = Path(file.name)
        history.to_csv(file, index=False)
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def save_snapshot(df, source_label="", snapshot_date=None, path=DEFAULT_HISTORY_PATH):
    snapshot = build_snapshot(df, source_label, snapshot_date)
    if snapshot.empty:
        return {"saved": False, "rows": 0, "path": str(path), "message": "没有可保存的明细记录"}
    history = load_history(path)
    combined = pd.concat([history, snapshot], ignore_index=True)
    combined = combined.drop_duplicates(KEYS, keep="last").sort_values(KEYS, kind="stable")
    _write_history(combined, path)
    return {"saved": True, "rows": len(snapshot), "path": str(path), "message": "快照已保存"}


def delete_history(snapshot_date=None, path=DEFAULT_HISTORY_PATH):
    history = load_history(path)
    if snapshot_date is None:
        remaining = history.iloc[:0]
    else:
        remaining = history[history["日期"].ne(str(snapshot_date))]
    removed = len(history) - len(remaining)
    _write_history(remaining, path)
    return removed


def import_history_files(files, path=DEFAULT_HISTORY_PATH):
    from src.parser.upload_data import list_upload_sheets, read_uploaded_data
    snapshots = []
    for file in files:
        sheets = list_upload_sheets(file)
        dated = [sheet for sheet in sheets if re.search(r"20\d{2}[.\-_/年]?\d{2}[.\-_/月]?\d{2}", sheet)]
        for sheet in (dated or sheets[:1]):
            if any(word in sheet for word in ("说明", "细分", "子表")):
                continue
            frame = read_uploaded_data(file, sheet)
            if frame.empty or "车型" not in frame:
                continue
            if "统计日期" not in frame and "日期" not in frame:
                match = re.search(r"(20\d{2})[.\-_/年]?(\d{2})[.\-_/月]?(\d{2})", sheet + " " + str(file.name))
                if not match:
                    raise ValueError(f"历史数据 {file.name}/{sheet} 缺少日期列或可识别的文件日期")
                frame["统计日期"] = "-".join(match.groups())
            snapshots.append(build_snapshot(frame, f"历史导入：{file.name}/{sheet}"))
    if not snapshots:
        raise ValueError("没有找到可导入的历史主表")
    incoming = unique_records(pd.concat(snapshots, ignore_index=True), KEYS)
    history = pd.concat([load_history(path), incoming], ignore_index=True).drop_duplicates(KEYS, keep="last")
    _write_history(history.sort_values(KEYS), path)
    return len(incoming)


def build_trend(history, branch="全辖", vehicle="全部车型", metric="预期成本率", period=None):
    columns = ["日期", metric, "记录数"]
    if history.empty or metric not in history:
        return pd.DataFrame(columns=columns)
    view = history.copy()
    if metric == "预期成本率" and {"预期赔付率", "成本率1"}.issubset(view.columns):
        complete = view["预期赔付率"].notna() & view["成本率1"].notna()
        view.loc[complete, metric] = apply_alert_rules(view.loc[complete])[metric]
    if branch != "全部机构":
        view = view[view["机构"].eq(branch)]
    if vehicle != "全部车型":
        view = view[view["车型"].eq(vehicle)]
    if "期间" in view:
        period = period or next((p for p in ("当日", "当月", "当年") if p in set(view["期间"])), "当日")
        view = view[view["期间"].eq(period)]
    view[metric] = pd.to_numeric(view[metric], errors="coerce")
    view["签单保费_万元"] = pd.to_numeric(view.get("签单保费_万元", pd.Series(index=view.index, dtype=float)), errors="coerce")
    view = view.dropna(subset=["日期", metric])
    rows = []
    for day, group in view.groupby("日期", sort=True):
        weights = group["签单保费_万元"].fillna(0)
        valid = weights > 0
        if valid.any() and metric in ("预期成本率", "预期赔付率", "成本率1"):
            value = (group.loc[valid, metric] * weights[valid]).sum() / weights[valid].sum()
        else:
            value = group[metric].mean()
        rows.append({"日期": day, metric: round(float(value), 4), "记录数": len(group)})
    return pd.DataFrame(rows, columns=columns)


def demo_history(df, days=7):
    frames = []
    anchor = pd.Timestamp(date.today())
    for i in range(days):
        sample = df.copy()
        if "预期赔付率" in sample:
            sample["预期赔付率"] = pd.to_numeric(sample["预期赔付率"], errors="coerce") + (i % 3 - 1) * 1.5 + i * .4
        frames.append(build_snapshot(sample, "内置合成演示历史", anchor - pd.Timedelta(days=days - i - 1)))
    return pd.concat(frames, ignore_index=True)


def rapid_changes(trend, metric, days=3, threshold=5, direction="上升"):
    if len(trend) < 2:
        return pd.DataFrame()
    view = trend.copy()
    view["日期"] = pd.to_datetime(view["日期"])
    view = view.sort_values("日期")
    recent = view[view["日期"] >= view.iloc[-1]["日期"] - pd.Timedelta(days=days)]
    if len(recent) < 2:
        return pd.DataFrame()
    change = float(recent.iloc[-1][metric] - recent.iloc[0][metric])
    triggered = change >= threshold if direction == "上升" else change <= -threshold
    return pd.DataFrame([{"指标": metric, "起始值": recent.iloc[0][metric], "最新值": recent.iloc[-1][metric],
                          "变化": round(change, 4), "结果": "提示快速变化" if triggered else "未触发"}])

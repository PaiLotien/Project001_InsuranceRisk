"""声明式校验：原始行位置独立于 DataFrame 索引，百分数采用 0–100 单位。"""

from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import pandera.pandas as pa
import yaml
from src.utils.helpers import is_total_row

ROOT = Path(__file__).resolve().parents[2]
ISSUE_COLUMNS = ["sheet", "row", "column", "rule_id", "severity", "message"]


def validate_frame(frame, sheet="", first_data_row=2, report="T2"):
    config = yaml.safe_load((ROOT / "config/data_quality_rules.yaml").read_text())
    df = frame.reset_index(drop=True).copy()
    issues = []

    def add(rule, positions, column, message):
        for pos in positions:
            source_row = (
                df.iloc[pos].get("来源行号", df.iloc[pos].get("来源行", first_data_row + pos))
                if pos is not None
                else None
            )
            issues.append(
                dict(
                    sheet=(df.iloc[pos].get("来源Sheet", sheet) if pos is not None else sheet),
                    row=source_row,
                    column=column,
                    rule_id=rule["id"],
                    severity=rule.get("severity", "error"),
                    message=message,
                )
            )

    business = {r["id"]: r for r in config.get("rules", [])}
    for rule in config["schema_rules"]:
        if not rule.get("enabled", True):
            continue
        if rule["id"] in business:
            if not business[rule["id"]].get("enabled", True):
                continue
            if rule["id"] == "DQ001":
                rule = {**rule, "tolerance": business["DQ001"].get("tolerance_percentage_points", 1.0)}
        col, kind = rule.get("column"), rule["kind"]
        if kind in ("required", "numeric", "range", "text"):
            if col not in df:
                if kind == "required":
                    add(rule, [None], col, "缺少必填列")
                continue
            if kind == "required":
                check = pa.Check(lambda s: s.notna() & s.astype(str).str.strip().ne(""), ignore_na=False)
            elif kind == "text":
                check = pa.Check(lambda s: s.map(lambda v: isinstance(v, str)), ignore_na=False)
            elif kind == "numeric":
                check = pa.Check(lambda s: np.isfinite(pd.to_numeric(s, errors="coerce")), ignore_na=False)
            else:
                check = pa.Check(
                    lambda s: pd.to_numeric(s, errors="coerce").between(rule["min"], rule["max"]), ignore_na=False
                )
            schema = pa.DataFrameSchema({col: pa.Column(None, checks=check, nullable=True)}, strict=False)
            try:
                schema.validate(df[[col]], lazy=True)
            except pa.errors.SchemaErrors as exc:
                positions = sorted({int(i) for i in exc.failure_cases["index"] if pd.notna(i)})
                add(rule, positions, col, rule["name"])
        elif kind == "formula" and set(rule["inputs"] + [col]).issubset(df):
            a, b = [pd.to_numeric(df[c], errors="coerce") for c in rule["inputs"]]
            expected = a + b if rule["operation"] == "sum" else a.div(b.where(b.ne(0))) * 100
            actual = pd.to_numeric(df[col], errors="coerce")
            bad = ~np.isfinite(expected) | ~np.isfinite(actual) | (actual - expected).abs().gt(rule["tolerance"])
            add(rule, list(np.flatnonzero(bad)), col, rule["name"])
        elif kind in ("total_required", "total_sum", "share") and {"机构", "车型"}.issubset(df):
            if kind == "share" and report not in ("T1", "T2"):
                continue
            if kind != "total_required" and col not in df:
                continue
            group_columns = [c for c in ["机构", "来源Sheet", "数据区块", "期间", "表类型"] if c in df]
            for _, group in df.groupby(group_columns, dropna=False, sort=False):
                if kind == "share" and "表类型" in group and not group["表类型"].eq("主表").all():
                    continue
                totals = group[group.apply(is_total_row, axis=1)]
                details = group[~group.apply(is_total_row, axis=1)]
                if kind == "total_required":
                    if len(totals) != 1:
                        add(rule, list(group.index[:1]), "车型", "每机构须有且只有一条合计行")
                elif kind == "share":
                    values = pd.to_numeric(details[col], errors="coerce")
                    if not len(values) or values.isna().any() or abs(values.sum() - 100) > rule["tolerance"]:
                        add(rule, list(group.index[:1]), col, rule["name"])
                elif len(totals) == 1:
                    values = pd.to_numeric(details[col], errors="coerce")
                    total = pd.to_numeric(totals[col], errors="coerce").iloc[0]
                    if values.isna().any() or not np.isfinite(total) or abs(values.sum() - total) > rule["tolerance"]:
                        add(rule, list(totals.index), col, rule["name"])
    daily = business.get("DQ003", {})
    if daily.get("enabled", False) and {"当日值", "当月天数", "当月总值"}.issubset(df):
        actual = pd.to_numeric(df["当日值"], errors="coerce") * pd.to_numeric(df["当月天数"], errors="coerce")
        expected = pd.to_numeric(df["当月总值"], errors="coerce")
        bad = (
            ~np.isfinite(actual)
            | ~np.isfinite(expected)
            | (actual - expected).abs().gt(expected.abs().mul(daily.get("ratio_tolerance", 0.2)).clip(lower=1))
        )
        add(
            {**daily, "severity": "warning"},
            list(np.flatnonzero(bad)),
            "当月总值",
            "当日×当月天数与当月总值偏离（仅作提示）",
        )
    return pd.DataFrame(issues, columns=ISSUE_COLUMNS)


def top3(issues):
    if issues.empty: return []
    counts = Counter(zip(issues["rule_id"], issues["message"]))
    return [{"rule_id": r, "message": m, "count": n} for (r, m), n in counts.most_common(3)]

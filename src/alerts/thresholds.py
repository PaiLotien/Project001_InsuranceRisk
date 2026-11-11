"""统一管理预警阈值、等级计算和上传数据的规则化预处理。"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from src.rules.expressions import COMPARATORS, matrix_threshold


ROOT = Path(__file__).resolve().parents[2]


def load_alert_config() -> dict[str, Any]:
    path = ROOT / "config" / "alert_rules.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_quality_config() -> dict[str, Any]:
    """加载数据质量规则配置，避免把校验口径散落在页面代码中。"""
    path = ROOT / "config" / "data_quality_rules.yaml"
    if not path.exists():
        return {"rules": []}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {"rules": []}


def configured_thresholds() -> dict[str, float]:
    """从 YAML 找到预期成本率各等级阈值，避免页面重复写数字。"""
    result = {"yellow": 90.0, "orange": 100.0, "red": 100.0}
    for rule in load_alert_config().get("rules", []):
        if rule.get("metric") != "预期成本率" or not rule.get("enabled", True):
            continue
        level = str(rule.get("level", "yellow")).lower()
        if level in result:
            result[level] = float(rule.get("threshold", result[level]))
    return result


def _level(value: float, thresholds: dict[str, float]) -> str:
    if pd.isna(value):
        return "normal"
    # 规则引擎采用“最高等级优先”：红 > 橙 > 黄。
    if COMPARATORS["gt"](value, thresholds["red"]):
        return "red"
    if COMPARATORS["gt"](value, thresholds["orange"]) and thresholds["orange"] < thresholds["red"]:
        return "orange"
    if COMPARATORS["gt"](value, thresholds["yellow"]):
        return "yellow"
    return "normal"


def calculate_alert_level(value: Any, yellow_threshold: float | None = None) -> str:
    """按当前配置计算单个值；批量数据由 apply_alert_rules 复用一次配置。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "normal"
    thresholds = configured_thresholds().copy()
    if yellow_threshold is not None:
        thresholds["yellow"] = float(yellow_threshold)
    return _level(value, thresholds)


def apply_alert_rules(df: pd.DataFrame, yellow_threshold: float | None = None) -> pd.DataFrame:
    """计算派生指标并生成系统预警等级，覆盖上传数据的同名人工标签。"""
    out = df.copy()
    if {"预期赔付率", "成本率1"}.issubset(out.columns):
        if "预期成本率" in out.columns and "原表预期成本率" not in out.columns:
            out["原表预期成本率"] = out["预期成本率"]
        calculated = pd.to_numeric(out["预期赔付率"], errors="coerce") + pd.to_numeric(
            out["成本率1"], errors="coerce"
        )
        out["预期成本率"] = calculated
    if "预期成本率" in out.columns:
        thresholds = configured_thresholds().copy()
        if yellow_threshold is not None:
            thresholds["yellow"] = float(yellow_threshold)
        values = pd.to_numeric(out["预期成本率"], errors="coerce")
        out["预警等级"] = [
            _level(value, {**thresholds,
                'yellow':matrix_threshold(row.get('机构',''),row.get('车型',''),'R005',thresholds['yellow']),
                'red':matrix_threshold(row.get('机构',''),row.get('车型',''),'R010',thresholds['red'])})
            for value,(_,row) in zip(values,out.iterrows())]
    else:
        out["预警等级"] = "normal"
    out.attrs["yellow_threshold"] = yellow_threshold
    return out


def validation_summary(df: pd.DataFrame, tolerance: float | None = None) -> pd.DataFrame:
    """执行当前单表可执行的数据质量规则并返回展示表。

    DQ002 明确需要两张报表，不能从一个已经拼接的 DataFrame 中臆造跨表结论；
    它由 :func:`cross_report_premium_check` 单独执行。
    """
    rows: list[dict[str, Any]] = []
    quality_rules = {}
    for rule in load_quality_config().get("rules", []):
        quality_rules[rule.get("id")] = rule
    dq001 = quality_rules.get("DQ001", {})
    if tolerance is None:
        tolerance = float(dq001.get("tolerance_percentage_points", 1.0))

    if dq001.get("enabled", True) and {"机构", "车型", "保费占比"}.issubset(df.columns):
        base = df[df["车型"].ne("合计")].copy()
        if "表类型" in base.columns:
            base = base[base["表类型"].eq("主表")]
        for branch, group in base.groupby("机构", dropna=False):
            values = pd.to_numeric(group["保费占比"], errors="coerce")
            valid = values.notna().all() and values.between(0, 100).all() and pd.notna(branch) and bool(str(branch).strip())
            if valid:
                total = values.sum()
                if abs(total - 100) <= tolerance:
                    status = "通过"
                else:
                    status = "不通过"
                detail = "仅数值求和；需确认没有混入子表或其他期间"
            else:
                total = float("nan")
                status = "无法执行"
                detail = "机构或占比缺失、格式无效或超出0至100"
            rows.append({
                "规则ID": "DQ001",
                "校验规则": "保费占比合计≈100%",
                "机构": branch,
                "实际值": round(float(total), 2),
                "容忍误差": f"±{tolerance:g}个百分点",
                "结果": status,
                "说明": detail,
                "目标值": 100.0,
                "差额": round(float(total) - 100, 4),
                "参与行数": len(group),
            })

    # DQ003 只有在配置显式启用且字段齐全时才执行，默认保持关闭。
    dq003 = quality_rules.get("DQ003", {})
    required = {"当日值", "当月天数", "当月总值"}
    if dq003.get("enabled", False) and required.issubset(df.columns):
        ratio_tolerance = float(dq003.get("ratio_tolerance", 0.20))
        for idx, row in df.iterrows():
            try:
                actual = float(row["当日值"]) * float(row["当月天数"])
                expected = float(row["当月总值"])
            except (TypeError, ValueError):
                continue
            rows.append({"规则ID": "DQ003", "校验规则": "当日×当月天数≈当月总值", "机构": row.get("机构", idx),
                         "实际值": round(actual, 2), "容忍误差": f"±{ratio_tolerance:.0%}",
                         "结果": "通过" if abs(actual - expected) <= max(abs(expected) * ratio_tolerance, 1) else "提示",
                         "说明": "仅作提示，不作为强制校验"})
    return pd.DataFrame(rows)


def quality_details(df: pd.DataFrame, checks: pd.DataFrame) -> pd.DataFrame:
    """返回 DQ001 不通过或无法执行的机构明细，不把细分表再次相加。"""
    if checks.empty or "机构" not in df.columns:
        return pd.DataFrame()
    failures = checks[checks["规则ID"].eq("DQ001") & checks["结果"].ne("通过")]
    parts = []
    for _, check in failures.iterrows():
        mask = df["机构"].isna() if pd.isna(check["机构"]) else df["机构"].eq(check["机构"])
        part = df[mask & df["车型"].ne("合计")].copy()
        if "表类型" in part.columns:
            part = part[part["表类型"].eq("主表")]
        part["机构合计"] = check["实际值"]
        part["距100%差额"] = check["差额"]
        parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def cross_report_premium_check(t2: pd.DataFrame, t7: pd.DataFrame) -> pd.DataFrame:
    """执行 DQ002：比较 T2 与 T7 同一机构的当月保费。

    输入契约：调用方先对齐统计月份、取数时点、口径和金额单位，再提供每机构一行
    的当月总额。本函数不猜测普通“签单保费”列的期间，也不自行合并明细与合计。
    缺失、重复、非有限金额一律标为无法执行；Decimal 避免二进制浮点误差。
    """
    result_columns = ["规则ID", "机构", "T2当月累计签单保费", "T7月签单保费", "差额", "结果", "说明"]
    left_col, right_col = "当月累计签单保费", "月签单保费"
    rule = {}
    for item in load_quality_config().get("rules", []):
        if item["id"] == "DQ002":
            rule = item
            break
    if not rule.get("enabled", False):
        return pd.DataFrame([{"规则ID": "DQ002", "结果": "未启用", "说明": "配置关闭"}], columns=result_columns)
    if "机构" not in t2.columns or "机构" not in t7.columns:
        return pd.DataFrame([{"规则ID": "DQ002", "机构": "-", "T2当月累计签单保费": None,
                              "T7月签单保费": None, "差额": None, "结果": "无法执行",
                              "说明": "两张表均需提供机构列"}], columns=result_columns)
    if left_col not in t2.columns or right_col not in t7.columns:
        return pd.DataFrame([{"规则ID": "DQ002", "机构": "-", "T2当月累计签单保费": None,
                              "T7月签单保费": None, "差额": None, "结果": "无法执行",
                              "说明": f"缺少金额列：T2需{left_col}，T7需{right_col}"}], columns=result_columns)

    if t2.empty or t7.empty:
        return pd.DataFrame([{"规则ID": "DQ002", "结果": "无法执行", "说明": "缺少至少一张表的数据"}], columns=result_columns)
    for frame in (t2, t7):
        missing_branch = frame["机构"].isna().any()
        blank_branch = frame["机构"].astype(str).str.strip().eq("").any()
        if missing_branch or blank_branch:
            return pd.DataFrame([{"规则ID": "DQ002", "结果": "无法执行", "说明": "存在空机构"}], columns=result_columns)

    branches = sorted(set(t2["机构"]).union(t7["机构"]), key=str)
    rows = []
    for branch in branches:
        left_rows = t2[t2["机构"] == branch]
        right_rows = t7[t7["机构"] == branch]
        left_value = right_value = difference = None
        status, detail = "无法执行", "每机构每表必须恰有一条已对齐口径的总额，当前存在缺失或重复"
        if len(left_rows) == 1 and len(right_rows) == 1:
            try:
                left_value = Decimal(str(left_rows.iloc[0][left_col]))
                right_value = Decimal(str(right_rows.iloc[0][right_col]))
                if not (left_value.is_finite() and right_value.is_finite()):
                    raise ValueError("金额非有限数")
                difference = left_value - right_value
                if difference == 0:
                    status = "通过"
                    detail = "数值完全一致（输入口径需调用方保证）"
                else:
                    status = "不通过"
                    detail = "请核查数据口径或抽取时间"
            except (InvalidOperation, TypeError, ValueError):
                left_value = right_value = None
                detail = "金额缺失或不是有限数值"
        rows.append({"规则ID": "DQ002", "机构": branch,
                     "T2当月累计签单保费": str(left_value) if left_value is not None else None,
                     "T7月签单保费": str(right_value) if right_value is not None else None,
                     "差额": str(difference) if difference is not None else None, "结果": status, "说明": detail})
    return pd.DataFrame(rows, columns=result_columns)

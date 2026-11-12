"""从 T2、T7 抽取同机构的当月保费，供 DQ002 对账。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd

from src.alerts.thresholds import cross_report_premium_check
from src.core.constants import METRIC_SIGNED_PREMIUM
from src.parser.sheet_detector import detect_report_type
from src.parser.t1_t2_parser import parse_t2


@dataclass(frozen=True)
class CrossReportInputs:
    """DQ002 所需的两张标准化输入表及抽取说明。"""

    t2: pd.DataFrame
    t7: pd.DataFrame
    notes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.t2.empty and not self.t7.empty


def _branch_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text or "全辖"


def _t2_frame(wb) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    matched = 0
    for sheet_name in wb.sheetnames:
        report_type, _ = detect_report_type(sheet_name)
        if report_type.value != "T2":
            continue
        matched += 1
        parsed = parse_t2(wb[sheet_name], sheet_name)
        # 只取“签单保费 × 当月”。当日值、月同比和月环比不能作为 DQ002 左值。
        metrics = []
        for metric in parsed.metrics:
            if (
                metric.metric_name == METRIC_SIGNED_PREMIUM
                and metric.time_dimension == "当月"
                and metric.table_role == "主表"
            ):
                metrics.append(metric)
        if not metrics:
            notes.append(f"T2 Sheet“{sheet_name}”未抽取到“签单保费-当月”")
            continue

        # 若同时存在明细行和合计行，优先使用明细行，避免重复相加。
        groups = {}
        for metric in metrics:
            groups.setdefault((metric.branch, metric.source_block), []).append(metric)
        for (branch, block), values in groups.items():
            totals = [m for m in values if m.vehicle_type == "合计"]
            chosen = totals if totals else values
            amounts = {}
            conflict = len(totals) > 1
            for metric in chosen:
                key = (metric.vehicle_type, metric.dimension_1, metric.dimension_2)
                amount = Decimal(str(metric.value))
                if key in amounts and amounts[key] != amount:
                    conflict = True
                amounts[key] = amount
            total = sum(amounts.values(), Decimal(0)) if not conflict else None
            if conflict:
                notes.append(f"{sheet_name} 区块{block}存在重复或冲突总额，不能自动合并")
            rows.append(
                {
                    "机构": _branch_text(branch),
                    "当月累计签单保费": total,
                    "来源行号": chosen[0].source_row or None,
                    "来源Sheet": f"{sheet_name} / 区块{block}",
                }
            )

    if matched == 0:
        notes.append("未识别到 T2（分车型预期成本率监控表（日月年））")
    return pd.DataFrame(rows, columns=["机构", "当月累计签单保费", "来源Sheet", "来源行号"]), notes


def _t7_frame(wb) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    matched = 0
    for sheet_name in wb.sheetnames:
        report_type, _ = detect_report_type(sheet_name)
        if report_type.value != "T7":
            continue
        matched += 1
        ws = wb[sheet_name]
        values = [list(row) for row in ws.iter_rows(values_only=True)]
        header = None
        for index, row in enumerate(values):
            next_row = []
            if index + 1 < len(values):
                next_row = values[index + 1]

            # 先查当前行，再查下一行；同名表头采用首次出现的列。
            branch_col = None
            premium_col = None
            for header_row in (row, next_row):
                for j, value in enumerate(header_row):
                    text = str(value).strip()
                    if text == "机构" and branch_col is None:
                        branch_col = j
                    if text == "月签单保费" and premium_col is None:
                        premium_col = j
            if branch_col is not None and premium_col is not None:
                header = (index, branch_col, premium_col)
                break

        if header is None:
            notes.append(f"T7 Sheet“{sheet_name}”未识别到“机构-月签单保费”双层表头")
            continue
        header_index, branch_col, premium_col = header
        for source_row, row in enumerate(values[header_index + 2 :], start=header_index + 3):
            raw_branch = None
            if branch_col < len(row):
                raw_branch = row[branch_col]
            branch = _branch_text(raw_branch)
            if branch == "机构":
                continue
            if branch == "全辖" and raw_branch is None:
                continue
            raw_value = None
            if premium_col < len(row):
                raw_value = row[premium_col]
            value = pd.to_numeric(pd.Series([raw_value]), errors="coerce").iloc[0]
            if pd.isna(value):
                continue
            rows.append({"机构": branch, "月签单保费": float(value), "来源Sheet": sheet_name, "来源行号": source_row})

    if matched == 0:
        notes.append("未识别到 T7（分机构车险日平台监控表）")
    return pd.DataFrame(rows, columns=["机构", "月签单保费", "来源Sheet", "来源行号"]), notes


def extract_dq002_inputs(wb) -> CrossReportInputs:
    """从已打开的 Workbook 中抽取 DQ002 的 T2/T7 输入。"""

    t2, t2_notes = _t2_frame(wb)
    t7, t7_notes = _t7_frame(wb)
    return CrossReportInputs(t2=t2, t7=t7, notes=tuple([*t2_notes, *t7_notes]))


def run_dq002(wb) -> tuple[CrossReportInputs, pd.DataFrame]:
    """抽取并执行 DQ002，供上传页直接调用。"""

    inputs = extract_dq002_inputs(wb)
    if not inputs.ready:
        return inputs, pd.DataFrame()
    return inputs, cross_report_premium_check(inputs.t2, inputs.t7)


def cross_report_issues(t2, t7, left_sheet="T2", right_sheet="T7"):
    """复用 DQ002 对账口径，输出统一问题结构（右表缺失行号为空）。"""
    from src.validation.schema import ISSUE_COLUMNS

    result = cross_report_premium_check(t2, t7)
    issues = []
    for _, row in result.iterrows():
        if row["结果"] == "通过":
            continue
        branch = row.get("机构", "")
        for frame, sheet, column in [(t2, left_sheet, "当月累计签单保费"), (t7, right_sheet, "月签单保费")]:
            positions = [i for i, (_, r) in enumerate(frame.iterrows()) if r.get("机构") == branch]
            for pos in positions or [None]:
                issues.append(
                    dict(
                        sheet=(frame.iloc[pos].get("来源Sheet", sheet) if pos is not None else sheet),
                        row=(frame.iloc[pos].get("来源行号", pos + 2) if pos is not None else None),
                        column=column,
                        rule_id="DQ002",
                        severity="error",
                        message=str(row.to_dict()),
                    )
                )
    return pd.DataFrame(issues, columns=ISSUE_COLUMNS)

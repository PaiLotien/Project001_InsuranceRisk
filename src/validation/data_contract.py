"""检查上传表的必需字段、数值格式和取值范围。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ValidationReport:
    """一次数据契约校验的机器可读结果。"""

    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        return "通过" if self.valid else "待核查"

    @property
    def message(self) -> str:
        messages = [*self.errors, *self.warnings]
        return "；".join(messages) if messages else "核心字段与数值范围检查通过"


CORE_REQUIRED_COLUMNS = ("车型",)
CORE_METRIC_COLUMNS = ("预期成本率", "预期赔付率")


def _non_empty_mask(series: pd.Series) -> pd.Series:
    """把空字符串、空白和缺失值统一视为无效标识。"""

    return series.notna() & series.astype(str).str.strip().ne("")


def _numeric_problem(series: pd.Series, *, minimum: float | None = None, maximum: float | None = None) -> str | None:
    """返回一列数值转换或范围检查的第一条可读问题。"""

    numeric = pd.to_numeric(series, errors="coerce")
    invalid_count = int(numeric.isna().sum() - series.isna().sum())
    if invalid_count:
        return f"字段“{series.name}”存在{invalid_count}个非数值单元格"
    if minimum is not None and (numeric.dropna() < minimum).any():
        return f"字段“{series.name}”不能小于{minimum:g}"
    if maximum is not None and (numeric.dropna() > maximum).any():
        return f"字段“{series.name}”不能大于{maximum:g}"
    return None


def validate_core_frame(df: pd.DataFrame) -> ValidationReport:
    """必需字段问题记为错误，可选字段问题记为警告。"""

    if not isinstance(df, pd.DataFrame):
        return ValidationReport(valid=False, errors=("上传对象不是DataFrame",))
    if df.empty:
        return ValidationReport(valid=False, errors=("上传表为空",))

    errors: list[str] = []
    warnings: list[str] = []
    columns = set(df.columns)

    missing_identity = []
    for column in CORE_REQUIRED_COLUMNS:
        if column not in columns:
            missing_identity.append(column)
    if missing_identity:
        errors.append(f"缺少必需字段：{', '.join(missing_identity)}")
    else:
        empty_vehicle_count = int((~_non_empty_mask(df["车型"])).sum())
        if empty_vehicle_count:
            errors.append(f"字段“车型”存在{empty_vehicle_count}个空值")

    available_metrics = []
    for column in CORE_METRIC_COLUMNS:
        if column in columns:
            available_metrics.append(column)
    if not available_metrics:
        errors.append("至少需要“预期成本率”或“预期赔付率”中的一个字段")

    # 率类字段允许大于100%，但拒绝负数和明显异常值；成本率的风险红线由 YAML 决定。
    for column in available_metrics:
        problem = _numeric_problem(df[column], minimum=0, maximum=200)
        if problem:
            errors.append(problem)

    if "保费占比" in columns:
        problem = _numeric_problem(df["保费占比"], minimum=0, maximum=100)
        if problem:
            warnings.append(problem)

    if "签单保费_万元" in columns:
        problem = _numeric_problem(df["签单保费_万元"], minimum=0)
        if problem:
            warnings.append(problem)

    if "机构" not in columns:
        warnings.append("未提供“机构”，区域汇总将按全辖或上传表原口径展示")

    return ValidationReport(valid=not errors, errors=tuple(errors), warnings=tuple(warnings))


def annotate_validation(df: pd.DataFrame, report: ValidationReport) -> pd.DataFrame:
    """将校验结果附加到明细表，供页面和导出结果直接展示。"""

    out = df.copy()
    out["数据校验状态"] = report.status
    out["数据校验信息"] = report.message
    return out


def report_to_dict(report: ValidationReport) -> dict[str, Any]:
    """将报告转换为 Streamlit/session state 友好的普通字典。"""

    return {
        "valid": report.valid,
        "status": report.status,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "message": report.message,
    }

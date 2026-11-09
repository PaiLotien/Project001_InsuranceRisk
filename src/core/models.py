"""核心数据模型 —— 纯数据结构，不依赖任何框架或数据库"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class AlertLevel(str, Enum):
    YELLOW = "黄色预警"
    ORANGE = "橙色预警"
    RED = "红色预警"


class ReportType(str, Enum):
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"
    T6 = "T6"
    T7 = "T7"
    T8 = "T8"
    T9 = "T9"
    UNKNOWN = "UNKNOWN"


@dataclass
class SheetMeta:
    """Sheet 元数据"""
    sheet_name: str
    report_type: ReportType = ReportType.UNKNOWN
    row_count: int = 0
    col_count: int = 0
    is_parseable: bool = False
    parse_note: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class MetricCell:
    """单个指标值"""
    vehicle_type: str
    metric_name: str
    value: Optional[float] = None
    time_dimension: str = "当日"
    branch: str = "全辖"
    dimension_1: str = ""  # 新续转等
    dimension_2: str = ""  # 产品组合等
    source_block: int = 0
    source_row: int = 0
    table_role: str = "主表"


@dataclass
class ParsedReport:
    """解析后的报表"""
    report_type: ReportType
    sheet_name: str
    report_date: Optional[date] = None
    data_caliber: str = ""
    branch: str = "全辖"
    unit_info: dict[str, str] = field(default_factory=dict)
    metrics: list[MetricCell] = field(default_factory=list)
    raw_headers: list[str] = field(default_factory=list)
    vehicle_types: list[str] = field(default_factory=list)
    row_count: int = 0


@dataclass
class AlertRecord:
    """预警记录"""
    sheet_name: str
    branch: str
    vehicle_type: str
    metric_name: str
    current_value: float
    threshold: float
    alert_level: AlertLevel = AlertLevel.YELLOW
    trigger_reason: str = ""
    rule_id: str = ""
    compare_operator: str = ">"
    triggered_at: datetime = field(default_factory=datetime.now)


@dataclass
class AttributionItem:
    """归因分析项"""
    dimension: str
    factor_name: str
    contribution_pct: float
    description: str
    is_primary: bool = False


@dataclass
class AttributionResult:
    """归因分析结果"""
    alert: AlertRecord
    target_metric: str
    target_value: float
    items: list[AttributionItem] = field(default_factory=list)
    summary: str = ""
    suggestions: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """数据校验结果"""
    rule_id: str
    rule_name: str
    is_passed: bool
    detail: str = ""
    actual_value: Optional[float] = None
    expected_value: Optional[float] = None

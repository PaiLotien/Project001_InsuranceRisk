"""将解析指标交给规则引擎，生成带报表来源的预警。"""

from typing import Optional

from src.core.models import AlertRecord, MetricCell
from src.rules.rule_engine import run_all_rules


def generate_alerts(
    metrics: list[MetricCell],
    sheet_name: str = "",
    rules: Optional[list[dict]] = None,
) -> list[AlertRecord]:
    """计算预警并附上来源 Sheet；rules 未提供时读取配置。"""
    alerts = run_all_rules(metrics, rules)

    for alert in alerts:
        alert.sheet_name = sheet_name

    return alerts


def generate_alerts_from_reports(
    reports: list,  # list[ParsedReport]
    rules: Optional[list[dict]] = None,
) -> list[AlertRecord]:
    """依次处理各报表，跳过没有指标的报表。"""
    all_alerts = []
    for report in reports:
        if not report.metrics:
            continue
        alerts = generate_alerts(
            report.metrics,
            sheet_name=report.sheet_name,
            rules=rules,
        )
        all_alerts.extend(alerts)

    return all_alerts


def generate_alerts_from_demo_data(
    demo_data: list[dict],
    rules: Optional[list[dict]] = None,
) -> list[AlertRecord]:
    """将演示数据中的数值列转为当日指标后计算预警。"""
    metrics = []
    for row in demo_data:
        vehicle_type = row.get("车型", "")
        for key, value in row.items():
            if key in ("车型", "is_demo"):
                continue
            if isinstance(value, (int, float)):
                metrics.append(MetricCell(
                    vehicle_type=vehicle_type,
                    metric_name=key,
                    value=float(value),
                    time_dimension="当日",
                ))

    return generate_alerts(metrics, sheet_name="模拟数据", rules=rules)

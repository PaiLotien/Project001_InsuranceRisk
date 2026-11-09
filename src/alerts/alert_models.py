"""预警数据模型（便利函数）

为 AlertRecord 提供格式化和筛选便利方法。
"""

from src.core.models import AlertLevel, AlertRecord


def format_alert(alert: AlertRecord) -> dict:
    """将 AlertRecord 格式化为前端展示用的字典"""
    level_emoji = {
        AlertLevel.YELLOW: "🟡",
        AlertLevel.ORANGE: "🟠",
        AlertLevel.RED: "🔴",
    }
    return {
        "Sheet": alert.sheet_name or "—",
        "机构/车型": f"{alert.branch}/{alert.vehicle_type}",
        "指标": alert.metric_name,
        "当前值": f"{alert.current_value:.1f}%",
        "阈值": f"{alert.threshold}%",
        "偏离": f"{alert.current_value - alert.threshold:+.1f}pp",
        "风险等级": f"{level_emoji.get(alert.alert_level, '')} {alert.alert_level.value}",
        "触发原因": alert.trigger_reason,
        "规则ID": alert.rule_id,
    }


def filter_alerts_by_level(alerts: list[AlertRecord], level: AlertLevel) -> list[AlertRecord]:
    """按预警等级筛选"""
    return [a for a in alerts if a.alert_level == level]


def get_alert_summary(alerts: list[AlertRecord]) -> dict:
    """生成预警摘要统计"""
    red = len(filter_alerts_by_level(alerts, AlertLevel.RED))
    orange = len(filter_alerts_by_level(alerts, AlertLevel.ORANGE))
    yellow = len(filter_alerts_by_level(alerts, AlertLevel.YELLOW))

    # 统计涉及的车型
    vehicles = list(set(a.vehicle_type for a in alerts))

    # 统计涉及的指标
    metrics = list(set(a.metric_name for a in alerts))

    return {
        "total": len(alerts),
        "red": red,
        "orange": orange,
        "yellow": yellow,
        "affected_vehicles": vehicles,
        "affected_metrics": metrics,
    }

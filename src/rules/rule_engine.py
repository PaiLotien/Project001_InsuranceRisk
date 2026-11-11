"""规则引擎

从 YAML 配置文件加载预警规则，对解析后的指标数据进行规则匹配。
支持: 绝对值阈值、变化率阈值、前置条件判断。
"""

import math
from pathlib import Path
from typing import Any, Optional

import yaml
from src.rules.expressions import COMPARATORS, SYMBOLS, safe_condition, matrix_threshold

from src.core.constants import (
    METRIC_AVG_PREMIUM,
    METRIC_CLAIM_FREQUENCY,
    METRIC_EXPECTED_COST_RATIO,
    METRIC_EXPECTED_LOSS_RATIO,
    METRIC_COST_RATIO_1,
)
from src.core.models import AlertLevel, AlertRecord, MetricCell
from src.rules.metrics import get_metric_value


def load_alert_rules(config_path: Optional[str] = None) -> list[dict]:
    """读取规则，默认路径为 config/alert_rules.yaml。"""
    if config_path is None:
        config_path = str(
            Path(__file__).parent.parent.parent / "config" / "alert_rules.yaml"
        )

    path = Path(config_path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("rules", [])


def evaluate_rule(rule: dict, metrics: list[MetricCell]) -> list[AlertRecord]:
    """按指标、期间和前置条件计算一条规则的预警。"""
    if not rule.get("enabled", True):
        return []

    metric_name = rule["metric"]
    operator = rule["operator"]
    threshold = rule.get("threshold")
    rule_id = rule["id"]
    if operator == "configured" or threshold is None:
        # 配置表只给出指标但没有比较方式/阈值时，不能凭经验自动补规则。
        return []
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(threshold):
        return []
    level_str = rule.get("level", "yellow")
    if level_str in ("黄色预警", "黄色", "yellow"):
        level = AlertLevel.YELLOW
    elif level_str in ("橙色预警", "橙色", "orange"):
        level = AlertLevel.ORANGE
    else:
        level = AlertLevel.RED

    alerts = []

    aliases = {
        "出险频度": {"出险频度", "累计出险频度", "满期出险频度"},
        "单均保费": {"单均保费", "单均保费非短"},
        "案均赔款": {"案均赔款"},
    }
    metric_names = aliases.get(metric_name, {metric_name})
    if rule.get("change_dimension"):
        change_dimension = str(rule["change_dimension"])
    elif operator == "change_lt":
        change_dimension = "月环比"
    else:
        change_dimension = "月同比"

    def preconditions_met(target: MetricCell) -> bool:
        conditions = rule.get("preconditions")
        if conditions is None:
            conditions = rule.get("precondition")
        if not conditions:
            return True
        if isinstance(conditions, dict):
            conditions = [conditions]

        for condition in conditions:
            condition_metric = condition.get("metric", "")
            candidates = []
            for m in metrics:
                if m.branch != target.branch or m.vehicle_type != target.vehicle_type or m.source_block != target.source_block:
                    continue
                if m.metric_name != condition_metric:
                    continue
                if m.dimension_1 != target.dimension_1 or m.dimension_2 != target.dimension_2:
                    continue
                if m.time_dimension != condition.get("time_dimension", "当月"):
                    continue
                candidates.append(m)
            # 重复值无法确定哪个才是同口径数据，不能任取一个使前置条件成立。
            if len(candidates) != 1:
                return False
            candidate = candidates[0]
            try:
                left = float(candidate.value)
                right = float(condition.get("value"))
            except (TypeError, ValueError):
                return False
            if not (math.isfinite(left) and math.isfinite(right)):
                return False
            op = condition.get("operator", "gt")
            matched = COMPARATORS.get(op, lambda a,b: False)(left,right)
            if not matched:
                return False
        return True

    # 获取所有有此指标的车型数据。变化类规则只读取明确标注的同比/环比字段，
    # 不把“当日值”误当成历史基准，避免在缺少历史数据时制造假预警。
    vehicle_values: dict[tuple, Optional[float]] = {}
    duplicate_keys = set()
    for m in metrics:
        if m.metric_name not in metric_names:
            continue
        if operator in {"change_gt", "change_lt"} and m.time_dimension != change_dimension:
            continue
        if operator not in {"change_gt", "change_lt"} and m.time_dimension not in rule.get(
            "time_dimensions", ["当日", "当月", "当年"]
        ):
            continue
        if not preconditions_met(m):
            continue
        key = (m.branch, m.vehicle_type, m.time_dimension, m.dimension_1, m.dimension_2, m.source_block)
        if key in vehicle_values:
            duplicate_keys.add(key)
        vehicle_values[key] = m.value

    for key, current_value in vehicle_values.items():
        if key in duplicate_keys:
            continue
        try:
            current_value = float(current_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(current_value):
            continue
        branch, vehicle_type, time_dimension, dim1, dim2, block = key
        effective_threshold = matrix_threshold(branch, vehicle_type, rule_id, threshold)
        matched = COMPARATORS.get(operator, lambda a,b: False)(current_value,effective_threshold)
        compare_operator = SYMBOLS.get(operator, '>')

        if not matched:
            continue

        unit = rule.get("unit", "")
        unit_label = {"percent": "%", "point": "个百分点", "wan_yuan": "万元"}.get(unit, unit)
        change_label = f"{metric_name}变化值" if operator.startswith("change_") else metric_name
        alerts.append(AlertRecord(
            sheet_name="",
            branch=branch,
            vehicle_type=vehicle_type,
            metric_name=metric_name,
            current_value=round(current_value, 2),
            threshold=effective_threshold,
            alert_level=level,
            trigger_reason=f"{time_dimension} {change_label} = {current_value:.2f}{unit_label}，触发规则阈值 {effective_threshold}{unit_label}",
            rule_id=rule_id,
            compare_operator=compare_operator,
            time_dimension=time_dimension, dimension_1=dim1, dimension_2=dim2,source_block=block,
        ))

    return alerts


def run_all_rules(
    metrics: list[MetricCell],
    rules: Optional[list[dict]] = None,
    combinations: Optional[list[dict]] = None,
) -> list[AlertRecord]:
    """运行全部启用规则，按等级及偏离度排列预警。"""
    if rules is None:
        rules = load_alert_rules()

    if combinations is None:
        path=Path(__file__).resolve().parents[2]/'config/alert_rules.yaml'
        combinations=(yaml.safe_load(path.read_text()) or {}).get('combinations',[])
    all_alerts = []
    for rule in rules:
        all_alerts.extend(evaluate_rule(rule,metrics))
    groups={}
    for alert in all_alerts:
        key=(alert.branch,alert.vehicle_type,alert.time_dimension,alert.dimension_1,alert.dimension_2,alert.source_block)
        groups.setdefault(key,[]).append(alert)
    for found in groups.values():
        flags={a.rule_id:True for a in found}
        upgrade=any(c.get('enabled',True) and safe_condition(c['expression'],flags) for c in combinations)
        if upgrade:
            for alert in found:
                alert.alert_level={AlertLevel.YELLOW:AlertLevel.ORANGE,AlertLevel.ORANGE:AlertLevel.RED,AlertLevel.RED:AlertLevel.RED}[alert.alert_level]
                alert.trigger_reason+='；组合条件成立，升一级'

    # 排序：红色 > 橙色 > 黄色；同等级按偏离度排序
    level_order = {AlertLevel.RED: 0, AlertLevel.ORANGE: 1, AlertLevel.YELLOW: 2}
    all_alerts.sort(key=lambda a: (
        level_order.get(a.alert_level, 99),
        -abs(a.current_value - a.threshold),
    ))

    return all_alerts


# ---- 简化归因的指标查询辅助函数 ----

def get_attribution_context(
    metrics: list[MetricCell],
    vehicle_type: str,
) -> dict[str, Any]:
    """获取某个车型的归因分析上下文数据

    返回该车型的核心指标值，用于归因分析。
    """
    context = {
        "车型": vehicle_type,
    }
    for metric_name in [
        METRIC_EXPECTED_COST_RATIO,
        METRIC_EXPECTED_LOSS_RATIO,
        METRIC_COST_RATIO_1,
        METRIC_AVG_PREMIUM,
        METRIC_CLAIM_FREQUENCY,
    ]:
        val = get_metric_value(metrics, vehicle_type, metric_name)
        context[metric_name] = val

    return context

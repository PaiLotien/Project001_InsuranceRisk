from unittest.mock import patch
import pytest
from src.rules.expressions import safe_condition, COMPARATORS
from src.rules.rule_engine import run_all_rules, evaluate_rule
from src.core.models import MetricCell, AlertLevel
from src.alerts.thresholds import apply_alert_rules
import pandas as pd


@pytest.mark.parametrize(
    "expr,result", [("A AND B", False), ("A OR B", True), ("A AND NOT B", True), ("频度恶化 AND 案均恶化", False)]
)
def test_expression(expr, result):
    assert safe_condition(expr, {"A": True, "B": False}) is result


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "A.x", "A[0]", "1+2", "[True]", "A > 1"])
def test_forbidden_expression(expr):
    with pytest.raises(ValueError):
        safe_condition(expr, {"A": True})


def test_combined_rules_same_scope_only():
    rules = [
        {"id": "A", "metric": "频度", "operator": "gt", "threshold": 1},
        {"id": "B", "metric": "案均", "operator": "gt", "threshold": 1},
    ]
    combinations = [{"expression": "A AND B"}]
    metrics = [MetricCell("家用车", "频度", 2, "当月", "湖北"), MetricCell("家用车", "案均", 2, "当月", "湖北")]
    assert all(a.alert_level == AlertLevel.ORANGE for a in run_all_rules(metrics, rules, combinations))
    metrics[1].branch = "江西"
    assert all(a.alert_level == AlertLevel.YELLOW for a in run_all_rules(metrics, rules, combinations))
    metrics[1].branch = "湖北"
    metrics[1].source_block = 1
    assert all(a.alert_level == AlertLevel.YELLOW for a in run_all_rules(metrics, rules, combinations))


def test_matrix_fallback_and_override():
    rule = {"id": "R005", "metric": "预期成本率", "operator": "gt", "threshold": 90}
    metric = [MetricCell("家用车", "预期成本率", 91, "当月", "湖北")]
    assert evaluate_rule(rule, metric)
    with patch(
        "src.rules.expressions._matrix_config",
        return_value={"thresholds": [{"branch": "湖北", "vehicle_type": "家用车", "rule_id": "R005", "threshold": 92}]},
    ):
        assert evaluate_rule(rule, metric) == []
        frame = pd.DataFrame({"机构": ["湖北", "江西"], "车型": ["家用车"] * 2, "预期成本率": [91, 91]})
        assert apply_alert_rules(frame)["预警等级"].tolist() == ["normal", "yellow"]


@pytest.mark.parametrize("op,expected", [("gt", False), ("gte", True), ("lt", False), ("lte", True)])
def test_central_boundary(op, expected):
    assert COMPARATORS[op](90, 90) is expected


def test_combinations_do_not_cross_periods_or_dimensions():
    rules = [
        {"id": "A", "metric": "频度", "operator": "gt", "threshold": 1},
        {"id": "B", "metric": "案均", "operator": "gt", "threshold": 1},
    ]
    metrics = [MetricCell("家用车", "频度", 2, "当月", "湖北"), MetricCell("家用车", "案均", 2, "当年", "湖北")]
    assert all(a.alert_level == AlertLevel.YELLOW for a in run_all_rules(metrics, rules, [{"expression": "A AND B"}]))
    metrics[1].time_dimension = "当月"
    metrics[1].dimension_1 = "新车"
    assert all(a.alert_level == AlertLevel.YELLOW for a in run_all_rules(metrics, rules, [{"expression": "A AND B"}]))

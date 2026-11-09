"""归因零值、缺失值和阈值传递的回归检查。"""

from unittest.mock import patch

import pandas as pd
import pytest
import numpy as np
from pathlib import Path
from streamlit.testing.v1 import AppTest

from src.alerts.thresholds import apply_alert_rules
from src.attribution.simple_attribution import analyze, batch_analyze
from src.utils.helpers import safe_float


@pytest.mark.parametrize("loss,cost,driver,loss_pct,cost_pct", [
    (110, 0, "赔付风险驱动", 100.0, 0.0),
    (0, 110, "费用成本驱动", 0.0, 100.0),
])
def test_zero_cost_component_is_valid(loss, cost, driver, loss_pct, cost_pct):
    result = analyze(pd.Series({"车型": "家用车", "预期赔付率": loss,
                                "成本率1": cost, "预期成本率": loss + cost}))
    assert result.cost_driver == driver
    assert result.loss_ratio_pct == loss_pct
    assert result.cost_ratio_pct == cost_pct
    assert len([item for item in result.items if item.dimension == "成本构成"]) == 2


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), "bad"])
def test_missing_component_is_not_assumed_zero(missing):
    result = analyze(pd.Series({"车型": "家用车", "预期赔付率": 110,
                                "成本率1": missing, "预期成本率": 110}))
    assert result.cost_driver == ""
    assert not [item for item in result.items if item.dimension == "成本构成"]


def test_custom_threshold_reaches_single_and_batch_summaries():
    data = apply_alert_rules(pd.DataFrame({"机构": ["演示机构"], "车型": ["家用车"],
                                          "预期赔付率": [60], "成本率1": [29]}), yellow_threshold=88)
    assert data.iloc[0]["预警等级"] == "yellow"
    single = analyze(data.iloc[0], yellow_threshold=88)
    batch = batch_analyze(data, yellow_threshold=88)[0]
    assert single.summary == batch.summary
    assert "阈值(88%) +1.0pp" in single.summary


def test_default_threshold_uses_config():
    with patch("src.attribution.simple_attribution.configured_thresholds", return_value={"yellow": 94}):
        result = analyze(pd.Series({"车型": "家用车", "预期成本率": 95}))
    assert "阈值(94%) +1.0pp" in result.summary


@pytest.mark.parametrize("value", [np.int64(60), np.float32(60)])
def test_dataframe_numeric_scalars_are_valid(value):
    assert safe_float(value) == 60.0


def test_ai_page_uses_configured_thresholds_without_model_call():
    path = Path(__file__).resolve().parents[1] / "src/dashboard/streamlit_app.py"
    captured = []

    def model_result(record, rule_text):
        captured.append(rule_text)
        return {"risk_summary": "测试说明", "evidence": [], "suggestions": [], "uncertainty": []}

    with patch("src.alerts.thresholds.configured_thresholds", return_value={"yellow": 88, "orange": 105, "red": 105}):
        app = AppTest.from_file(path, default_timeout=30).run()
        app.sidebar.radio[0].set_value("AI建议原型").run()
        with patch("src.llm_advisor.analyze", side_effect=model_result):
            button = next(b for b in app.button if b.label == "调用本地 qwen3.5:4b 生成建议")
            button.click().run()
    assert not app.exception
    assert len(captured) == 1
    assert "黄色阈值=88%" in captured[0]
    assert "红色阈值=105%" in captured[0]

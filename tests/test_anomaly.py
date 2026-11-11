"""统计层独立性、注入异常与时间方向验证。"""

import numpy as np
import pandas as pd
import pytest

from src.monitoring.anomaly import detect_deterioration, load_temporal_policy, score_anomalies


def test_forest_detects_injection_without_changing_alerts():
    rng = np.random.default_rng(42)
    frame = pd.DataFrame({"机构": [f"机构{i}" for i in range(80)], "车型": "A",
                          "频度": rng.normal(20, 1, 80), "案均": rng.normal(3000, 100, 80),
                          "预警等级": "yellow"})
    original = frame.copy(deep=True)
    current = frame.copy()
    current.loc[3, "频度"] = 150
    result = score_anomalies(current, ["频度", "案均"])
    assert 3 in result.nlargest(3, "anomaly_score").index
    assert result.loc[3, "top_metric"] == "频度"
    assert result["anomaly_score"].between(0, 100).all()
    pd.testing.assert_frame_equal(frame, original)
    assert current["预警等级"].eq("yellow").all()
    pd.testing.assert_frame_equal(result, score_anomalies(current, ["频度", "案均"]))
    historical = score_anomalies(current, ["频度", "案均"], reference=frame)
    assert historical.loc[3, "top_metric"] == "频度"
    assert historical.loc[3, "anomaly_score"] > historical["anomaly_score"].median()


@pytest.mark.parametrize("kind", ["duplicate", "missing", "small"])
def test_invalid_forest_samples(kind):
    frame = pd.DataFrame({"机构": ["甲", "乙"], "车型": "A", "指标": [1, 2]})
    if kind == "duplicate":
        frame.loc[1, "机构"] = "甲"
    elif kind == "missing":
        frame["指标"] = np.nan
    else:
        frame = frame.iloc[:1]
    with pytest.raises(ValueError):
        score_anomalies(frame, ["指标"])


def temporal_frame():
    return pd.DataFrame({"机构": "甲", "车型": "A", "日期": pd.date_range("2026-01-01", periods=12),
                         "预期成本率": [80.] * 8 + [100., 110., 120., 80.]})


def test_temporal_persistent_and_no_future_leakage():
    data = temporal_frame()
    result = detect_deterioration(data)
    assert result.loc[8, "sudden_change"]
    assert result.loc[10, "persistent_deterioration"]
    assert result.loc[11, "deterioration_days"] == 0
    pd.testing.assert_frame_equal(result.iloc[:9], detect_deterioration(data.iloc[:9]))
    assert load_temporal_policy()["consecutive_days"] == 3


def test_missing_day_breaks_streak():
    data = temporal_frame().drop(index=9)
    assert not detect_deterioration(data)["persistent_deterioration"].any()


def test_temporal_groups_are_isolated():
    data = temporal_frame()
    other = data.assign(机构="乙", 预期成本率=80.)
    result = detect_deterioration(pd.concat([data, other], ignore_index=True))
    assert not result.loc[result["机构"] == "乙", "sudden_change"].any()

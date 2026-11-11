"""定量归因守恒及合成注入回归。"""

import math

import pandas as pd
import pytest

from src.attribution.decomposition import additive_drilldown, log_decomposition, mix_rate_decomposition
from src.attribution.simple_attribution import analyze


def factors(frequency=20, severity=3000, premium=2000):
    return {"累计出险频度": frequency, "案均赔款": severity,
            "单均保费_元": premium, "预期赔付率": frequency * severity / premium}


@pytest.mark.parametrize("f,s,p", [(30, 4000, 1800), (10, 2000, 3000), (20, 3000, 2000), (15, 6000, 1000)])
def test_log_conservation(f, s, p):
    result = log_decomposition(factors(), factors(f, s, p))
    assert not result["fallback"]
    assert sum(result["contributions"].values()) == pytest.approx(result["total_change"], abs=1e-9)
    assert result["total_change"] == pytest.approx(math.log((f * s / p) / 30), abs=1e-9)


@pytest.mark.parametrize("changes", [{"频度分母": "满期暴露"}, {"赔款口径": "预期"},
                                    {"预期赔付率": 99}, {"案均赔款": 0}, {"单均保费_元": float("nan")}])
def test_incompatible_or_invalid_factors_warn(changes):
    result = log_decomposition(factors(), {**factors(), **changes})
    assert result["fallback"] and result["warning"]
    assert "contributions" not in result


def test_cost_ratio_not_loss_identity():
    assert log_decomposition(factors(), factors(), metric="预期成本率")["fallback"]


@pytest.mark.parametrize("new_group", [False, True])
def test_mix_rate_conservation(new_group):
    a = pd.DataFrame({"机构": ["甲", "乙"], "车型": ["A", "B"], "签单保费": [100, 200], "预期赔款": [30, 160]})
    b = pd.DataFrame({"机构": ["甲", "丙" if new_group else "乙"], "车型": ["A", "B"],
                      "签单保费": [250, 100], "预期赔款": [125, 90]})
    result = mix_rate_decomposition(a, b)
    assert result["mix"] + result["rate"] == pytest.approx(result["total_change"], abs=1e-9)
    assert result["details"][["mix", "rate"]].sum().sum() == pytest.approx(result["total_change"], abs=1e-9)


def test_mix_zero_denominator_rejected():
    frame = pd.DataFrame({"机构": ["甲"], "车型": ["A"], "签单保费": [0], "预期赔款": [10]})
    with pytest.raises(ValueError):
        mix_rate_decomposition(frame, frame)


@pytest.mark.parametrize("metric", ["赔款", "保费", "件数"])
def test_injected_branch_vehicle_hits_top3(metric):
    a = pd.DataFrame([{"机构": f"机构{i}", "车型": f"车型{j}", metric: 100.0}
                      for i in range(8) for j in range(6)])
    b = a.copy()
    b.loc[(b["机构"] == "机构3") & (b["车型"] == "车型2"), metric] += 2000
    result = additive_drilldown(a, b, metric, top_n=3)
    assert any(r["dimensions"] == {"机构": "机构3", "车型": "车型2"} for r in result)
    assert all(0 <= r["surprise"] <= math.log(2) for r in result)


def test_unchanged_drilldown_is_zero():
    frame = pd.DataFrame({"机构": ["甲"], "车型": ["A"], "赔款": [0]})
    assert additive_drilldown(frame, frame, "赔款")[0]["score"] == 0


def test_legacy_interface_quantitative_and_fallback():
    result = analyze({**factors(30), "车型": "家用车"}, reference=factors())
    assert not result.quantitative_analysis["fallback"]
    assert len([item for item in result.items if item.dimension == "三要素"]) == 3
    fallback = analyze({"车型": "家用车", "累计出险频度": 30})
    assert fallback.quantitative_analysis["fallback"]
    assert all(item.contribution_pct == 0 for item in fallback.items)

"""表格缺失值在界面上应显示为 —，而不是字面 None / nan。"""

import numpy as np
import pandas as pd

from src.dashboard.export import MISSING_DISPLAY, display_frame, mask_missing, present_frame, build_report_html
from src.attribution.simple_attribution import analyze


def _cells(frame):
    return [v for row in frame.itertuples(index=False) for v in row]


def test_present_frame_replaces_none_and_nan_in_factor_table():
    frame = pd.DataFrame({
        "维度": ["成本构成", "成本构成", "三要素", "三要素"],
        "因子": ["预期赔付率", "成本率1（费用率）", "出险频度", "单均保费"],
        "成本构成占比(%)": [74.2, 25.8, None, np.nan],
        "说明": ["预期赔付率 71.9%", "费用率 25.8%", "None", "nan"],
    })
    shown = present_frame(frame)
    share = shown["成本构成占比(%)"].tolist()
    assert share[0] == 74.2
    assert share[1] == 25.8
    assert share[2:] == [MISSING_DISPLAY, MISSING_DISPLAY]
    assert shown["说明"].tolist()[2:] == [MISSING_DISPLAY, MISSING_DISPLAY]
    text = " ".join(str(v) for v in _cells(shown))
    assert "None" not in text
    assert "nan" not in text.lower()


def test_present_frame_keeps_numeric_column_when_complete():
    frame = pd.DataFrame({"预期成本率": [96.9, 87.0], "机构": ["全辖", "圣保罗"]})
    shown = present_frame(frame)
    assert shown["预期成本率"].dtype.kind == "f"
    assert shown["预期成本率"].tolist() == [96.9, 87.0]


def test_attribution_factor_table_hides_missing_cost_share():
    result = analyze(pd.Series({
        "机构": "全辖", "车型": "企业车",
        "预期赔付率": 71.9, "成本率1": 25.0, "预期成本率": 96.9,
        "累计出险频度": 19.8, "单均保费_元": 1784,
    }))
    items_data = [{
        "维度": item.dimension,
        "因子": item.factor_name,
        "成本构成占比(%)": item.contribution_pct if item.dimension == "成本构成" else None,
        "说明": item.description,
    } for item in result.items]
    shown = present_frame(pd.DataFrame(items_data))
    missing_rows = shown[shown["维度"] == "三要素"]
    assert not missing_rows.empty
    assert (missing_rows["成本构成占比(%)"] == MISSING_DISPLAY).all()
    cost_rows = shown[shown["维度"] == "成本构成"]
    assert cost_rows["成本构成占比(%)"].map(lambda v: isinstance(v, (int, float))).all()
    assert "None" not in " ".join(map(str, _cells(shown)))


def test_print_report_uses_em_dash_for_missing():
    df = pd.DataFrame({
        "机构": ["全辖"], "车型": ["企业车"],
        "预期赔付率": [71.9], "成本率1": [25.0], "预期成本率": [np.nan],
        "预警等级": ["orange"],
    })
    html = build_report_html(df)
    assert "None" not in html
    assert MISSING_DISPLAY in html
    assert "nan" not in html.lower()

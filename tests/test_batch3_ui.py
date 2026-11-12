"""第 3 批：正式版界面、默认公开数据集、行业对标与处置跟踪；不发送任何通知。"""
import inspect
import math
import re
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from src.alerts.lifecycle import AlertLifecycle
from src.dashboard import batch_panel
from src.dashboard.quant_panel import factors, prepare_cells
from src.attribution.decomposition import log_decomposition, mix_rate_decomposition
from src.parser.excel_loader import read_excel

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/dashboard/streamlit_app.py"
NAV = ['监测总览', '预警中心', '归因分析', '数据质量', '处置跟踪', '预警通知', '行业对标', 'AI 风控顾问', '系统设置']
FORBIDDEN = re.compile(r"Demo|demo|测试|原型|演示|样例数据")
CHINA = "中国行业监测视图（国家统计局 / 金融监管总局 / 精算师协会公开统计）"


def _texts(app):
    out = []
    for kind in ("markdown", "caption", "info", "warning", "success", "error", "title", "header", "subheader"):
        out += [str(e.value) for e in app.get(kind)]
    out += [b.label for b in app.button] + [r.label for r in app.radio] + [o for r in app.radio for o in r.options]
    out += [t.label for t in app.get("tab")]
    return out


def test_navigation_titles_and_no_prototype_wording(tmp_path):
    with patch('src.config.threshold_store.DB_PATH', tmp_path / 'irds.db'):
        app = AppTest.from_file(APP, default_timeout=120).run()
        assert list(app.sidebar.radio[0].options) == NAV
        assert app.sidebar.radio[1].value.startswith("车险经营监测数据集")
        for page in NAV:
            app.sidebar.radio[0].set_value(page).run()
            assert not app.exception, page
            texts = _texts(app)
            bad = [t for t in texts if FORBIDDEN.search(t)]
            assert not bad, (page, bad[:3])
            joined = "\n".join(texts)
            assert "车险智能风控平台" in joined and "IRDS · Insurance Risk Dynamic Surveillance" in joined
            assert "内部版本 2026.11" in joined


def test_page_config_and_default_dataset():
    source = APP.read_text(encoding="utf-8")
    assert 'page_title="车险智能风控平台"' in source
    assert "demo_dataset" not in source
    assert "fonts.googleapis" not in source and "http" not in (ROOT / "src/dashboard/theme.py").read_text(encoding="utf-8")


def test_official_dataset_keeps_real_state_names():
    df = read_excel(ROOT / "data/official/brvehins_2011.xlsx", sheet_name="完整数据集")
    states = set(df["机构"]) - {"全辖"}
    assert len(states) == 27 and {"圣保罗", "里约热内卢", "联邦区"} <= states
    assert not states & {"北京", "上海", "湖北", "广东"}


def test_log_decomposition_against_portfolio_is_exact():
    df = read_excel(ROOT / "data/official/brvehins_2011.xlsx", sheet_name="完整数据集")
    df = df[~df["车型"].eq("合计")]
    cells, reason = prepare_cells(df)
    assert cells is not None, reason
    base = factors(cells[cells["机构"].eq("全辖")])
    cur = factors(cells[cells["机构"].eq("圣保罗")])
    result = log_decomposition(base, cur)
    assert not result["fallback"]
    assert math.isclose(math.fsum(result["contributions"].values()), result["total_change"], abs_tol=1e-12)
    mix = mix_rate_decomposition(cells[cells["机构"].eq("全辖")], cells[cells["机构"].eq("圣保罗")],
                                 dimensions=("车型",), numerator="赔款", denominator="签单保费")
    assert math.isclose(mix["mix"] + mix["rate"], mix["total_change"], abs_tol=1e-9)


def test_quant_panel_degrades_without_counts():
    cells, reason = prepare_cells(pd.DataFrame({"机构": ["湖北"], "车型": ["家用车"], "预期成本率": [90]}))
    assert cells is None and "缺少" in reason


def test_industry_page_has_map_benchmark_and_scope_notes(tmp_path):
    with patch('src.config.threshold_store.DB_PATH', tmp_path / 'irds.db'):
        app = AppTest.from_file(APP, default_timeout=120).run()
        app.sidebar.radio[0].set_value("行业对标").run()
        assert not app.exception
        joined = "\n".join(_texts(app))
        assert "财产险全险种" in joined and "不含计划单列市" in joined and "2020 版" in joined
        assert "待核实" in joined and "并非定论" in joined
        assert any("choropleth" in c.proto.spec for c in app.get("plotly_chart"))
        assert any("赔付率(%)" in d.value.columns for d in app.dataframe)
        # 中国视图下明细页切换为行业对标，不混用巴西州数据
        app.sidebar.radio[1].set_value(CHINA).run()
        app.sidebar.radio[0].set_value("监测总览").run()
        assert not app.exception
        assert any("中国行业监测视图" in t for t in _texts(app))


def test_lifecycle_lists_cases_and_escalates(tmp_path):
    life = AlertLifecycle(tmp_path / "irds.db")
    ident = life.create("王经理", 0, alert_ref="圣保罗/企业车/R005")
    assert [c["id"] for c in life.list_cases()] == [ident]
    life.transition(ident, "已确认", "已核实")
    assert life.list_cases()[0]["status"] == "已确认"
    assert life.escalate_overdue() == 1


def test_cases_page_creates_case_in_isolated_db(tmp_path):
    with patch('src.config.threshold_store.DB_PATH', tmp_path / 'irds.db'), \
            patch('src.alerts.lifecycle.DEFAULT_DB', tmp_path / 'cases.db'), \
            patch('src.dashboard.cases_panel.AlertLifecycle', lambda: AlertLifecycle(tmp_path / 'cases.db')):
        app = AppTest.from_file(APP, default_timeout=120).run()
        app.sidebar.radio[0].set_value("处置跟踪").run()
        assert not app.exception
        next(t for t in app.text_input if t.label == "责任人").input("王经理").run()
        next(b for b in app.button if b.label == "新建处置单").click().run()
        assert not app.exception
        assert len(AlertLifecycle(tmp_path / 'cases.db').list_cases()) == 1


def test_batch_send_keeps_normal_delivery_behavior():
    source = inspect.getsource(batch_panel.render_batch_panel)
    assert '"发送全部预警通知"' in source
    assert "dry_run=True" not in source and "sender=send_batch_item" in source

"""反馈涉及的前端控制均在模拟运行中验证，不发送真实邮件。"""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "src/dashboard/streamlit_app.py"


def test_history_demo_has_seven_dates_and_can_switch_table_and_reset():
    app = AppTest.from_file(APP, default_timeout=45).run()
    app.sidebar.radio[0].set_value("核心指标表").run()
    assert not app.exception
    view = next(r for r in app.radio if r.label == "趋势视图")
    view.set_value("数据表").run()
    tables = [d.value for d in app.dataframe if "日期" in d.value and "记录数" in d.value]
    assert tables and tables[0]["日期"].nunique() == 7
    next(b for b in app.button if b.label == "重置图表视图").click().run()
    assert app.session_state["history_plot_rev"] == 1
    next(r for r in app.radio if r.label == "趋势视图").set_value("图表").run()
    assert not app.exception


def test_batch_requires_preview_and_confirmation_and_charts_have_headroom():
    app = AppTest.from_file(APP, default_timeout=45).run()
    app.sidebar.radio[0].set_value("风险预警中心").run()
    assert not app.exception
    send = next(b for b in app.button if b.label == "发送全部预警通知")
    assert send.disabled
    next(b for b in app.button if b.label == "查看收件人清单").click().run()
    assert not app.exception
    plan = app.session_state["notification_plan"]
    keys = [(item["record"]["机构"], item["record"]["车型"]) for item in plan["items"]]
    assert len(keys) == len(set(keys))
    assert next(b for b in app.button if b.label == "发送全部预警通知").disabled
    next(c for c in app.checkbox if c.label == "已核对本次收件人与抄送清单").check().run()
    assert not next(b for b in app.button if b.label == "发送全部预警通知").disabled
    # 不点击发送；真实 SMTP 不参与测试。
    charts = app.get("plotly_chart")
    for chart in charts:
        spec = json.loads(chart.proto.spec)
        if spec["data"] and spec["data"][0].get("type") == "bar":
            assert spec["data"][0]["cliponaxis"] is False
            assert spec["layout"]["margin"]["t"] >= 40

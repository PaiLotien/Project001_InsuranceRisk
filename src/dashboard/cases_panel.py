"""处置跟踪：预警处置单状态机（新建→已确认→处置中→已关闭）、责任人、时限与超时升级。"""

import time
from datetime import datetime

import pandas as pd
import streamlit as st

from src.alerts.lifecycle import STATES, AlertLifecycle
from src.dashboard.theme import kpi_row, note, page_title, section
from src.dashboard.export import present_frame


def _fmt(ts):
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


def open_case_count():
    try:
        return sum(1 for c in AlertLifecycle().list_cases() if c["status"] != "已关闭")
    except Exception:
        return 0


def render_cases(alert_df=None):
    page_title("处置跟踪", "预警处置闭环 · 责任到人 · 超时升级")
    lifecycle = AlertLifecycle()
    cases = lifecycle.list_cases()
    now = time.time()
    open_cases = [c for c in cases if c["status"] != "已关闭"]
    overdue = [c for c in open_cases if c["deadline"] < now]
    kpi_row([
        ("处置单总数", len(cases), "件", "slate", ""),
        ("待处置", len(open_cases), "件", "orange" if open_cases else "", "新建、已确认、处置中"),
        ("已超时", len(overdue), "件", "red" if overdue else "", "超过处置时限且未关闭"),
        ("已关闭", len(cases) - len(open_cases), "件", "", ""),
    ])

    left, right = st.columns([3, 2])
    with left:
        section("处置单列表")
        if cases:
            table = pd.DataFrame([{
                "处置单号": c["id"][:8],
                "关联预警": c.get("alert_ref", ""),
                "责任人": c["owner"],
                "状态": c["status"],
                "处置时限": _fmt(c["deadline"]),
                "是否超时": "是" if c["status"] != "已关闭" and c["deadline"] < now else "否",
                "已升级": "是" if c["escalated"] else "否",
                "最近记录": c["records"][-1].get("note", "") if c["records"] else "",
            } for c in cases])
            st.dataframe(present_frame(table), hide_index=True, use_container_width=True, height=360)
        else:
            st.info("暂无处置单。可在右侧根据预警新建。")
    with right:
        section("新建处置单")
        options = []
        if alert_df is not None and not alert_df.empty:
            options = [f"{r['机构']}/{r['车型']}/{r.get('触发规则', '') or r.get('预警等级', '')}" for _, r in alert_df.iterrows()]
        alert_ref = st.selectbox("关联预警", options, key="case_alert_ref") if options else st.text_input("关联预警（机构/车型/规则）", key="case_alert_ref_text")
        owner = st.text_input("责任人", key="case_owner")
        hours = st.number_input("处置时限（小时）", min_value=1, value=24, key="case_hours")
        if st.button("新建处置单", type="primary", key="case_create"):
            try:
                ident = lifecycle.create(owner, time.time() + hours * 3600, alert_ref=alert_ref or "")
                st.success(f"已新建处置单 {ident[:8]}")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    section("状态流转")
    if open_cases:
        labels = {c["id"]: f"{c['id'][:8]} · {c.get('alert_ref', '')} · {c['status']}" for c in open_cases}
        ident = st.selectbox("选择处置单", list(labels), format_func=labels.get, key="case_select")
        current = next(c for c in open_cases if c["id"] == ident)
        nxt = STATES[STATES.index(current["status"]) + 1]
        c1, c2 = st.columns([3, 1])
        record = c1.text_input(f"处置记录（流转至“{nxt}”）", key="case_note")
        c2.write("")
        if c2.button(f"流转至{nxt}", key="case_transition"):
            try:
                lifecycle.transition(ident, nxt, record)
                st.rerun()
            except (ValueError, KeyError) as exc:
                st.error(str(exc))
        with st.expander("处置轨迹"):
            st.dataframe(present_frame(pd.DataFrame([{**r, "at": _fmt(r["at"])} for r in current["records"]]).rename(
                columns={"status": "状态", "at": "时间", "note": "记录", "owner": "责任人"})), hide_index=True, use_container_width=True)
    else:
        st.caption("当前没有未关闭的处置单。")
    if st.button("检查超时并升级", key="case_escalate"):
        st.success(f"本次升级 {lifecycle.escalate_overdue()} 件")
    note("状态按 新建 → 已确认 → 处置中 → 已关闭 顺序流转，每次流转须填写处置记录；超时未关闭的处置单可一键升级并留痕。数据保存在本机 data/runtime/irds.db。")

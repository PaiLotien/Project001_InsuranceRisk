"""批量通知预览与可中断的任务面板。"""

import pandas as pd
import streamlit as st

from src.notification.batch import BatchJob, batch_signature, prepare_batch, recipient_preview, send_batch_item
from src.dashboard.export import present_frame


@st.fragment(run_every="1s")
def render_batch_status():
    job = st.session_state.get("notification_job")
    if job is None:
        return
    state = job.snapshot()
    completed = len(state["结果"])
    st.progress(completed / max(1, state["总数"]), text=f"{state['状态']}：已处理 {completed}/{state['总数']}")
    left, middle, right = st.columns(3)
    if left.button("暂停发送", disabled=state["状态"] != "运行中", key="pause_notification"):
        job.pause()
        st.rerun(scope="fragment")
    if middle.button("继续发送", disabled=state["状态"] != "已暂停", key="resume_notification"):
        job.resume()
        st.rerun(scope="fragment")
    if right.button("终止发送", disabled=state["状态"] in ("已完成", "已终止"), key="stop_notification"):
        job.cancel()
        st.rerun(scope="fragment")
    st.caption("暂停和终止作用于尚未开始的邮件；正在提交的一封可能仍会完成，不会自动重试。")
    if state["结果"]:
        st.dataframe(present_frame(pd.DataFrame(state["结果"])), hide_index=True, use_container_width=True)


def render_batch_panel(df, threshold):
    st.subheader("批量预警通知")
    signature = batch_signature(df, threshold)
    plan = st.session_state.get("notification_plan")
    if st.button("查看收件人清单", key="preview_recipients"):
        try:
            items = prepare_batch(df, threshold)
            st.session_state["notification_plan"] = {"signature": signature, "items": items, "used": False}
            st.session_state["notification_confirm"] = False
            plan = st.session_state["notification_plan"]
        except (ValueError, OSError) as exc:
            st.error(f"收件人清单生成失败：{exc}")
    valid = plan is not None and plan["signature"] == signature and not plan["used"]
    if plan is not None:
        preview = recipient_preview(plan["items"])
        recipients = {address for item in plan["items"] for address in [*item["to"], *item["cc"]]}
        branches = {item["record"]["机构"] for item in plan["items"]}
        st.caption(f"本次 {len(preview)} 封通知，覆盖 {len(branches)} 家机构、{len(recipients)} 个收件或抄送地址。合计行不单独发送，每机构车型保留一条；数据变化后须重新准备清单。")
        st.dataframe(present_frame(pd.DataFrame(preview)), hide_index=True, use_container_width=True)
    confirmed = st.checkbox("已核对本次收件人与抄送清单", key="notification_confirm")
    batch_size = st.number_input("每批发送数量", 1, 50, 5, key="notification_batch_size")
    job = st.session_state.get("notification_job")
    running = job is not None and job.snapshot()["状态"] not in ("已完成", "已终止")
    if st.button("发送全部预警通知", type="primary", disabled=not(valid and confirmed) or running):
        job = BatchJob(plan["items"], batch_size, sender=send_batch_item)
        st.session_state["notification_job"] = job
        plan["used"] = True
        job.start()
        st.rerun()

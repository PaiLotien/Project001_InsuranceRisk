"""历史导入、维护与多指标趋势页面。"""

from datetime import date

import plotly.express as px
import streamlit as st

from src.monitoring.drift import compare_drift
from src.monitoring.history import (build_snapshot, build_trend, delete_history,
                                    import_history_files, load_history, rapid_changes, save_snapshot)


def render_history_panel(df, source_label):
    with st.container():
        with st.expander("历史数据维护（导入、导出和删除）", expanded=False):
            day = st.date_input("快照日期", value=date.today(), key="history_snapshot_date")
            if st.button("保存当前快照", key="save_history_snapshot"):
                try:
                    result = save_snapshot(df, source_label, day)
                    st.success(f"已保存到本机历史：{result['rows']} 条记录")
                except (ValueError, OSError) as exc:
                    st.error(str(exc))

            files = st.file_uploader("批量导入历史文件", type=["xlsx", "csv"], accept_multiple_files=True, key="history_import")
            if st.button("导入历史包", disabled=not files):
                try:
                    count = import_history_files(files)
                    st.success(f"已导入 {count} 条历史记录")
                except (ValueError, OSError) as exc:
                    st.error(f"未写入历史：{exc}")
            st.caption("历史文件需带日期列或日期文件名；也可上传含多个日期 Sheet 的工作簿。跨电脑可导出后批量导入；当前仍为本机存储。")
            try:
                local = load_history()
            except (ValueError, OSError) as exc:
                st.error(f"本机历史无法读取：{exc}")
                return
            st.download_button("导出本机历史 CSV", local.to_csv(index=False).encode("utf-8-sig"),
                               "IRDS_历史快照.csv", "text/csv", key="history_export")
            if not local.empty:
                delete_day = st.selectbox("选择要删除的快照日期", sorted(local["日期"].unique()), key="delete_history_date")
                confirmed = st.checkbox("确认删除选定日期或清空本机历史", key="history_delete_confirm")
                left, right = st.columns(2)
                if left.button("删除指定日期快照", disabled=not confirmed):
                    delete_history(delete_day)
                    st.rerun()
                if right.button("清空本机历史", disabled=not confirmed):
                    delete_history()
                    st.rerun()

        history = local
        if history.empty:
            st.info("暂无本机历史。可以保存当前快照，或一次导入多个历史文件。")
            return
        if history["预期赔付率"].isna().all():
            st.caption("旧快照未保存成本组成，无法反推修正原表错误；可重新导入原始报表替换。")
        c1, c2, c3 = st.columns(3)
        branch = c1.selectbox("趋势机构", ["全部机构", *sorted(history["机构"].unique())], key="history_trend_branch")
        vehicle = c2.selectbox("趋势车型", ["全部车型", *sorted(history["车型"].unique())], key="history_trend_vehicle")
        metric = c3.selectbox("趋势指标", ["预期成本率", "预期赔付率", "成本率1", "单均保费_元", "累计出险频度"], key="history_metric")
        period = st.selectbox("趋势期间", sorted(history["期间"].unique()), key="history_period")
        trend = build_trend(history, branch, vehicle, metric, period)
        view = st.radio("趋势视图", ["图表", "数据表"], horizontal=True, key="history_view")
        if st.button("重置图表视图", key="history_reset"):
            st.session_state["history_plot_rev"] = st.session_state.get("history_plot_rev", 0) + 1
        if view == "数据表":
            st.dataframe(trend, hide_index=True, use_container_width=True)
        elif not trend.empty:
            fig = px.line(trend, x="日期", y=metric, markers=True)
            fig.update_layout(height=320, dragmode="pan", margin=dict(t=20, b=30))
            st.plotly_chart(fig, use_container_width=True, key=f"history_plot_{st.session_state.get('history_plot_rev', 0)}",
                            config={"scrollZoom": False, "doubleClick": "reset", "displaylogo": False})
        st.caption("率类成本指标有保费时按保费加权；单均保费、频度当前为记录均值，尚无件数和风险暴露分母。")
        st.subheader("短期快速变化提示")
        st.caption("以下为可调整的参考条件，待业务确认；只提示趋势，不改变业务预警等级。")
        days = st.number_input("观察天数", 1, 90, 3, key="rapid_days")
        unit = "元" if metric == "单均保费_元" else "个百分点"
        change = st.number_input(f"变化阈值（{unit}）", min_value=0.1, value=5.0, key="rapid_change")
        direction = st.selectbox("监测方向", ["上升", "下降"], index=1 if metric == "单均保费_元" else 0, key="rapid_direction")
        signals = rapid_changes(trend, metric, days, change, direction)
        if not signals.empty:
            st.dataframe(signals, hide_index=True, use_container_width=True)

        st.subheader("数据漂移监测")
        current = build_snapshot(df, source_label, day)
        dates = sorted(set(history["日期"]) - {str(day)})
        if not dates:
            st.info("需要另一个日期的历史数据作为漂移基准。")
            return
        baseline = st.selectbox("选择基准日期", dates, key="drift_baseline_day")
        comparison = compare_drift(history[history["日期"].eq(baseline)], current)
        st.dataframe(comparison, hide_index=True, use_container_width=True)

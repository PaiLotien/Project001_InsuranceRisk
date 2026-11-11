"""车险智能风控平台（IRDS · Insurance Risk Dynamic Surveillance）— Streamlit 前端。

默认加载 data/official/brvehins_2011.xlsx（车险经营监测数据集，SUSEP 公开统计 2011 年）。
另提供中国行业监测视图（国家统计局 / 金融监管总局 / 中国精算师协会公开统计）与机构数据上传。
"""

import os
import sys
import logging
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# 加载 .env（如有）
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_env_file = _PROJECT_ROOT / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

# 确保项目根目录在 sys.path 中
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.alerts.thresholds import apply_alert_rules, configured_thresholds, validation_summary, quality_details
from src.dashboard.history_panel import render_history_panel
from src.dashboard.batch_panel import render_batch_status
from src.parser.upload_data import UploadReadError, read_uploaded_data, list_upload_sheets
from src.validation.cross_report import run_dq002

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="车险智能风控平台",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import theme
from src.dashboard.theme import kpi_row, note, page_title, section, LEVEL_COLORS, LEVEL_LABELS, LEVEL_TINTS

theme.inject_css()
# 兼容保留页面中的旧样式类名
st.markdown("""
<style>
.ds-section-title { font-size:1.02rem; font-weight:650; margin:16px 0 10px; padding-left:10px; border-left:3px solid #0f766e; }
.ds-info-card { background:#ffffff; color:#1f2a37; border:1px solid #dfe5eb; border-radius:6px; padding:14px 18px; }
.ds-info-card table { width:100%; border-collapse:collapse; font-size:.88rem; }
.ds-info-card table td { padding:5px 6px; border-bottom:1px solid #eef2f5; color:#1f2a37; }
.ds-info-card table td:first-child { color:#5b6b7b; width:96px; white-space:nowrap; }
.ds-attribution-card { padding:16px 20px; border-radius:6px; border:1px solid #dfe5eb; margin-bottom:14px; color:#1f2a37; }
.ds-attribution-card h3 { margin:0 0 10px; font-size:1.05rem; font-weight:650; color:#1f2a37; }
</style>
""", unsafe_allow_html=True)

from src.utils.helpers import detail_frame
from src.alerts.pipeline import evaluate_frame
from src.dashboard.export import display_frame, build_report_xlsx, build_report_html, metric_explanations
from src.dashboard.region_map import render_region_map, _risk_by_branch
from src.dashboard.quant_panel import render_quantitative
from src.dashboard.industry_panel import render_industry
from src.dashboard.cases_panel import render_cases, open_case_count
from src.config.threshold_store import save_user_thresholds, reset_thresholds

# 数据源
SOURCE_OFFICIAL = "车险经营监测数据集（SUSEP 2011）"
SOURCE_CHINA = "中国行业监测视图（国家统计局 / 金融监管总局 / 精算师协会公开统计）"
SOURCE_UPLOAD = "上传机构数据"
OFFICIAL_PATH = _PROJECT_ROOT / "data" / "official" / "brvehins_2011.xlsx"
OFFICIAL_LABEL = "车险经营监测数据集（SUSEP 公开统计，2011 年）"
OFFICIAL_SOURCE = "CASdatasets brvehins1 / 巴西保险监管局 SUSEP AUTOSEG 公开统计（GPL-2+）"
OFFICIAL_SCOPE = ("27 个州及联邦区保持巴西真实地名；“全辖”为全国合计口径。签单保费、承保车年、赔案件数、案均赔款、"
                  "经验赔付率来自公开统计；金额单位为巴西雷亚尔（字段名沿用平台“元 / 万元”）；预期赔付率为公开数据拟合的 GLM 估计，"
                  "成本率1 为假设费用率（待业务校准），详见 docs/data_sources.md。")
CHINA_SOURCE = "国家统计局《中国统计年鉴》、国家金融监督管理总局月度统计、中国精算师协会基准纯风险保费表（2020 版）"
CHINA_SCOPE = "省级为财产险全险种已付口径（非车险单独口径），省份不含计划单列市；基准纯风险保费为 2020 版，已去掉车上人员责任险。"

# 颜色常量（与 theme 一致）
ACCENT = theme.BRAND_ACCENT
ACCENT_MUTED = theme.BRAND_ACCENT
ALERT_COLORS = dict(LEVEL_COLORS)
ALERT_LABELS = dict(LEVEL_LABELS)
TEXT_MUTED = theme.MUTED
PLOT_FONT = "#334155"
PLOT_TEMPLATE = theme.plot_template()


@st.cache_data
def load_official_dataset() -> pd.DataFrame:
    if not OFFICIAL_PATH.exists():
        return pd.DataFrame()
    from src.parser.excel_loader import read_excel
    return read_excel(OFFICIAL_PATH, sheet_name="完整数据集")


@st.cache_data
def load_uploaded_dataset(uploaded_file, source_sheet=None) -> pd.DataFrame:
    return read_uploaded_data(uploaded_file, source_sheet)


@st.cache_data
def get_dataset_stats(df: pd.DataFrame) -> dict:
    df = detail_frame(df)
    return {
        "total_records": len(df),
        "branch_count": df["机构"].nunique() if "机构" in df.columns else 0,
        "vehicle_count": df["车型"].nunique() if "车型" in df.columns else 0,
        "red_alerts": int((df.get("预警等级") == "red").sum()),
        "orange_alerts": int((df.get("预警等级") == "orange").sum()),
        "yellow_alerts": int((df.get("预警等级") == "yellow").sum()),
        "total_alerts": int(df.get("预警等级").isin(["yellow", "orange", "red"]).sum()),
    }


# 侧边栏
NAV_PAGES = ["监测总览", "预警中心", "归因分析", "数据质量", "处置跟踪", "预警通知", "行业对标", "AI 风控顾问", "系统设置"]

with st.sidebar:
    st.markdown(
        f'<div class="irds-side-brand"><div class="t">{theme.PRODUCT_NAME}</div>'
        f'<div class="s">{theme.PRODUCT_SUBTITLE}</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="irds-side-label">功能导航</div>', unsafe_allow_html=True)
    page = st.radio("导航", NAV_PAGES, index=0, key="nav_page", label_visibility="collapsed")

    st.divider()
    st.markdown('<div class="irds-side-label">数据来源</div>', unsafe_allow_html=True)
    data_source = st.radio("选择数据源", [SOURCE_OFFICIAL, SOURCE_CHINA, SOURCE_UPLOAD], label_visibility="collapsed")
    uploaded_file = None
    if data_source == SOURCE_UPLOAD:
        uploaded_file = st.sidebar.file_uploader(
            "上传 Excel 或 CSV 文件", type=["xlsx", "csv"], key="main_upload", label_visibility="collapsed",
        )

    st.divider()
    thresholds = configured_thresholds()
    with st.expander("预警阈值（预期成本率）", expanded=False):
        yellow_threshold = st.number_input(
            "黄色阈值（%）", min_value=0.0, max_value=200.0, value=float(thresholds["yellow"]), step=0.5,
            key="yellow_alert_threshold", help="黄色、橙色、红色须严格递增；超过阈值才触发。",
        )
        orange_threshold = st.number_input("橙色阈值（%）", min_value=0.0, max_value=200.0, value=float(thresholds["orange"]), step=0.5, key="orange_alert_threshold")
        red_threshold = st.number_input("红色阈值（%）", min_value=0.0, max_value=200.0, value=float(thresholds["red"]), step=0.5, key="red_alert_threshold")
        valid_thresholds = yellow_threshold < orange_threshold < red_threshold
        if valid_thresholds:
            save_user_thresholds(dict(yellow=yellow_threshold, orange=orange_threshold, red=red_threshold))
        if st.button("恢复默认", key="reset_thresholds"):
            reset_thresholds()
            for key in ("yellow_alert_threshold", "orange_alert_threshold", "red_alert_threshold"):
                del st.session_state[key]
            st.rerun()
        st.caption("橙色 95% 为默认参考值，待业务确认。红色严格大于阈值，等于红色阈值不判红。")
    st.caption(f"黄 {yellow_threshold:g}% · 橙 {orange_threshold:g}% · 红 {red_threshold:g}%")
    if not valid_thresholds:
        st.error("阈值须满足黄色＜橙色＜红色，当前输入未保存。")
        st.stop()
    st.markdown(f'<div style="font-size:.7rem;color:#7c8b99;margin-top:14px;">内部版本 {theme.VERSION} · 本地部署</div>', unsafe_allow_html=True)


def show_dataframe(data, *args, **kwargs):
    if isinstance(data, pd.DataFrame): data = display_frame(data)
    elif hasattr(data, 'data') and isinstance(data.data, pd.DataFrame): data = display_frame(data.data)
    return st.dataframe(data, *args, **kwargs)


def level_table(frame, columns, **kwargs):
    """等级中文化后按等级加克制色底，保留排序与筛选。"""
    view = display_frame(frame[[c for c in columns if c in frame.columns]])
    tint = {LEVEL_LABELS[k]: v for k, v in LEVEL_TINTS.items() if v}
    color = {LEVEL_LABELS[k]: v for k, v in LEVEL_COLORS.items()}

    def paint(row):
        level = row.get("预警等级", "")
        styles = [f"background-color: {tint[level]}" if level in tint else "" for _ in row.index]
        if "预警等级" in row.index and level in color:
            styles[list(row.index).index("预警等级")] += f"; color: {color[level]}; font-weight: 600"
        return styles

    formats = {c: "{:.1f}" for c in view.columns if view[c].dtype.kind == "f"}
    return st.dataframe(view.style.apply(paint, axis=1).format(formats, na_rep="—"), **kwargs)


def portfolio_rate(df, metric):
    """全辖口径：有“全辖”行时只用全辖行，否则按签单保费加权全部明细。"""
    if df.empty or metric not in df:
        return None
    view = df[df["机构"].eq("全辖")] if "机构" in df and df["机构"].eq("全辖").any() else df
    values = pd.to_numeric(view[metric], errors="coerce")
    weights = pd.to_numeric(view.get("签单保费_万元", pd.Series(1.0, index=view.index)), errors="coerce").fillna(0)
    weights = weights.where(values.notna(), 0)
    if weights.sum() > 0:
        return float((values.fillna(0) * weights).sum() / weights.sum())
    return float(values.mean()) if values.notna().any() else None


def quality_issue_frame(df):
    from src.validation.schema import validate_frame
    issues = df.attrs.get("smart_validation")
    if isinstance(issues, list):
        issues = pd.DataFrame(issues, columns=["sheet", "row", "column", "rule_id", "severity", "message"])
    if issues is None:
        issues = validate_frame(df, "主数据")
    return issues


# 通用数据加载
def get_usable_data() -> tuple[pd.DataFrame, str]:
    if data_source == SOURCE_OFFICIAL:
        df = load_official_dataset()
        if df.empty:
            st.error("未找到车险经营监测数据集，请检查 data/official/brvehins_2011.xlsx。")
            return pd.DataFrame(), ""
        return evaluate_frame(df, st.session_state.get("yellow_alert_threshold")), OFFICIAL_LABEL
    if data_source == SOURCE_CHINA:
        return pd.DataFrame(), ""
    uploaded = uploaded_file
    if uploaded:
        try:
            sheets = list_upload_sheets(uploaded)
            selected_sheet = st.sidebar.selectbox("主数据源（只读取所选Sheet）", sheets, key="main_source_sheet")
            df = load_uploaded_dataset(uploaded, selected_sheet)
        except UploadReadError as exc:
            st.sidebar.error(str(exc))
            return pd.DataFrame(), ""
        except Exception:
            logger.exception("处理上传明细时发生内部异常")
            st.sidebar.error("数据处理异常，请联系维护人员查看程序日志。")
            return pd.DataFrame(), ""
        if not df.empty:
            for warning in df.attrs.get("unit_warnings", []):
                st.sidebar.warning(warning)
            if df.attrs.get("blocks"):
                st.sidebar.dataframe(pd.DataFrame(df.attrs["blocks"]), hide_index=True)
            st.sidebar.caption("当日、当年及同比环比均已保留供规则计算；明细默认展示优先绝对期间。其他工作表未参与当前分析。")
            info = df.attrs.get("source_info", {})
            st.sidebar.caption(f"主数据源：{info.get('主数据源', '')}；忽略其他Sheet {info.get('忽略其他Sheet数', 0)} 张；排除细分区块 {info.get('排除细分区块数', 0)} 个。")
            if "统计日期" in df and df["统计日期"].notna().any():
                dates = sorted(pd.to_datetime(df["统计日期"], errors="coerce").dropna().dt.strftime("%Y-%m-%d").unique(), reverse=True)
                if dates:
                    selected_day = st.sidebar.selectbox("业务日期", dates, key="main_business_day")
                    df = df[pd.to_datetime(df["统计日期"], errors="coerce").dt.strftime("%Y-%m-%d").eq(selected_day)].copy()
            if "数据校验状态" in df.columns:
                status = str(df["数据校验状态"].iloc[0])
                message = str(df["数据校验信息"].iloc[0])
                if status == "通过":
                    st.sidebar.success("上传数据契约：通过")
                else:
                    st.sidebar.warning(f"上传数据契约：{status}")
                st.sidebar.caption(message)
            return evaluate_frame(df, st.session_state.get("yellow_alert_threshold")), f"上传文件：{uploaded.name}"
    return pd.DataFrame(), ""


def no_data_hint():
    if data_source == SOURCE_UPLOAD:
        st.info("请在侧栏上传机构报表（Excel / CSV）。上传后将自动识别报表结构并计算预警。")
    else:
        st.warning("当前数据源不可用，请在侧栏切换数据来源。")


def _bar_layout(fig, height=360, **kwargs):
    fig.update_traces(cliponaxis=False)
    fig.update_layout(template=PLOT_TEMPLATE, height=height, margin=dict(t=40, b=10, l=10, r=10), **kwargs)
    return fig


# 页面：监测总览
def render_home():
    df, source_label = get_usable_data()
    page_title("监测总览", source_label or "")
    if df.empty:
        no_data_hint()
        return
    stats = get_dataset_stats(df)
    loss = portfolio_rate(df, "预期赔付率")
    cost = portfolio_rate(df, "预期成本率")
    issues = quality_issue_frame(df)
    fmt = lambda v: "—" if v is None else f"{v:.1f}"
    kpi_row([
        ("预期赔付率", fmt(loss), "%", "", "全辖 · 签单保费加权"),
        ("预期成本率", fmt(cost), "%", "red" if cost and cost > red_threshold else ("orange" if cost and cost > orange_threshold else ""), f"全辖 · 红色阈值 {red_threshold:g}%"),
        ("红色预警", stats["red_alerts"], "件", "red", "机构 × 车型"),
        ("橙色预警", stats["orange_alerts"], "件", "orange", "机构 × 车型"),
        ("黄色预警", stats["yellow_alerts"], "件", "yellow", "机构 × 车型"),
        ("待处置", open_case_count(), "件", "slate", "未关闭处置单"),
        ("数据质量问题", len(issues), "项", "slate", "智能校验"),
    ])

    left, right = st.columns([2, 3])
    with left:
        section("风险等级分布")
        dist = df["预警等级"].value_counts()
        frame = pd.DataFrame([{"等级": LEVEL_LABELS[k], "数量": int(dist.get(k, 0))} for k in ["normal", "yellow", "orange", "red"]])
        fig = px.pie(frame, names="等级", values="数量", hole=0.58, color="等级",
                     color_discrete_map={LEVEL_LABELS[k]: LEVEL_COLORS[k] for k in LEVEL_COLORS})
        fig.update_traces(textinfo="value+percent", sort=False)
        fig.update_layout(template=PLOT_TEMPLATE, height=380, margin=dict(t=30, b=10, l=10, r=10),
                          annotations=[dict(text=f"{stats['total_records']}<br><span style='font-size:11px'>监测单元</span>", showarrow=False, font=dict(size=20))])
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with right:
        branches = _risk_by_branch(df)
        branches = branches[branches["机构"].ne("全辖")].dropna(subset=["风险值"])
        if data_source == SOURCE_UPLOAD:
            section("机构风险地图")
            render_region_map(df, height=380)
        else:
            section("地区风险排行（预期成本率，签单保费加权）")
            top = branches.sort_values("风险值", ascending=False).head(12).sort_values("风险值")
            colors = [LEVEL_COLORS["red"] if v > red_threshold else LEVEL_COLORS["orange"] if v > orange_threshold
                      else LEVEL_COLORS["yellow"] if v > yellow_threshold else "#5b8fa8" for v in top["风险值"]]
            fig = go.Figure(go.Bar(x=top["风险值"], y=top["机构"], orientation="h", marker_color=colors,
                                   text=top["风险值"].map("{:.1f}%".format), textposition="outside",
                                   customdata=top["预警数"], hovertemplate="%{y}<br>预期成本率 %{x:.1f}%<br>预警 %{customdata} 件<extra></extra>"))
            fig.add_vline(x=red_threshold, line_dash="dot", line_color=LEVEL_COLORS["red"], annotation_text=f"红色阈值 {red_threshold:g}%", annotation_position="top")
            _bar_layout(fig, height=380, xaxis_title="预期成本率（%）",
                        xaxis_range=[min(60, float(top["风险值"].min()) - 5) if not top.empty else 0, max(red_threshold, float(top["风险值"].max()) if not top.empty else 100) * 1.12])
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            st.caption("巴西 27 个州及联邦区按原地名展示，不映射为中国省份。")

    section("重点关注（预期成本率前 10）")
    alerts = df[df["预警等级"].isin(["red", "orange", "yellow"])]
    focus = alerts[alerts["机构"].ne("全辖")] if "机构" in alerts else alerts
    level_table(focus.sort_values("预期成本率", ascending=False).head(10),
                ["机构", "车型", "签单保费_万元", "预期赔付率", "成本率1", "预期成本率", "预警等级", "触发规则"],
                hide_index=True, use_container_width=True)

    c1, c2, c3 = st.columns([1, 1, 4])
    c1.download_button("一键导出", build_report_xlsx(df, checks=validation_summary(df)), "车险风控报告.xlsx", type="primary", use_container_width=True)
    c2.download_button("打印版", build_report_html(df, checks=validation_summary(df)), "车险风控报告.html", mime="text/html", use_container_width=True)
    from src.rules.rule_engine import load_alert_rules
    c3.caption(f"已启用规则 {sum(r.get('enabled', True) for r in load_alert_rules())} 条；触发情况见预警中心“触发规则”列。"
               "一键导出生成 Excel 报告（概览、预警明细、触发规则、数据质量、阈值说明），打印版为可直接打印的 HTML。")
    if data_source == SOURCE_OFFICIAL:
        note(f"<b>数据来源</b>：{OFFICIAL_SOURCE}。{OFFICIAL_SCOPE} 当前公开数据只有 2011 年一个期间，不生成同比环比。")

    section("经营趋势与历史快照")
    render_history_panel(df, source_label)
    with st.expander("指标口径与核心公式"):
        st.markdown("ECR（预期成本率）＝ELR（预期赔付率）＋CR1（成本率1）")
        st.markdown("CF（出险频度）＝〔已决案件数＋RBNR（已发生未报案案件数）〕÷承保件数")
        st.markdown("AP（单均保费）＝签单保费÷承保件数；PS（保费占比）＝车型签单保费÷总签单保费")
        st.caption("阈值作用：识别成本、同比及环比风险，组合条件成立时升级。红色采用严格大于边界。")
        show_dataframe(metric_explanations(), hide_index=True)


def render_metrics_table(df, source_label):
    col_filter1, col_filter2, col_filter3 = st.columns(3)
    with col_filter1:
        sel_branch = st.selectbox("机构", ["全部"] + sorted(df["机构"].unique().tolist()), key="metrics_branch")
    with col_filter2:
        levels = ["全部"] + sorted(df["预警等级"].unique().tolist())
        sel_level = st.selectbox("预警等级", levels, format_func=lambda x: ALERT_LABELS.get(x, x), key="metrics_level")
    with col_filter3:
        search = st.text_input("车型搜索", placeholder="输入车型关键词", key="metrics_search")
    view = df.copy()
    if sel_branch != "全部":
        view = view[view["机构"] == sel_branch]
    if sel_level != "全部":
        view = view[view["预警等级"] == sel_level]
    if search:
        view = view[view["车型"].str.contains(search, case=False, na=False)]
    st.caption(f"显示 {len(view)} / {len(df)} 条记录 · 预警等级由规则引擎自动计算")
    level_table(view, ["机构", "车型", "保费占比", "签单保费_万元", "预期赔付率", "成本率1", "预期成本率", "累计出险频度",
                       "单均保费_元", "预警等级", "触发规则", "规则类型"], use_container_width=True, height=500, hide_index=True)


# 页面：预警中心
def render_alert_center():
    render_batch_status()
    yellow = st.session_state.get("yellow_alert_threshold", configured_thresholds()["yellow"])
    df, source_label = get_usable_data()
    page_title("预警中心", "规则预警 · 指标明细 · 统计异常 · 批量通知")
    if df.empty:
        no_data_hint()
        return
    stats = get_dataset_stats(df)
    kpi_row([
        ("红色预警", stats["red_alerts"], "件", "red", f"预期成本率 > {red_threshold:g}%"),
        ("橙色预警", stats["orange_alerts"], "件", "orange", f"> {orange_threshold:g}%（待业务确认）"),
        ("黄色预警", stats["yellow_alerts"], "件", "yellow", f"> {yellow_threshold:g}%"),
        ("正常", stats["total_records"] - stats["total_alerts"], "件", "", ""),
    ])
    alert_df = df[df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    tabs = st.tabs(["预警清单", "指标明细", "统计异常", "批量通知"])

    with tabs[0]:
        if alert_df.empty:
            st.success("当前所有指标均在正常范围内。")
        else:
            c1, c2, c3 = st.columns(3)
            level = c1.selectbox("预警等级", ["全部", "red", "orange", "yellow"], format_func=lambda x: ALERT_LABELS.get(x, x), key="alert_level_filter")
            branch = c2.selectbox("机构 / 地区", ["全部", *sorted(alert_df["机构"].unique())], key="alert_branch_filter")
            order = c3.selectbox("排序", ["预警等级", "预期成本率", "预期赔付率", "签单保费_万元"], key="alert_sort")
            view = alert_df.copy()
            if level != "全部":
                view = view[view["预警等级"].eq(level)]
            if branch != "全部":
                view = view[view["机构"].eq(branch)]
            view["_sort"] = view["预警等级"].map({"red": 0, "orange": 1, "yellow": 2})
            if order == "预警等级" or order not in view:
                view = view.sort_values(["_sort", "预期成本率"], ascending=[True, False])
            else:
                view = view.sort_values(order, ascending=False)
            st.caption(f"共 {len(view)} 条预警；表头可再次排序。")
            level_table(view, ["机构", "车型", "签单保费_万元", "预期赔付率", "成本率1", "预期成本率", "累计出险频度",
                               "单均保费_元", "预警等级", "触发规则"], use_container_width=True, height=420, hide_index=True)

            col1, col2 = st.columns(2)
            with col1:
                section("机构预警排行")
                by_branch = alert_df.groupby("机构").size().sort_values(ascending=False).reset_index(name="数量").head(10)
                fig = px.bar(by_branch, x="机构", y="数量", text="数量")
                fig.update_traces(marker_color=theme.BRAND, textposition="outside", textfont_color=PLOT_FONT)
                _bar_layout(fig, 360, xaxis_title="", yaxis_title="预警数量", yaxis_range=[0, max(1, float(by_branch["数量"].max())) * 1.25])
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            with col2:
                section("车型预警排行")
                by_vehicle = alert_df.groupby("车型").size().sort_values(ascending=False).reset_index(name="数量").head(10)
                fig = px.bar(by_vehicle, x="车型", y="数量", text="数量")
                fig.update_traces(marker_color=ACCENT, textposition="outside", textfont_color=PLOT_FONT)
                _bar_layout(fig, 360, xaxis_title="", yaxis_title="预警数量", yaxis_range=[0, max(1, float(by_vehicle["数量"].max())) * 1.25])
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with tabs[1]:
        render_metrics_table(df, source_label)

    with tabs[2]:
        note("统计异常分（IsolationForest）独立于规则预警，只用于提示数值组合罕见的单元，<b>不改变预警等级</b>。")
        from src.monitoring.anomaly import score_anomalies
        reference = df.drop_duplicates(['机构', '车型'])
        try:
            scored = score_anomalies(reference, ['预期成本率'])
            show_dataframe(scored.rename(columns={'anomaly_score': '异常强度', 'top_metric': '主要敏感指标',
                                                  'top_metric_contribution': '分数变化', 'metric_explanation': '解释'}),
                           hide_index=True, use_container_width=True)
        except ValueError as exc:
            st.info(f"本次无法执行统计异常分析：{exc}")

    with tabs[3]:
        if alert_df.empty:
            st.info("当前没有预警，无需批量通知。")
        else:
            from src.dashboard.batch_panel import render_batch_panel
            render_batch_panel(alert_df, yellow)
            st.caption("未配置邮件服务时按模拟方式记录；配置 SMTP 后按正式流程投递。发送前须预览并确认收件人清单。")

    st.session_state["alert_df"] = alert_df
    st.session_state["full_df"] = df


# 页面：数据质量
def render_quality_page():
    page_title("数据质量", "智能校验 · 问题定位 · 报表接入识别")
    df, source_label = get_usable_data()
    tabs = st.tabs(["质量校验", "数据接入与报表识别"])
    with tabs[0]:
        if df.empty:
            no_data_hint()
        else:
            render_quality(df)
    with tabs[1]:
        render_upload_and_sheets()


def render_quality(df):
    from src.validation.schema import top3
    issues = quality_issue_frame(df)
    tolerance = st.number_input("保费占比容忍误差（百分点）", 0.0, 10.0, 1.0, 0.1, key="premium_tolerance")
    checks = validation_summary(df, tolerance)
    errors = int((issues["severity"] == "error").sum()) if not issues.empty else 0
    failed = int((checks["结果"] != "通过").sum()) if not checks.empty and "结果" in checks else 0
    kpi_row([
        ("智能校验问题", len(issues), "项", "orange" if len(issues) else "", "按行列定位"),
        ("其中错误级", errors, "项", "red" if errors else "", ""),
        ("涉及字段", issues["column"].nunique() if not issues.empty else 0, "个", "slate", ""),
        ("DQ001 未通过机构", failed, "家", "red" if failed else "", f"容忍 ±{tolerance:g} 个百分点"),
    ])
    section("主要三项问题（TOP3）")
    top = top3(issues)
    if not top:
        st.success("未发现智能校验问题。")
    else:
        cols = st.columns(3)
        for col, item in zip(cols, top):
            from html import escape
            col.markdown(f'<div class="irds-card"><h4>{escape(str(item["rule_id"]))} · {item["count"]} 处</h4>'
                         f'<p>{escape(str(item["message"]))}</p></div>', unsafe_allow_html=True)
    section("问题明细（定位到行和字段）")
    if issues.empty:
        st.caption("无问题明细。")
    else:
        sev_color = {"错误": "rgba(192,57,43,0.12)", "提示": "rgba(201,162,39,0.14)"}
        view = display_frame(issues)
        st.dataframe(view.style.apply(lambda r: [f"background-color: {sev_color.get(r.get('严重程度'), '')}" if c == "严重程度" else "" for c in r.index], axis=1),
                     hide_index=True, use_container_width=True, height=300)
        located = _issue_cells(df, issues)
        if located is not None:
            section("问题单元格高亮")
            frame, marks = located
            st.dataframe(frame.style.apply(lambda col: ["background-color: rgba(192,57,43,0.18); font-weight:600" if (i, col.name) in marks else "" for i in col.index]),
                         use_container_width=True, height=280)
    section("DQ001 保费占比合计校验")
    if checks.empty:
        st.info("当前单表没有可展示的 DQ001 结果；DQ003 默认关闭，DQ002 需同时提供 T2 与 T7。")
    else:
        show_dataframe(checks, use_container_width=True, hide_index=True)
        details = quality_details(df, checks)
        if not details.empty:
            st.markdown("**异常机构参与求和的原始明细**")
            show_dataframe(details, use_container_width=True, hide_index=True)
            st.download_button("下载校验异常明细", details.to_csv(index=False).encode("utf-8-sig"), "数据质量异常.csv", "text/csv")
    st.caption("DQ001 只汇总主表明细，不重复加入细分表或合计行；±1 个百分点暂为参考设置。DQ002 只在同时具备 T2 与 T7 时执行。")


def _issue_cells(df, issues):
    """把问题行号映射回当前表格，返回仅含问题行的表与需高亮的 (行, 列) 集合。"""
    if "来源行" in df.columns or "来源行号" in df.columns:
        key = "来源行号" if "来源行号" in df.columns else "来源行"
        index = {str(v): i for i, v in zip(df.index, df[key])}
    else:
        index = {str(pos + 2): i for pos, i in enumerate(df.index)}
    marks = set()
    for _, row in issues.iterrows():
        target = index.get(str(row.get("row")).split(".")[0])
        if target is not None and row.get("column") in df.columns:
            marks.add((target, row["column"]))
    if not marks:
        return None
    rows = sorted({r for r, _ in marks})
    cols = [c for c in df.columns if c in {"机构", "车型"} or any(c == m for _, m in marks)]
    return df.loc[rows, cols], marks

# 页面：归因分析
def render_attribution():
    page_title("归因分析", "定量分解 · 规则归因 · 同业对比")

    df, source_label = get_usable_data()
    if df.empty:
        st.warning("请选择数据源以进行归因分析。")
        return

    render_quantitative(df)
    section("单条预警规则归因")

    alert_df = df[df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    if alert_df.empty:
        st.success("无预警触发，无需归因分析。")
        return

    col1, col2 = st.columns(2)
    with col1:
        sel_branch = st.selectbox(
            "机构", ["全部"] + sorted(alert_df["机构"].unique().tolist()), key="attr_branch",
        )
    with col2:
        sel_level = st.selectbox(
            "预警等级", ["全部", "红色预警", "橙色预警", "黄色预警"], key="attr_level",
        )

    candidate = alert_df.copy()
    if sel_branch != "全部":
        candidate = candidate[candidate["机构"] == sel_branch]
    level_map = {"红色预警": "red", "橙色预警": "orange", "黄色预警": "yellow"}
    if sel_level != "全部":
        candidate = candidate[candidate["预警等级"] == level_map[sel_level]]

    if candidate.empty:
        st.info("无匹配的预警记录。")
        return

    alert_options = []
    for _, row in candidate.iterrows():
        alert_options.append(
            f"{row['机构']} | {row['车型']} | "
            f"预期成本率={row['预期成本率']:.1f}% | "
            f"赔付率={row['预期赔付率']:.1f}% | 费用率={row['成本率1']:.1f}%"
        )

    selected_idx = st.selectbox(
        "选择预警记录以查看归因分析",
        range(len(alert_options)),
        format_func=lambda i: alert_options[i],
    )
    selected = candidate.iloc[selected_idx]
    st.divider()

    # ---- 调用新归因引擎 ----
    from src.attribution.simple_attribution import analyze, _match_vehicle_type, get_rules
    reference = get_rules().get("tri_factor_analysis", {}).get("reference_thresholds", {})
    with st.expander("三要素参考阈值设置"):
        frequency_limit = st.number_input("出险频度参考阈值（%）", min_value=0.0,
                                         value=float(reference.get("claim_frequency_percent", 25)), key="tri_frequency")
        premium_limit = st.number_input("单均保费参考阈值（元）", min_value=0.0,
                                       value=float(reference.get("average_premium_yuan", 2000)), key="tri_premium")
        st.caption("25% 和 2000 元暂为参考值，未找到已确认的业务口径；可按调研结论调整，不据此宣称同比恶化。")
    yellow_threshold = st.session_state.get("yellow_alert_threshold", configured_thresholds()["yellow"])
    result = analyze(selected, df, yellow_threshold=yellow_threshold,
                     frequency_threshold=frequency_limit, premium_threshold=premium_limit)

    # 先展示输入证据，再展示解释，避免将规则推测误认为原始事实。
    from src.attribution.evidence import condition_evidence
    section("原始输入数据")
    st.caption(f"来源：{source_label}；以下为当前加载表的完整记录，非保单原件。预警等级由当前规则引擎重新计算。")
    show_dataframe(selected.to_frame().T, use_container_width=True, hide_index=True)
    st.download_button("下载当前记录 CSV", selected.to_frame().T.to_csv(index=False).encode("utf-8-sig"),
                       file_name="attribution_input.csv", mime="text/csv")
    section("触发条件与归因依据")
    evidence_rows = []
    category = _match_vehicle_type(str(selected.get("车型", "")))
    for rule in get_rules().get("attribution_rules", {}).get(category, []):
        for condition in rule.get("conditions", []):
            evidence = condition_evidence(condition, selected)
            evidence.pop("matched")
            evidence_rows.append({"规则": rule["id"], **evidence})
    if evidence_rows:
        show_dataframe(pd.DataFrame(evidence_rows), use_container_width=True, hide_index=True)
    else:
        st.info("该车型尚无专属归因条件。")
    st.caption("规则内全部条件满足才生成专属结论；缺少维度或比较基准时标为无法判断。成本占比说明构成，不代表异常变化的因果贡献。")

    vehicle_class = _match_vehicle_type(str(selected.get("车型", "")))
    loss_ratio = selected["预期赔付率"]
    cost_ratio = selected["成本率1"]
    total_cost = selected["预期成本率"]
    deviation = total_cost - yellow_threshold
    level_color = ALERT_COLORS[selected["预警等级"]]
    claim_freq = selected.get("累计出险频度", None)
    avg_premium = selected.get("单均保费_元", None)

    # ---- 左侧：归因分析主体 ----
    col_left, col_right = st.columns([3, 2])

    with col_left:
        # 车型识别 + 归因结论
        vc_label = f"车型大类: {vehicle_class}" if vehicle_class else "车型未匹配专属规则"
        st.markdown(f"""
        <div class="ds-attribution-card" style="border-left:3px solid {level_color};background:#ffffff;">
            <h3>归因结论</h3>
            <p style="font-size:0.85rem;color:{TEXT_MUTED};margin:0 0 8px 0;">{vc_label}</p>
            <p style="font-size:0.95rem;line-height:1.8;color:#334155;margin:0;">
                <strong style="color:{ACCENT};">【{result.cost_driver}】</strong>
                {selected['机构']} · {selected['车型']}
                预期成本率: <strong style="color:{level_color};font-size:1.1rem;">{total_cost:.1f}%</strong>
            </p>
            <p style="font-size:0.85rem;color:{TEXT_MUTED};margin:6px 0 0;">
                偏离黄色预警阈值({yellow_threshold:g}%): {deviation:+.1f}pp
            </p>
        </div>
        """, unsafe_allow_html=True)

        # 风险维度映射
        if result.matched_dimensions:
            st.markdown('<div class="ds-section-title">风险维度映射</div>', unsafe_allow_html=True)
            dim_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">'
            for d in result.matched_dimensions[:8]:
                dim_html += (
                    f'<div style="background:#f2f8f8;border:1px solid #cfe3e1;'
                    f'border-radius:6px;padding:6px 12px;font-size:0.78rem;color:#334155;">'
                    f'<strong style="color:{ACCENT};">{d["name"]}</strong>'
                    f'<br><span style="font-size:0.7rem;color:{TEXT_MUTED};">{d["detail"]}</span></div>'
                )
            dim_html += '</div>'
            st.markdown(dim_html, unsafe_allow_html=True)

        # 归因因子明细
        st.markdown('<div class="ds-section-title">归因因子明细</div>', unsafe_allow_html=True)
        items_data = []
        for item in result.items:
            items_data.append({
                "维度": item.dimension,
                "因子": item.factor_name,
                "成本构成占比(%)": item.contribution_pct if item.dimension == "成本构成" else None,
                "说明": item.description,
                "主因": "🔴" if item.is_primary else "",
            })
        show_dataframe(pd.DataFrame(items_data), use_container_width=True, hide_index=True)

        # 贡献度柱状图
        st.markdown('<div class="ds-section-title">成本构成占比</div>', unsafe_allow_html=True)
        plot_data = [{"因子": i.factor_name, "构成占比(%)": i.contribution_pct, "维度": i.dimension}
                     for i in result.items if i.dimension == "成本构成"]
        if plot_data:
            fig_attr = px.bar(
                pd.DataFrame(plot_data), x="因子", y="构成占比(%)",
                color="维度", text="构成占比(%)",
                color_discrete_sequence=["#5eead4", "#a78bfa"],
            )
            fig_attr.update_traces(textposition="outside", textfont_color=PLOT_FONT)
            fig_attr.update_layout(template=PLOT_TEMPLATE, height=350,
                                   showlegend=True,
                                   legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(color=PLOT_FONT)))
            st.plotly_chart(fig_attr, use_container_width=True, config={"displayModeBar": False})

        # 车型专属规则链结果
        if result.triggered_rules:
            st.markdown('<div class="ds-section-title">触发规则链</div>', unsafe_allow_html=True)
            for rule in result.triggered_rules:
                st.markdown(f"""
                <div style="background:#fffaf0;color:#1f2a37;border:1px solid #f0dfb0;
                            border-radius:8px;padding:14px;margin-bottom:10px;">
                    <strong style="color:#9a7b12;">[{rule['rule_id']}]</strong>
                    <span style="color:#334155;font-size:0.85rem;">{rule['conclusion']}</span>
                </div>
                """, unsafe_allow_html=True)

        # 三要素联动分析
        if result.tri_factor_analysis:
            tf = result.tri_factor_analysis
            sev_color = {"高": "#f87171", "中": "#fbbf24", "极高": "#f87171"}.get(tf.get("severity", ""), "#fbbf24")
            st.markdown(f"""
            <div style="background:#fff6f5;color:#1f2a37;border:1px solid #f1c9c4;
                        border-radius:8px;padding:14px;margin-bottom:10px;">
                <strong style="color:{sev_color};">三要素联动分析 · 严重度:{tf.get('severity','-')}</strong>
                <p style="color:#334155;font-size:0.85rem;margin:6px 0 0;">{tf.get('summary','')}</p>
                <p style="color:{TEXT_MUTED};font-size:0.8rem;margin:4px 0 0;">{tf.get('detail','')}</p>
            </div>
            """, unsafe_allow_html=True)

    # ---- 右侧 ----
    with col_right:
        section("预警详情")
        st.markdown(f"""
        <div class="ds-info-card">
            <table>
                <tr><td>机构</td><td><strong>{selected['机构']}</strong></td></tr>
                <tr><td>车型</td><td><strong>{selected['车型']}</strong></td></tr>
                <tr><td>车型大类</td><td><strong>{vehicle_class or '未匹配'}</strong></td></tr>
                <tr><td>预警等级</td><td><strong style="color:{level_color};">{ALERT_LABELS[selected['预警等级']]}</strong></td></tr>
                <tr><td>预期成本率</td><td><strong style="color:{level_color};">{total_cost:.1f}%</strong></td></tr>
                <tr><td>预期赔付率</td><td>{loss_ratio:.1f}%</td></tr>
                <tr><td>成本率1</td><td>{cost_ratio:.1f}%</td></tr>
                <tr><td>出险频度</td><td>{claim_freq if claim_freq else 'N/A'}%</td></tr>
                <tr><td>单均保费</td><td>{avg_premium if avg_premium else 'N/A'} 元</td></tr>
            </table>
        </div>
        """, unsafe_allow_html=True)

        # 整改建议
        if result.suggestions:
            st.markdown('<div class="ds-section-title" style="margin-top:16px;">整改建议</div>', unsafe_allow_html=True)
            for i, sug in enumerate(result.suggestions, 1):
                st.markdown(
                    f'<p style="font-size:0.83rem;color:#334155;margin:4px 0;line-height:1.5;">'
                    f'<span style="color:{ACCENT};font-weight:600;">{i}.</span> {sug}</p>',
                    unsafe_allow_html=True,
                )

        # 同车型正常分支对比
        st.divider()
        st.markdown('<div class="ds-section-title">同车型正常分支对比</div>', unsafe_allow_html=True)
        same_vehicle = df[
            (df["车型"] == selected["车型"])
            & (df["机构"] != selected["机构"])
            & (df["预警等级"] == "normal")
        ]
        if not same_vehicle.empty:
            # 保费规模越大的分支对同车型基准贡献越大，默认使用签单保费加权平均；
            # 若上传表缺少保费列，再退回简单平均并明确提示。
            premium_col = next((c for c in ("签单保费_万元", "签单保费") if c in same_vehicle.columns), None)
            if premium_col:
                weights = pd.to_numeric(same_vehicle[premium_col], errors="coerce").fillna(0)
                if weights.sum() > 0:
                    avg_normal_cost = float((same_vehicle["预期成本率"] * weights).sum() / weights.sum())
                    avg_normal_loss = float((same_vehicle["预期赔付率"] * weights).sum() / weights.sum())
                    peer_method = f"签单保费加权平均（{premium_col}）"
                else:
                    avg_normal_cost = same_vehicle["预期成本率"].mean()
                    avg_normal_loss = same_vehicle["预期赔付率"].mean()
                    peer_method = "简单平均（保费权重无有效值）"
            else:
                avg_normal_cost = same_vehicle["预期成本率"].mean()
                avg_normal_loss = same_vehicle["预期赔付率"].mean()
                peer_method = "简单平均（缺少保费列）"
            st.markdown(f"""
            <div style="background:#f3faf6;border:1px solid #cfe7d8;
                        border-radius:8px;padding:14px;font-size:0.8rem;color:#334155;">
                <strong style="color:#2f855a;">正常分支基准</strong> ({len(same_vehicle)} 个分支，{peer_method})<br/>
                预期成本率: <strong style="color:#2f855a;">{avg_normal_cost:.1f}%</strong>
                （本分支: <span style="color:{level_color};">{total_cost:.1f}%</span>）<br/>
                预期赔付率: <strong>{avg_normal_loss:.1f}%</strong>
                （本分支: {loss_ratio:.1f}%）
            </div>
            """, unsafe_allow_html=True)
        else:
            st.caption("该车型在其他分支无正常数据可对比")

    # ---- 批量归因摘要 ----
    st.divider()
    st.markdown('<div class="ds-section-title">全量预警归因摘要</div>', unsafe_allow_html=True)

    batch_alerts = alert_df.copy()
    loss_driven = sum(1 for _, r in batch_alerts.iterrows() if r["预期赔付率"] > r["成本率1"])
    cost_driven = len(batch_alerts) - loss_driven

    col_d1, col_d2, col_d3 = st.columns(3)
    with col_d1:
        st.metric("预警总数", len(batch_alerts))
    with col_d2:
        pct = round(loss_driven / len(batch_alerts) * 100) if len(batch_alerts) > 0 else 0
        st.metric("赔付驱动型", loss_driven, f"{pct}%")
    with col_d3:
        pct = round(cost_driven / len(batch_alerts) * 100) if len(batch_alerts) > 0 else 0
        st.metric("费用驱动型", cost_driven, f"{pct}%")

    st.caption(f"数据来源: {source_label}")


# 数据上传与报表识别
def render_upload_and_sheets():
    section("数据接入与报表识别")

    sheet_info = [
        ("T1", "车型日成本率", "出单日期", "深度解析"),
        ("T2", "日/月/年成本率", "出单日期", "深度解析"),
        ("T3", "全周期出险频度进度", "生效日期", "结构识别"),
        ("T4", "团车车型跟踪", "出单日期", "结构识别"),
        ("T5", "费用进度监控", "生效日期", "结构识别"),
        ("T6", "商业车单均保费进度", "生效日期", "结构识别"),
        ("T7", "机构日平台", "出单日期", "结构识别"),
        ("T8", "全周期赔付率日跟踪", "生效日期", "结构识别"),
        ("T9", "私家车新续转", "出单日期", "结构识别"),
    ]
    wb = None
    source_label = ""

    if data_source == SOURCE_UPLOAD:
        uploaded = uploaded_file
        if uploaded:
            if uploaded.name.lower().endswith(".csv"):
                st.info("CSV 为单表输入，仅上传单表时，不触发跨表一致性校验。")
                try:
                    show_dataframe(read_uploaded_data(uploaded), hide_index=True, use_container_width=True)
                except ValueError as exc:
                    st.error(str(exc))
                return
            import openpyxl
            try:
                uploaded.seek(0)
                wb = openpyxl.load_workbook(uploaded, data_only=True)
                source_label = f"上传文件：{uploaded.name}"
            except Exception as e:
                logger.exception("读取上传报表结构失败")
                st.error(f"文件读取失败：{e}")
    else:
        st.info(
            "当前为“车险经营监测数据集（SUSEP 2011）”。接入机构报表时，请在侧栏数据来源中选择“上传机构数据”，"
            "系统将自动识别以下 9 类报表结构。"
        )
        st.divider()
        section("支持的报表类型（9 类）")

        show_dataframe(
            pd.DataFrame(sheet_info, columns=["编号", "报表名称", "时间口径", "解析状态"]),
            use_container_width=True, hide_index=True,
        )
        return

    if wb is None:
        st.info("请在侧栏上传机构报表（Excel / CSV）以开始识别。")
        st.divider()
        show_dataframe(
            pd.DataFrame(sheet_info, columns=["编号", "报表名称", "时间口径", "解析状态"]),
            use_container_width=True, hide_index=True,
        )
        return

    st.success(f"文件已加载：{source_label}")

    from src.parser.excel_loader import get_sheet_names
    from src.parser.sheet_detector import analyze_all_sheets

    sheet_names = get_sheet_names(wb)
    sheet_metas = analyze_all_sheets(wb)

    st.markdown(f'<div class="ds-section-title">报表概览（共 {len(sheet_names)} 张）</div>', unsafe_allow_html=True)
    overview_data = []
    for meta in sheet_metas:
        overview_data.append({
            "报表名称": meta.sheet_name,
            "报表类型": meta.report_type.value,
            "物理行数": wb[meta.sheet_name].max_row,
            "非空行数": meta.row_count,
            "列数": meta.col_count,
            "可解析": "是" if meta.is_parseable else "部分",
            "说明": meta.parse_note,
        })
    show_dataframe(pd.DataFrame(overview_data), use_container_width=True, hide_index=True)

    st.markdown('<div class="ds-section-title">跨表数据质量校验 / 跨表校验</div>', unsafe_allow_html=True)
    inputs, dq002_result = run_dq002(wb)
    if inputs.ready:
        from src.validation.cross_report import cross_report_issues
        from src.validation.schema import top3
        cross_issues=cross_report_issues(inputs.t2,inputs.t7)
        st.write('跨表主要三项问题',top3(cross_issues))
        with st.expander('跨表问题定位'): show_dataframe(cross_issues,hide_index=True)
    if inputs.ready:
        st.caption("DQ002：只比较 T2 的“签单保费-当月”与 T7 的“月签单保费”；抽取不到这两个明确时间口径时不会臆造结论。")
        show_dataframe(dq002_result, use_container_width=True, hide_index=True)
    else:
        st.info("仅上传单表时，不触发跨表一致性校验。DQ002 需要同口径的 T2 当月签单保费和 T7 月签单保费。")
    for note in inputs.notes:
        st.caption(f"校验说明：{note}")

    st.markdown('<div class="ds-section-title">报表详情</div>', unsafe_allow_html=True)
    for meta in sheet_metas:
        status_icon = "✓" if meta.is_parseable else "○"
        with st.expander(f"{status_icon} {meta.sheet_name} ({meta.row_count} 行 × {meta.col_count} 列)"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**报表类型**：{meta.report_type.value}")
                st.write(f"**可深度解析**：{'是' if meta.is_parseable else '否'}")
                st.write(f"**说明**：{meta.parse_note}")
            with col2:
                if meta.metadata:
                    st.write("**元数据**：")
                    for k, v in meta.metadata.items():
                        st.write(f"- {k}: {v}")
            if meta.report_type.value in ("T1", "T2"):
                from src.parser.table_blocks import read_table_blocks
                blocks = read_table_blocks(wb[meta.sheet_name])
                st.caption(f"识别到 {len(blocks)} 个数据区块；物理行数包含标题、空白和注释，不等于数据记录数。")
                for number, block in enumerate(blocks, 1):
                    if not block.empty:
                        st.markdown(f"**区块 {number} · {block.iloc[0]['表类型']}**")
                        show_dataframe(block, hide_index=True, use_container_width=True)
            # 非 T1/T2 表不强行套用核心指标规则，但允许用户查看前几行，
            # 这样“结构识别”和“可用数据预览”是两个清晰边界。
            if meta.report_type.value not in ("T1", "T2"):
                from src.parser.excel_loader import read_excel
                preview = read_excel(uploaded, sheet_name=meta.sheet_name, header=None, nrows=12)
                st.caption("该报表当前为结构识别模式，以下为原始前 12 行预览。")
                show_dataframe(preview, use_container_width=True, hide_index=True)


# 页面：预警通知
def render_notification_center():
    from src.alerts.lifecycle import AlertLifecycle, STATES
    from src.notification.channels import dispatch
    import time

    df, source_label = get_usable_data()
    with st.expander("多渠道推送演练（企业微信 / 钉钉 / 飞书 / 邮件）"):
        batch = st.text_input("推送批次", "手工批次", key="dispatch_batch")
        if st.button("多渠道推送演练（不发送）", key="dispatch_dry_run"):
            alerts = [
                {"branch": r.get("机构", "全辖"), "batch": batch, "object": r.get("车型", ""), "level": r["预警等级"]}
                for _, r in df.iterrows()
                if r.get("预警等级") in ("yellow", "orange", "red")
            ]
            import yaml

            config = yaml.safe_load(
                (Path(__file__).resolve().parents[2] / "config/notification_rules.yaml").read_text()
            )["delivery"]
            config["dry_run"] = True
            st.write(dispatch(alerts, config=config))
        st.caption("演练只生成本机推送记录，不调用任何外部渠道。处置单的新建与流转见“处置跟踪”。")
        alert_ref = st.text_input("关联预警（机构/批次/车型/规则）")
        owner = st.text_input("责任人")
        hours = st.number_input("处置时限（小时）", min_value=1, value=24)
        if st.button("新建处置单"):
            try:
                st.write(AlertLifecycle().create(owner, time.time() + hours * 3600, alert_ref=alert_ref))
            except ValueError as exc:
                st.error(str(exc))
        ident = st.text_input("处置单 ID")
        state = st.selectbox("下一状态", STATES[1:])
        note = st.text_input("处置记录")
        if st.button("更新处置状态"):
            try:
                st.write(AlertLifecycle().transition(ident, state, note))
            except (ValueError, KeyError) as exc:
                st.error(str(exc))
        if st.button("检查超时升级"):
            st.write({"本次升级数": AlertLifecycle().escalate_overdue()})

    # SMTP 状态（提前定义，f-string 要用）
    from src.notification.email_sender import is_smtp_available

    smtp_available = False
    mode_badge = (
        '<span style="display:inline-block;background:rgba(74,222,128,0.12);color:#2f855a;'
        "border:1px solid rgba(74,222,128,0.2);border-radius:6px;padding:2px 10px;"
        'font-size:0.75rem;font-weight:600;">SMTP 实时</span>'
        if smtp_available
        else '<span style="display:inline-block;background:rgba(251,191,36,0.10);color:#9a7b12;'
        "border:1px solid rgba(251,191,36,0.15);border-radius:6px;padding:2px 10px;"
        'font-size:0.75rem;font-weight:600;">模拟模式</span>'
    )
    page_title("预警通知", "预警分发 · 收件人匹配 · 邮件生成")
    st.markdown(f"通知模式：{mode_badge}", unsafe_allow_html=True)

    if df.empty:
        st.warning("请选择数据来源。")
        return

    alert_df = df[df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    if alert_df.empty:
        st.success("当前没有预警，无需发送通知。")
        return

    from src.notification.recipient_mapper import (
        format_recipient_list,
        get_notification_context,
    )
    from src.notification.email_templates import generate_html_body, generate_subject
    from src.notification.email_sender import clear_send_history, get_send_history, send_email

    col_left, col_right = st.columns([3, 2])

    with col_left:
        section("选择预警记录")

        quick_col1, quick_col2 = st.columns(2)
        with quick_col1:
            level_filter = st.selectbox("预警等级", ["红色预警", "橙色预警", "黄色预警", "全部"], key="nc_level")
        with quick_col2:
            branch_filter = st.selectbox("机构", ["全部"] + sorted(alert_df["机构"].unique().tolist()), key="nc_branch")

        candidate = alert_df.copy()
        if level_filter != "全部":
            level_map = {"红色预警": "red", "橙色预警": "orange", "黄色预警": "yellow"}
            candidate = candidate[candidate["预警等级"] == level_map[level_filter]]
        if branch_filter != "全部":
            candidate = candidate[candidate["机构"] == branch_filter]

        if candidate.empty:
            st.info("没有匹配的预警记录。")
            return

        alert_options = []
        for _, row in candidate.iterrows():
            alert_options.append(
                f"{row['机构']} | {row['车型']} | 预期成本率={row['预期成本率']:.1f}% | 预期赔付率={row['预期赔付率']:.1f}%"
            )

        selected_idx = st.selectbox(
            "选择预警记录",
            range(len(alert_options)),
            format_func=lambda i: alert_options[i],
            key="nc_alert_select",
        )
        selected = candidate.iloc[selected_idx]

        ctx = get_notification_context(selected)

        # 地址编辑先于邮件模板生成，确保预览和实际投递对象一致。
        from src.notification.address_input import parse_addresses

        with col_right:
            section("收件邮箱")
            manual_to = st.text_area("收件人", key="nc_manual_to")
            manual_cc = st.text_area("抄送（可选）", key="nc_manual_cc")
            address_error = ""
            try:
                edited_to = parse_addresses(manual_to)
                edited_cc = [a for a in parse_addresses(manual_cc) if a.lower() not in {x.lower() for x in edited_to}]
                if not edited_to:
                    address_error = "请填写至少一个收件邮箱。"
            except ValueError as exc:
                edited_to, edited_cc = [], []
                address_error = str(exc)
            if address_error:
                st.warning(address_error)
            # 替换本次上下文，绝不追加配置文件中的默认收件人。
            ctx.override_addresses(edited_to, edited_cc)

        loss_ratio = selected["预期赔付率"]
        cost_ratio = selected["成本率1"]
        total_cost = selected["预期成本率"]
        is_loss_driven = loss_ratio > cost_ratio
        driver_text = "赔付驱动" if is_loss_driven else "费用驱动"
        yellow_threshold = st.session_state.get("yellow_alert_threshold", configured_thresholds()["yellow"])
        attribution_summary = (
            f"【{driver_text}】{selected['机构']} · {selected['车型']} "
            f"预期成本率 {total_cost:.1f}%，主要由"
            f"{'预期赔付率' if is_loss_driven else '成本率1'} "
            f"（{'%.1f' % (loss_ratio if is_loss_driven else cost_ratio)}%）贡献。"
            f"较黄色阈值 {yellow_threshold:g}% 偏离 {total_cost - yellow_threshold:+.1f} 个百分点。"
        )

        suggestions = (
            [
                "下钻分析出险频度和案均赔款",
                "检查该机构和车型的核保政策",
                "按新续转维度排查逆选择",
            ]
            if is_loss_driven
            else [
                "检查该车型的市场费用率",
                "评估渠道费用政策调整空间",
                "对比同类机构的费用率",
            ]
        )
        if selected.get("累计出险频度", 0) > 25:
            suggestions.append(f"出险频度 {selected['累计出险频度']:.1f}% 偏高，建议复核核保质量")

        subject = generate_subject(selected, threshold=yellow_threshold)
        html_body = generate_html_body(
            selected,
            context=ctx,
                    
            attribution_summary=attribution_summary,
            suggestions=suggestions,
            threshold=yellow_threshold,
        )

        st.divider()
        section("邮件预览")

        st.markdown(
            f"""
        <div class="irds-card">
            <strong style="color:#334155;font-size:0.8rem;">邮件主题</strong><br/>
            <span style="font-size:0.9rem;color:#1f2937;">{subject}</span>
        </div>
        """,
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            st.components.v1.html(html_body, height=700, scrolling=True)

    with col_right:
        section("收件人")

        recipients_data = format_recipient_list(ctx)
        role_labels = {
            "branch_manager": "机构负责人",
            "deputy_gm": "分管负责人",
            "hq_operations": "总部运营",
            "hq_director": "总部负责人",
        }
        for rd in recipients_data:
            role_display = role_labels.get(rd["角色"], rd["角色"])
            st.markdown(
                f"""
            <div class="ds-info-card" style="margin-bottom:8px;padding:12px 14px;">
                <strong style="color:#1f2937;">{rd["姓名"]}</strong><br/>
                <span style="font-size:0.75rem;color:{TEXT_MUTED};">{role_display} — {rd["职务"]}</span><br/>
                <span style="font-size:0.75rem;color:{ACCENT};">{rd["邮箱"]}</span>
            </div>
            """,
                unsafe_allow_html=True,
            )

        st.divider()
        section("通知规则")

        st.markdown(
            f"""
        <div class="irds-card" style="font-size:0.85rem;">
            <strong style="color:#b45309;">{ctx.escalation_description}</strong><br/><br/>
            <strong>优先级：</strong>{ctx.priority}<br/>
            <strong>响应时限：</strong>{ctx.response_hours} 小时内<br/>
            <strong>涉及部门：</strong>{ctx.department}
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.divider()
        section("发送操作")

        if st.button("模拟发送（不真实投递）", type="primary", use_container_width=True, disabled=bool(address_error)):
            with st.spinner("正在发送……"):
                record = send_email(
                    subject=subject,
                    body=html_body,
                    to_list=edited_to,
                    cc_list=edited_cc,
                    context=ctx,
                    dry_run=True,
                )
            if record.status == "success":
                st.success(f"邮件发送成功！(模拟)")
                st.markdown(f"""
                **发送编号**：`{record.send_id}`
                **时间**：{record.timestamp}
                **收件人**：{", ".join(record.recipients)}
                **模式**：模拟
                **状态**：成功
                """)
            else:
                st.error(f"发送失败 (模拟): {record.error_message}")

        if st.button("清空发送记录", use_container_width=True):
            clear_send_history()
            st.rerun()

    st.divider()
    section("发送记录")

    history = get_send_history()
    if history:
        history_data = []
        for h in history:
            history_data.append(
                {
                    "发送编号": h.send_id,
                    "时间": h.timestamp,
                    "模式": "模拟" if h.mode == "mock" else "邮件服务",
                    "主题": h.subject[:60] + ("..." if len(h.subject) > 60 else ""),
                    "收件人数": len(h.recipients),
                    "状态": "成功" if h.status == "success" else f"失败：{h.error_message}",
                }
            )
        show_dataframe(pd.DataFrame(history_data), use_container_width=True, hide_index=True)
    else:
        st.info("暂无发送记录。选择预警并填写收件邮箱后即可生成模拟发送记录。")

    st.caption(f"数据来源：{source_label}")


# AI 风控顾问

def render_ai_advisor():
    from src.advisor.context import build_alert_context
    from src.advisor.service import advisor_health, rebuild_index, stream_advisor, ask_advisor
    from src.advisor import rag

    page_title("AI 风控顾问", "本地模型 · 知识库检索 · 引用溯源 · 数据不出本机")
    health = advisor_health()
    state = lambda ok: "在线" if ok else "未就绪"
    kpi_row([
        ("本地模型服务", state(health["online"]), "", "" if health["online"] else "orange", rag.CHAT_MODEL),
        ("对话模型", state(health["chat_model"]), "", "" if health["chat_model"] else "orange", ""),
        ("向量模型", state(health["embed_model"]), "", "" if health["embed_model"] else "orange", ""),
        ("知识库索引", "需更新" if health["stale"] else "最新", "", "orange" if health["stale"] else "", f"{len(health['manifest'].get('files', {}))} 个文件"),
    ])
    if health["stale"]:
        st.warning("知识库未构建或已过期")
        if st.button("重建知识库"):
            try:
                with st.status("构建本地知识库"):
                    rebuild_index()
                st.rerun()
            except Exception as exc:
                st.error(f"本地索引构建失败：{exc}")
    with st.expander("管理员：Skill 与知识文件"):
        st.json(health["manifest"], expanded=False)
        st.json(rag.load_skills()[1])
        st.write(sorted(health["manifest"].get("files", {})))
    df, source_label = get_usable_data()
    if df.empty:
        st.info("请选择数据源")
        return
    alerts = df[df["预警等级"].isin(["red", "orange", "yellow"])]
    if alerts.empty:
        st.success("当前没有预警记录")
        return
    idx = st.selectbox(
        "选择一条规则预警",
        range(len(alerts)),
        format_func=lambda i: f"{alerts.iloc[i]['机构']} | {alerts.iloc[i]['车型']}",
    )
    context = build_alert_context(alerts.iloc[idx].to_dict(), configured_thresholds(), source_label=source_label)
    show_dataframe(pd.DataFrame(context.items(), columns=["系统依据", "值"]), hide_index=True)

    def show_meta(meta):
        with st.expander("引用的知识片段"):
            for hit in meta["hits"]:
                st.write(f"[{hit['cite']}] {hit['source']}｜{hit['section']}｜{hit['cos']}")
                st.text(hit["text"])
        if meta["invalid_citations"]:
            st.warning("有引用无法对应，请人工核对")
        if meta["untraced_numbers"]:
            st.warning("有数字无法溯源，请以系统计算结果为准")
        st.caption(f"用时：{meta['timing']}；模型：{rag.CHAT_MODEL}")

    if st.button("生成预警解读"):
        result = ask_advisor("请解释这条预警的原因并给出核查建议", context, json_mode=True)
        if result.get("error"):
            st.error(result["error"])
        else:
            data = result["json"]
            if data["refused"]:
                st.info("顾问拒答：请核对问题范围与依据")
            for label, key in [
                ("结论", "answer"),
                ("依据", "evidence"),
                ("建议", "suggestions"),
                ("待核查", "uncertainty"),
            ]:
                st.markdown("**" + label + "**")
                st.write(data[key])
        show_meta(result)
    key = f"advisor_history_{idx}_{source_label}"
    history = st.session_state.setdefault(key, [])
    for message in history:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    question = st.chat_input("输入你的问题……")
    if question:
        with st.chat_message("user"):
            st.write(question)
        with st.chat_message("assistant"):
            iterator, meta = stream_advisor(question, context, history=history)
            answer = st.write_stream(iterator)
            show_meta(meta)
        history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])


# 页面：处置跟踪
def render_cases_page():
    df, _ = get_usable_data()
    alerts = df[df["预警等级"].isin(["red", "orange", "yellow"])] if not df.empty else None
    render_cases(alerts)


# 页面：系统设置
def render_settings():
    page_title("系统设置", "版本信息 · 数据源登记 · 指标口径 · 通知配置")
    from src.notification.email_sender import is_smtp_available
    t = configured_thresholds()
    kpi_row([
        ("内部版本", theme.VERSION, "", "slate", "本地部署"),
        ("预警阈值", f"{t['yellow']:g} / {t['orange']:g} / {t['red']:g}", "%", "", "黄 / 橙 / 红，侧栏可调整"),
        ("邮件服务", "已配置" if is_smtp_available() else "未配置", "", "" if is_smtp_available() else "orange", "未配置时按模拟方式记录"),
    ])
    section("数据源登记")
    st.dataframe(pd.DataFrame([
        {"数据源": SOURCE_OFFICIAL, "文件": "data/official/brvehins_2011.xlsx", "发布机构": OFFICIAL_SOURCE, "用途": "默认明细监测（州 × 车型）"},
        {"数据源": "中国行业监测视图", "文件": "data/official/china/*.csv", "发布机构": CHINA_SOURCE, "用途": "行业对标（宏观与基准）"},
        {"数据源": SOURCE_UPLOAD, "文件": "侧栏上传 Excel / CSV", "发布机构": "机构内部报表", "用途": "机构 × 车型监测"},
    ]), hide_index=True, use_container_width=True)
    st.caption("每份数据的字段、许可与缺口见 docs/data_sources.md。")
    section("指标口径与预警规则")
    show_dataframe(metric_explanations(), hide_index=True, use_container_width=True)


# 主路由
PAGE_MAP = {
    "监测总览": render_home,
    "预警中心": render_alert_center,
    "归因分析": render_attribution,
    "数据质量": render_quality_page,
    "处置跟踪": render_cases_page,
    "预警通知": render_notification_center,
    "行业对标": render_industry,
    "AI 风控顾问": render_ai_advisor,
    "系统设置": render_settings,
}

if data_source == SOURCE_CHINA or page == "行业对标":
    theme.topbar("中国行业监测视图", "2020 – 2026.9", "国家统计局 / 金融监管总局 / 中国精算师协会")
elif data_source == SOURCE_UPLOAD:
    theme.topbar("机构上传数据", uploaded_file.name if uploaded_file else "待上传", "机构内部报表（本机处理，不出本机）")
else:
    theme.topbar("车险经营监测数据集", "2011 年度", "SUSEP AUTOSEG 公开统计 · CASdatasets")

DETAIL_PAGES = {"监测总览", "预警中心", "归因分析", "预警通知", "AI 风控顾问"}
if data_source == SOURCE_CHINA and page in DETAIL_PAGES:
    note("当前数据来源为“中国行业监测视图”，只含省级与全国公开统计，不含机构 × 车型明细，已切换为行业对标视图。"
         "如需查看明细预警，请在侧栏选择“车险经营监测数据集（SUSEP 2011）”或“上传机构数据”。")
    render_fn = render_industry
else:
    render_fn = PAGE_MAP.get(page)
if render_fn:
    render_fn()

if data_source == SOURCE_CHINA or page == "行业对标":
    theme.footer(CHINA_SOURCE, CHINA_SCOPE)
elif data_source == SOURCE_UPLOAD:
    theme.footer("机构上传报表", "以报表表头标注的单位与期间为准；合计行不参与预警判级与 DQ001 求和。")
else:
    theme.footer(OFFICIAL_SOURCE, OFFICIAL_SCOPE)

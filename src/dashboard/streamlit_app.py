"""车险业务智能监控与风险预警平台 — Streamlit 仪表盘

企业级浅色主题 · 清晰高对比设计语言 · 配置驱动规则引擎
默认加载 data/sample/demo_dataset.xlsx。
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

# 页面配置
st.set_page_config(
    page_title="IRDS · 车险智能风控平台",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 全局 CSS（浅色主题 · 高对比度设计语言）
GLOBAL_CSS = """
<style>
/* ---- Base ---- */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }

/* ---- Light application shell ---- */
html, body, [data-testid="stAppViewContainer"] { background: #ffffff; color: #1f2937; }
[data-testid="stHeader"] { background: rgba(255,255,255,0.96); }
section[data-testid="stSidebar"] { background: #f8fafc; }
section[data-testid="stSidebar"] * { color: #1f2937; }

/* ---- Card ---- */
.ds-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 24px 20px;
    text-align: center;
    transition: border-color 0.2s;
}
.ds-card:hover { border-color: #99f6e4; box-shadow: 0 4px 14px rgba(15,118,110,0.08); }
.ds-card .ds-card-value {
    font-size: 2.25rem;
    font-weight: 700;
    margin: 0 0 4px 0;
    line-height: 1.1;
    letter-spacing: -0.02em;
}
.ds-card .ds-card-label {
    font-size: 0.8rem;
    color: #64748b;
    margin: 0;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 500;
}

/* ---- Alert accent bar ---- */
.ds-card-red { border-top: 2px solid #f87171; }
.ds-card-orange { border-top: 2px solid #fb923c; }
.ds-card-amber { border-top: 2px solid #fbbf24; }
.ds-card-green { border-top: 2px solid #4ade80; }

/* ---- Section header ---- */
.ds-section-title {
    font-size: 1.05rem;
    font-weight: 600;
    color: #1f2937;
    margin-bottom: 16px;
    letter-spacing: -0.01em;
}

/* ---- Info cards ---- */
.ds-info-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 18px 20px;
}
.ds-info-card table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
}
.ds-info-card table td {
    padding: 6px 8px;
    vertical-align: top;
}
.ds-info-card table td:first-child {
    color: #64748b;
    width: 90px;
    font-weight: 500;
}

/* ---- Attribution result card ---- */
.ds-attribution-card {
    padding: 20px 24px;
    border-radius: 10px;
    border: 1px solid #e2e8f0;
    margin-bottom: 16px;
}
.ds-attribution-card h3 {
    margin: 0 0 12px 0;
    font-size: 1.1rem;
    font-weight: 600;
    color: #1f2937;
}

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {
    border-right: 1px solid #e2e8f0;
}
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stMarkdown p {
    color: #334155;
}
section[data-testid="stSidebar"] hr {
    border-color: #cbd5e1;
    margin: 16px 0;
}

/* ---- Main content area ---- */
.main .block-container { padding-top: 2rem; }

/* ---- Divider ---- */
hr { border-color: #e2e8f0 !important; }

/* ---- Dataframe overrides ---- */
[data-testid="stDataFrame"] {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    overflow: hidden;
}

/* ---- Expander ---- */
.streamlit-expanderHeader {
    font-weight: 500;
    color: #334155;
}
.streamlit-expanderContent {
    border: 1px solid #e2e8f0;
    border-radius: 0 0 8px 8px;
    padding: 16px;
}

/* ---- Metric overrides ---- */
[data-testid="stMetricValue"] { color: #1f2937; font-weight: 700; }
[data-testid="stMetricDelta"] { font-weight: 500; }

/* ---- Button ---- */
.stButton > button {
    background: #ffffff;
    color: #1f2937;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    font-weight: 500;
    transition: all 0.2s;
}
.stButton > button:hover {
    border-color: #0f766e;
    color: #0f766e;
}
.stButton > button[kind="primary"] {
    background: #0f766e;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
.stButton > button[kind="primary"]:hover {
    background: #14b8a6;
}

/* ---- Selectbox / Input ---- */
.stSelectbox > div > div,
.stTextInput > div > div {
    background: #ffffff;
    border-color: #cbd5e1;
    border-radius: 8px;
    color: #1f2937;
}
div[data-baseweb="select"] > div,
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div {
    background: #ffffff;
    border-color: #cbd5e1;
    color: #1f2937;
}

/* ---- Tabs (if any) ---- */
.stTabs [data-baseweb="tab"] { color: #64748b; }
.stTabs [aria-selected="true"] { color: #0f766e; }

/* 首页标题在浅色/黑白主题下保持足够对比度 */
div[data-testid="stMarkdownContainer"] .irds-home-title { color: #0f766e !important; }

/* 兼容页面中少量历史内联样式，确保浅色主题下正文和卡片文字可读。 */
[style*="#e4e6eb"], [style*="#cdd1d9"] { color: #1f2937 !important; }
[style*="#8892a4"] { color: #64748b !important; }
[style*="background:#1a1d2e"] { background: #ffffff !important; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# 数据加载层（缓存）
@st.cache_data
def load_demo_dataset() -> pd.DataFrame:
    path = _PROJECT_ROOT / "data" / "sample" / "demo_dataset.xlsx"
    if path.exists():
        return pd.read_excel(path, sheet_name="完整数据集")
    return pd.DataFrame()


@st.cache_data
def load_uploaded_dataset(uploaded_file, source_sheet=None) -> pd.DataFrame:
    return read_uploaded_data(uploaded_file, source_sheet)


@st.cache_data
def df_to_metric_cells(df: pd.DataFrame) -> list:
    from src.core.models import MetricCell
    cells = []
    col_map = {
        "保费占比": "保费占比", "签单保费_万元": "签单保费",
        "预期赔付率": "预期赔付率", "成本率1": "成本率1",
        "预期成本率": "预期成本率", "自主定价系数": "自主定价系数",
        "单均保费_元": "单均保费", "累计出险频度": "累计出险频度",
    }
    for _, row in df.iterrows():
        if row.get("车型") == "合计":
            continue
        branch = row.get("机构", "全辖")
        vehicle = row.get("车型", "")
        for col, metric_name in col_map.items():
            if col in row and pd.notna(row[col]):
                cells.append(MetricCell(
                    vehicle_type=str(vehicle), metric_name=metric_name,
                    value=float(row[col]), time_dimension="当日",
                    branch=str(branch),
                ))
    return cells


@st.cache_data
def get_dataset_stats(df: pd.DataFrame) -> dict:
    non_summary = df[df["车型"] != "合计"] if "车型" in df.columns else df
    return {
        "total_records": len(df),
        "branch_count": df["机构"].nunique() if "机构" in df.columns else 0,
        "vehicle_count": non_summary["车型"].nunique() if "车型" in df.columns else 0,
        "red_alerts": int((df.get("预警等级") == "red").sum()),
        "orange_alerts": int((df.get("预警等级") == "orange").sum()),
        "yellow_alerts": int((df.get("预警等级") == "yellow").sum()),
        "total_alerts": int(df.get("预警等级").isin(["yellow", "orange", "red"]).sum()),
    }


# 颜色常量（浅色主题调色板）
ACCENT = "#5eead4"        # teal-300 — 品牌主色
ACCENT_MUTED = "#0d9488"  # teal-600 — 按钮/重点

ALERT_COLORS = {
    "red": "#f87171",
    "orange": "#fb923c",
    "yellow": "#fbbf24",
    "normal": "#4ade80",
}
TEXT_MUTED = "#64748b"
PLOT_BG = "#ffffff"
PLOT_PAPER = "#ffffff"
PLOT_GRID = "#e2e8f0"
PLOT_FONT = "#334155"

# 统一的 Plotly 图表模板
PLOT_TEMPLATE = go.layout.Template()
PLOT_TEMPLATE.layout.update(
    paper_bgcolor=PLOT_PAPER,
    plot_bgcolor=PLOT_BG,
    font=dict(color=PLOT_FONT, size=12),
    xaxis=dict(gridcolor=PLOT_GRID, zerolinecolor=PLOT_GRID),
    yaxis=dict(gridcolor=PLOT_GRID, zerolinecolor=PLOT_GRID),
    title=dict(font=dict(color=PLOT_FONT, size=14)),
    legend=dict(font=dict(color=PLOT_FONT)),
    margin=dict(t=0, b=0, l=0, r=0),
)

ALERT_LABELS = {"red": "红色预警", "orange": "橙色预警", "yellow": "黄色预警", "normal": "正常"}

# 侧边栏
with st.sidebar:
    st.markdown("""
    <div style="padding:4px 0 16px 0;">
        <div style="font-size:1.15rem;font-weight:700;color:#1f2937;letter-spacing:-0.01em;">
            IRDS
        </div>
        <div style="font-size:0.75rem;color:#64748b;font-weight:500;">
            车险智能风控平台
        </div>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio(
        "导航",
        ["项目首页", "核心指标表", "风险预警中心", "归因分析中心", "AI建议原型", "预警通知中心", "数据上传与Sheet识别"],
        index=0,
        format_func=lambda x: {
            "项目首页": "项目首页",
            "核心指标表": "核心指标表",
            "风险预警中心": "风险预警中心",
            "归因分析中心": "归因分析中心",
            "AI建议原型": "AI建议原型",
            "预警通知中心": "预警通知中心",
            "数据上传与Sheet识别": "数据上传与报表识别",
        }[x],
    )

    st.divider()

    st.markdown(
        '<span style="font-size:0.75rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;">'
        '数据来源</span>',
        unsafe_allow_html=True,
    )
    data_source = st.radio(
        "选择数据源",
        ["使用Demo数据集（默认）", "上传Excel文件"],
        label_visibility="collapsed",
    )

    thresholds = configured_thresholds()
    yellow_threshold = st.number_input(
        "预期成本率黄色阈值（%）",
        min_value=0.0, max_value=200.0, value=float(thresholds["yellow"]), step=0.5,
        key="yellow_alert_threshold",
        help="该阈值同时用于预警等级、页面说明和邮件内容；红色/橙色阈值仍以 YAML 为准。",
    )
    st.caption(f"规则阈值：黄色 {yellow_threshold:g}% · 橙色 {thresholds['orange']:g}% · 红色 {thresholds['red']:g}%")

    st.divider()
    st.markdown(
        '<p style="font-size:0.7rem;color:#555;margin:0;">'
        '项目演示版 · 脱敏 Demo 数据</p>',
        unsafe_allow_html=True,
    )


# 通用数据加载
def get_usable_data() -> tuple[pd.DataFrame, str]:
    if data_source == "使用Demo数据集（默认）":
        df = load_demo_dataset()
        if df.empty:
            st.error("未找到默认演示数据，请检查 data/sample/demo_dataset.xlsx。")
            return pd.DataFrame(), ""
        return apply_alert_rules(df, st.session_state.get("yellow_alert_threshold")), "默认演示数据"
    else:
        uploaded = st.sidebar.file_uploader(
            "上传 Excel 或 CSV 文件", type=["xlsx", "csv"], key="main_upload",
            label_visibility="collapsed",
        )
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
                return apply_alert_rules(df, st.session_state.get("yellow_alert_threshold")), f"上传文件：{uploaded.name}"
        return pd.DataFrame(), ""




# 页面1: 项目首页
def render_home():
    st.markdown("""
    <div style="margin-bottom:4px;">
        <div class="irds-home-title" style="font-size:1.6rem;font-weight:700;letter-spacing:-0.02em;">
            车险智能风控平台
        </div>
        <div style="font-size:0.85rem;color:#64748b;font-weight:500;">
            车险业务智能监控与风险预警平台
        </div>
    </div>
    """, unsafe_allow_html=True)

    df, source_label = get_usable_data()

    if not df.empty:
        stats = get_dataset_stats(df)
        st.divider()

        # ---- 数据概览卡片 ----
        cols = st.columns(5)
        metrics = [
            (ACCENT, stats["total_records"], "记录总数", ""),
            (ACCENT, stats["branch_count"], "分支机构", ""),
            (ACCENT, stats["vehicle_count"], "监控车型", ""),
            (ALERT_COLORS["red"], stats["total_alerts"], "触发预警", "ds-card-red"),
            ("#8892a4", stats["total_records"] - stats["total_alerts"], "正常记录", "ds-card-green"),
        ]
        for col, (color, val, label, extra_class) in zip(cols, metrics):
            with col:
                st.markdown(f"""
                <div class="ds-card {extra_class}">
                    <div class="ds-card-value" style="color:{color};">{val}</div>
                    <div class="ds-card-label">{label}</div>
                </div>
                """, unsafe_allow_html=True)

        # ---- 风险等级分布图 ----
        st.divider()
        col_chart1, col_chart2 = st.columns(2)

        with col_chart1:
            st.markdown('<div class="ds-section-title">风险等级分布</div>', unsafe_allow_html=True)
            alert_dist = df["预警等级"].value_counts()
            dist_data = []
            for level in ["normal", "yellow", "orange", "red"]:
                count = int(alert_dist.get(level, 0))
                dist_data.append({"等级": ALERT_LABELS[level], "数量": count, "颜色": ALERT_COLORS[level]})
            fig_dist = px.pie(
                pd.DataFrame(dist_data), names="等级", values="数量",
                color="等级",
                color_discrete_map={ALERT_LABELS[k]: ALERT_COLORS[k] for k in ["normal", "yellow", "orange", "red"]},
                hole=0.45,
            )
            fig_dist.update_layout(template=PLOT_TEMPLATE, height=360)
            st.plotly_chart(fig_dist, use_container_width=True, config={"displayModeBar": False})

        with col_chart2:
            st.markdown('<div class="ds-section-title">预警等级构成</div>', unsafe_allow_html=True)
            alert_only = df[df["预警等级"] != "normal"]
            if not alert_only.empty:
                alert_counts = alert_only["预警等级"].value_counts()
                bar_data = []
                for level in ["yellow", "orange", "red"]:
                    bar_data.append({"等级": ALERT_LABELS[level], "数量": int(alert_counts.get(level, 0))})
                fig_bar = px.bar(
                    pd.DataFrame(bar_data), x="等级", y="数量",
                    color="等级",
                    color_discrete_map={
                        ALERT_LABELS["yellow"]: ALERT_COLORS["yellow"],
                        ALERT_LABELS["orange"]: ALERT_COLORS["orange"],
                        ALERT_LABELS["red"]: ALERT_COLORS["red"],
                    },
                    text="数量",
                )
                fig_bar.update_traces(textposition="outside", textfont_color=PLOT_FONT)
                fig_bar.update_layout(template=PLOT_TEMPLATE, height=360, showlegend=False)
                st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})
            else:
                st.info("当前没有预警数据。")

        st.caption(f"数据来源：{source_label}")

    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("支持报表", "9 类", delta="核心监控报表")
        with col2:
            st.metric("车型分类", "13+", delta="含细分车型")
        with col3:
            st.metric("预警规则", "5 条", delta="规则文件驱动")
        st.info("上传 Excel 文件后查看实时统计。")

    st.divider()

    # ---- 演示引导（新用户快速上手） ----
    with st.expander("演示引导 — 点击展开查看系统使用流程"):
        st.markdown("""
        **IRDS 车险智能风控平台** 将车险监控从「人工看报表」升级为「自动预警 + 智能归因 + 一键通知」。

        **操作流程：**

        | 步骤 | 页面 | 操作 | 你将看到 |
        |---|---|---|---|
        | ① 上传数据 | 数据上传 | 拖入 Excel 监控报表 | 自动识别报表结构 |
        | ② 查看指标 | 核心指标表 | 按机构、车型和等级筛选 | 异常行自动高亮 |
        | ③ 发现风险 | 风险预警中心 | 浏览预警列表和图表 | 从总体风险定位到机构和车型 |
        | ④ 分析原因 | 归因分析中心 | 点选预警记录 | 查看触发条件、归因结论和建议 |
        | ⑤ 发送通知 | 预警通知中心 | 选择预警并生成邮件 | 收件人可在前端修改 |

        **系统亮点：**
        - YAML 配置驱动：预警阈值、负责人邮箱、通知规则均可无代码修改
        - 浅色高对比界面：适合运营监控场景
        - 支持模拟发送和 SMTP 邮件发送
        """)

    # ---- 项目介绍 ----
    col_left, col_right = st.columns([3, 2])
    with col_left:
        st.markdown("""
        ### 系统说明

        系统面向车险经营管理场景，将 Excel 监控报表接入平台，经过指标计算和规则判断形成预警，再通过归因分析解释异常原因，并生成通知内容。

        | 功能 | 说明 |
        |---|---|
        | 数据解析 | 自动识别多类监控报表，保留原始记录和解析状态 |
        | 指标计算 | 计算预期成本率、出险频度等核心指标 |
        | 规则预警 | 根据可配置阈值自动生成预警等级 |
        | 归因分析 | 展示原始数据、触发条件、归因结论和整改建议 |
        | 通知分发 | 生成邮件预览，收件人和抄送可在前端修改 |
        """)

    with col_right:
        st.markdown("### 核心公式")
        st.latex(r"\text{Expected Cost Ratio} = \text{Expected Loss Ratio} + \text{Cost Ratio 1}")
        st.latex(r"\text{Claim Frequency} = \frac{\text{Resolved Claims (incl. RBNR)}}{\text{Policies In Force}}")
        st.latex(r"\text{Avg Premium} = \frac{\text{Signed Premium}}{\text{Policies In Force}}")
        st.latex(r"\text{Premium Share} = \frac{\text{Vehicle-type Signed Premium}}{\text{Total Signed Premium}}")

        st.markdown("### 预警阈值")
        threshold = st.session_state.get("yellow_alert_threshold", configured_thresholds()["yellow"])
        configured = configured_thresholds()
        st.markdown(f"""
        | Level | Expected Cost Ratio |
        |---|---|
        | <span style="color:#4ade80;">Normal</span> | ≤ {threshold:g}% |
        | <span style="color:#fbbf24;">Yellow</span> | &gt; {threshold:g}% |
        | <span style="color:#fb923c;">Orange</span> | {configured['orange']:g}%（当前 YAML 未启用独立橙色规则） |
        | <span style="color:#f87171;">Red</span> | &gt; {configured['red']:g}% |
        """, unsafe_allow_html=True)


# 页面2: 核心指标表
def render_metrics_table():
    st.markdown("""
    <div style="margin-bottom:16px;">
        <div style="font-size:1.3rem;font-weight:700;color:#1f2937;letter-spacing:-0.02em;">核心指标表</div>
    </div>
    """, unsafe_allow_html=True)

    df, source_label = get_usable_data()
    if df.empty:
        st.warning("请选择数据源以查看指标表。")
        return

    render_history_panel(df, source_label)

    st.success(f"来源：{source_label}｜{len(df)} 条记录｜预警等级由规则引擎自动计算")

    col_filter1, col_filter2, col_filter3 = st.columns(3)
    with col_filter1:
        branches = ["全部"] + sorted(df["机构"].unique().tolist())
        sel_branch = st.selectbox("机构", branches)
    with col_filter2:
        levels = ["全部"] + sorted(df["预警等级"].unique().tolist())
        sel_level = st.selectbox("预警等级", levels)
    with col_filter3:
        search = st.text_input("车型搜索", placeholder="输入车型关键词...")

    view = df.copy()
    if sel_branch != "全部":
        view = view[view["机构"] == sel_branch]
    if sel_level != "全部":
        view = view[view["预警等级"] == sel_level]
    if search:
        view = view[view["车型"].str.contains(search, case=False, na=False)]

    st.caption(f"显示 {len(view)} / {len(df)} 条记录")

    def highlight_rows(row):
        level = row.get("预警等级", "normal")
        if level == "red":
            return ["background-color: rgba(248,113,113,0.12)" for _ in row.index]
        elif level == "orange":
            return ["background-color: rgba(251,146,60,0.10)" for _ in row.index]
        elif level == "yellow":
            return ["background-color: rgba(251,191,36,0.08)" for _ in row.index]
        return [""] * len(row.index)

    display_cols = [
        "机构", "车型", "保费占比", "预期赔付率", "成本率1",
        "预期成本率", "累计出险频度", "单均保费_元", "预警等级",
    ]
    available_cols = [c for c in display_cols if c in view.columns]
    styled = view[available_cols].style.apply(highlight_rows, axis=1)
    st.dataframe(styled, use_container_width=True, height=500)

    with st.expander("数据质量校验"):
        tolerance = st.number_input("保费占比容忍误差（百分点）", 0.0, 10.0, 1.0, 0.1, key="premium_tolerance")
        checks = validation_summary(df, tolerance)
        if checks.empty:
            st.info("当前单表没有可展示的 DQ001 结果；DQ003 默认关闭，DQ002 需同时提供 T2 与 T7。")
        else:
            st.dataframe(checks, use_container_width=True, hide_index=True)
            details = quality_details(df, checks)
            if not details.empty:
                st.markdown("**异常机构参与求和的原始明细**")
                st.dataframe(details, use_container_width=True, hide_index=True)
                st.download_button("下载校验异常明细", details.to_csv(index=False).encode("utf-8-sig"), "数据质量异常.csv", "text/csv")
        st.caption("DQ001 只汇总主表明细，不重复加入细分表或合计行；±1个百分点暂为参考设置。DQ002 只在同时具备 T2 与 T7 时执行。")


# 页面3: 风险预警中心
def render_alert_center():
    render_batch_status()
    yellow_threshold = st.session_state.get("yellow_alert_threshold", configured_thresholds()["yellow"])
    st.markdown("""
    <div style="margin-bottom:16px;">
        <div style="font-size:1.3rem;font-weight:700;color:#1f2937;letter-spacing:-0.02em;">风险预警中心</div>
    </div>
    """, unsafe_allow_html=True)

    df, source_label = get_usable_data()
    if df.empty:
        st.warning("请选择数据源以查看预警。")
        return

    stats = get_dataset_stats(df)

    # ---- 预警摘要卡片 ----
    cols = st.columns(4)
    alert_cards = [
        ("红色预警", stats["red_alerts"], "ds-card-red", ALERT_COLORS["red"]),
        ("橙色预警", stats["orange_alerts"], "ds-card-orange", ALERT_COLORS["orange"]),
        ("黄色预警", stats["yellow_alerts"], "ds-card-amber", ALERT_COLORS["yellow"]),
        ("正常记录", stats["total_records"] - stats["total_alerts"], "ds-card-green", ALERT_COLORS["normal"]),
    ]
    for col, (label, val, extra, color) in zip(cols, alert_cards):
        with col:
            st.markdown(f"""
            <div class="ds-card {extra}">
                <div class="ds-card-value" style="color:{color};">{val}</div>
                <div class="ds-card-label">{label}</div>
            </div>
            """, unsafe_allow_html=True)

    st.divider()

    alert_df = df[df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    if alert_df.empty:
        st.success("当前所有指标均在正常范围内。")
        return

    level_order = {"red": 0, "orange": 1, "yellow": 2}
    alert_df["_sort"] = alert_df["预警等级"].map(level_order)
    alert_df = alert_df.sort_values(["_sort", "预期成本率"], ascending=[True, False])

    display_cols = [
        "机构", "车型", "预期赔付率", "成本率1", "预期成本率",
        "累计出险频度", "单均保费_元", "预警等级",
    ]
    available_cols = [c for c in display_cols if c in alert_df.columns]

    def highlight_alert_rows(row):
        level = row.get("预警等级", "normal")
        if level == "red":
            return ["background-color: rgba(248,113,113,0.15)"] * len(row.index)
        elif level == "orange":
            return ["background-color: rgba(251,146,60,0.12)"] * len(row.index)
        elif level == "yellow":
            return ["background-color: rgba(251,191,36,0.10)"] * len(row.index)
        return [""] * len(row.index)

    st.markdown('<div class="ds-section-title">预警明细</div>', unsafe_allow_html=True)
    styled = alert_df[available_cols].style.apply(highlight_alert_rows, axis=1)
    st.dataframe(styled, use_container_width=True, height=400)

    from src.dashboard.batch_panel import render_batch_panel
    render_batch_panel(alert_df, yellow_threshold)

    # ---- 图表区 ----
    st.divider()
    st.markdown('<div class="ds-section-title">风险分析图表</div>', unsafe_allow_html=True)

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.markdown("**机构预警排行**")
        alert_by_branch = alert_df.groupby("机构").size().sort_values(ascending=False).reset_index(name="数量")
        fig_branch = px.bar(
            alert_by_branch.head(10), x="机构", y="数量",
            color="数量", color_continuous_scale="Reds", text="数量",
        )
        fig_branch.update_traces(textposition="outside", textfont_color=PLOT_FONT, cliponaxis=False)
        fig_branch.update_layout(
            template=PLOT_TEMPLATE, height=380,
            xaxis_title="", yaxis_title="预警数量", yaxis_range=[0, max(1, float(alert_by_branch['数量'].max())) * 1.25], margin=dict(t=40),
        )
        st.plotly_chart(fig_branch, use_container_width=True, config={"displayModeBar": False})

    with col_chart2:
        st.markdown("**车型预警排行**")
        alert_by_vehicle = (
            alert_df[alert_df["车型"] != "合计"].groupby("车型")
            .size().sort_values(ascending=False).reset_index(name="数量")
        )
        fig_vehicle = px.bar(
            alert_by_vehicle.head(10), x="车型", y="数量",
            color="数量", color_continuous_scale="Oranges", text="数量",
        )
        fig_vehicle.update_traces(textposition="outside", textfont_color=PLOT_FONT, cliponaxis=False)
        fig_vehicle.update_layout(
            template=PLOT_TEMPLATE, height=380,
            xaxis_title="", yaxis_title="预警数量", yaxis_range=[0, max(1, float(alert_by_vehicle['数量'].max())) * 1.25], margin=dict(t=40),
        )
        st.plotly_chart(fig_vehicle, use_container_width=True, config={"displayModeBar": False})

    # ---- 重点机构 × 车型 ----
    # 热力图在机构和车型较多时容易出现大面积同色，打印和录屏都不利于阅读。
    # 这里改用排序表，保留同样的定位信息，并直接展示机构、车型和核心指标。
    st.divider()
    st.markdown('<div class="ds-section-title">重点机构与车型风险明细</div>', unsafe_allow_html=True)
    focus_cols = [
        "机构", "车型", "预期成本率", "预期赔付率", "成本率1", "预警等级",
    ]
    focus_view = alert_df[alert_df["车型"] != "合计"].copy()
    focus_view = focus_view.sort_values("预期成本率", ascending=False)
    focus_view = focus_view[[c for c in focus_cols if c in focus_view.columns]].head(15)
    st.dataframe(focus_view, use_container_width=True, hide_index=True, height=430)
    st.caption("按预期成本率从高到低展示重点机构与车型组合，便于在黑白打印和小屏录制中核对。")

    st.caption(f"数据来源：{source_label}")
    st.session_state["alert_df"] = alert_df
    st.session_state["full_df"] = df


# 页面4: 归因分析中心
def render_attribution():
    st.markdown("""
    <div style="margin-bottom:16px;">
        <div style="font-size:1.3rem;font-weight:700;color:#1f2937;letter-spacing:-0.02em;">归因分析中心</div>
    </div>
    """, unsafe_allow_html=True)

    df, source_label = get_usable_data()
    if df.empty:
        st.warning("请选择数据源以进行归因分析。")
        return

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
        st.caption("25% 和 2000 元暂为演示参考值，未找到已确认的业务口径；可按调研结论调整，不据此宣称同比恶化。")
    yellow_threshold = st.session_state.get("yellow_alert_threshold", configured_thresholds()["yellow"])
    result = analyze(selected, df, yellow_threshold=yellow_threshold,
                     frequency_threshold=frequency_limit, premium_threshold=premium_limit)

    # 先展示输入证据，再展示解释，避免将规则推测误认为原始事实。
    from src.attribution.evidence import condition_evidence
    st.subheader("原始输入数据")
    st.caption(f"来源：{source_label}；以下为当前加载表的完整记录，非保单原件。预警等级由当前规则引擎重新计算。")
    st.dataframe(selected.to_frame().T, use_container_width=True, hide_index=True)
    st.download_button("下载当前记录 CSV", selected.to_frame().T.to_csv(index=False).encode("utf-8-sig"),
                       file_name="attribution_input.csv", mime="text/csv")
    st.subheader("触发条件与归因依据")
    evidence_rows = []
    category = _match_vehicle_type(str(selected.get("车型", "")))
    for rule in get_rules().get("attribution_rules", {}).get(category, []):
        for condition in rule.get("conditions", []):
            evidence = condition_evidence(condition, selected)
            evidence.pop("matched")
            evidence_rows.append({"规则": rule["id"], **evidence})
    if evidence_rows:
        st.dataframe(pd.DataFrame(evidence_rows), use_container_width=True, hide_index=True)
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
                    f'<div style="background:rgba(94,234,212,0.06);border:1px solid rgba(94,234,212,0.12);'
                    f'border-radius:6px;padding:6px 12px;font-size:0.78rem;color:#334155;">'
                    f'<strong style="color:{ACCENT};">{d["name"]}</strong>'
                    f'<br><span style="font-size:0.7rem;color:{TEXT_MUTED};">{d["detail"][:60]}...</span></div>'
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
        st.dataframe(pd.DataFrame(items_data), use_container_width=True, hide_index=True)

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
                <div style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.12);
                            border-radius:8px;padding:14px;margin-bottom:10px;">
                    <strong style="color:#fbbf24;">[{rule['rule_id']}]</strong>
                    <span style="color:#334155;font-size:0.85rem;">{rule['conclusion']}</span>
                </div>
                """, unsafe_allow_html=True)

        # 三要素联动分析
        if result.tri_factor_analysis:
            tf = result.tri_factor_analysis
            sev_color = {"高": "#f87171", "中": "#fbbf24", "极高": "#f87171"}.get(tf.get("severity", ""), "#fbbf24")
            st.markdown(f"""
            <div style="background:rgba(248,113,113,0.06);border:1px solid rgba(248,113,113,0.15);
                        border-radius:8px;padding:14px;margin-bottom:10px;">
                <strong style="color:{sev_color};">三要素联动分析 · 严重度:{tf.get('severity','-')}</strong>
                <p style="color:#334155;font-size:0.85rem;margin:6px 0 0;">{tf.get('summary','')}</p>
                <p style="color:{TEXT_MUTED};font-size:0.8rem;margin:4px 0 0;">{tf.get('detail','')[:180]}</p>
            </div>
            """, unsafe_allow_html=True)

    # ---- 右侧 ----
    with col_right:
        st.markdown('<div class="ds-section-title">预警详情</div>', unsafe_allow_html=True)
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
            <div style="background:rgba(74,222,128,0.08);border:1px solid rgba(74,222,128,0.15);
                        border-radius:8px;padding:14px;font-size:0.8rem;color:#334155;">
                <strong style="color:#4ade80;">正常分支基准</strong> ({len(same_vehicle)} 个分支，{peer_method})<br/>
                预期成本率: <strong style="color:#4ade80;">{avg_normal_cost:.1f}%</strong>
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
    st.markdown("""
    <div style="margin-bottom:16px;">
        <div style="font-size:1.3rem;font-weight:700;color:#1f2937;letter-spacing:-0.02em;">数据上传与报表识别</div>
    </div>
    """, unsafe_allow_html=True)

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

    if data_source == "上传Excel文件":
        uploaded = st.sidebar.file_uploader(
            "上传 Excel 或 CSV 文件", type=["xlsx", "csv"], key="main_upload",
            label_visibility="collapsed",
        )
        if uploaded:
            if uploaded.name.lower().endswith(".csv"):
                st.info("CSV 为单表输入，仅上传单表时，不触发跨表一致性校验。")
                try:
                    st.dataframe(read_uploaded_data(uploaded), hide_index=True, use_container_width=True)
                except ValueError as exc:
                    st.error(str(exc))
                return
            import openpyxl
            try:
                wb = openpyxl.load_workbook(uploaded, data_only=True)
                source_label = f"上传文件：{uploaded.name}"
            except Exception as e:
                logger.exception("读取上传报表结构失败")
                st.error(f"文件读取失败：{e}")
    else:
        st.info(
            "**默认演示数据模式**：系统已加载 `data/sample/demo_dataset.xlsx` "
            "（361 条记录、25 家机构、22 种车型）。可前往核心指标表或风险预警中心查看。"
        )
        st.divider()
        st.markdown('<div class="ds-section-title">支持的报表类型（9 类）</div>', unsafe_allow_html=True)

        st.dataframe(
            pd.DataFrame(sheet_info, columns=["编号", "报表名称", "时间口径", "解析状态"]),
            use_container_width=True, hide_index=True,
        )
        return

    if wb is None:
        st.info("请上传 Excel 文件以开始分析。")
        st.divider()
        st.dataframe(
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
    st.dataframe(pd.DataFrame(overview_data), use_container_width=True, hide_index=True)

    st.markdown('<div class="ds-section-title">跨表数据质量校验 / Cross-report Check</div>', unsafe_allow_html=True)
    inputs, dq002_result = run_dq002(wb)
    if inputs.ready:
        st.caption("DQ002：只比较 T2 的“签单保费-当月”与 T7 的“月签单保费”；抽取不到这两个明确时间口径时不会臆造结论。")
        st.dataframe(dq002_result, use_container_width=True, hide_index=True)
    else:
        st.info("仅上传单表时，不触发跨表一致性校验。DQ002 需要同口径的 T2 当月签单保费和 T7 月签单保费。")
    for note in inputs.notes:
        st.caption(f"校验说明：{note}")

    st.markdown('<div class="ds-section-title">报表详情</div>', unsafe_allow_html=True)
    for meta in sheet_metas:
        status_icon = "✓" if meta.is_parseable else "○"
        with st.expander(f"{status_icon} {meta.sheet_name} ({meta.row_count} rows x {meta.col_count} cols)"):
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
                        st.dataframe(block, hide_index=True, use_container_width=True)
            # 非 T1/T2 表不强行套用核心指标规则，但允许用户查看前几行，
            # 这样“结构识别”和“可用数据预览”是两个清晰边界。
            if meta.report_type.value not in ("T1", "T2"):
                preview = pd.read_excel(uploaded, sheet_name=meta.sheet_name, header=None, nrows=12)
                st.caption("该报表当前为结构识别模式，以下为原始前 12 行预览。")
                st.dataframe(preview, use_container_width=True, hide_index=True)


# 预警通知中心
def render_notification_center():
    # SMTP 状态（提前定义，f-string 要用）
    from src.notification.email_sender import is_smtp_available
    smtp_available = is_smtp_available()
    mode_badge = (
        '<span style="display:inline-block;background:rgba(74,222,128,0.12);color:#4ade80;'
        'border:1px solid rgba(74,222,128,0.2);border-radius:6px;padding:2px 10px;'
        'font-size:0.75rem;font-weight:600;">SMTP 实时</span>'
        if smtp_available else
        '<span style="display:inline-block;background:rgba(251,191,36,0.10);color:#fbbf24;'
        'border:1px solid rgba(251,191,36,0.15);border-radius:6px;padding:2px 10px;'
        'font-size:0.75rem;font-weight:600;">模拟模式</span>'
    )
    st.markdown(f"""
    <div style="margin-bottom:4px;">
        <div style="font-size:1.3rem;font-weight:700;color:#1f2937;letter-spacing:-0.02em;">预警通知中心</div>
        <div style="font-size:0.8rem;color:#64748b;">预警分发 · 收件人匹配 · 邮件生成 · {mode_badge}</div>
    </div>
    """, unsafe_allow_html=True)

    df, source_label = get_usable_data()
    if df.empty:
        st.warning("请选择数据来源。")
        return

    alert_df = df[df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    if alert_df.empty:
        st.success("当前没有预警，无需发送通知。")
        return

    from src.notification.recipient_mapper import (
        format_recipient_list, get_notification_context,
    )
    from src.notification.email_templates import generate_html_body, generate_subject
    from src.notification.email_sender import clear_send_history, get_send_history, send_email

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown('<div class="ds-section-title">选择预警记录</div>', unsafe_allow_html=True)

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
            st.subheader("收件邮箱")
            manual_to = st.text_area("收件人", key="nc_manual_to")
            manual_cc = st.text_area("抄送（可选）", key="nc_manual_cc")
            address_error = ""
            try:
                edited_to = parse_addresses(manual_to)
                edited_cc = [a for a in parse_addresses(manual_cc)
                             if a.lower() not in {x.lower() for x in edited_to}]
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
            ] if is_loss_driven else [
                "检查该车型的市场费用率",
                "评估渠道费用政策调整空间",
                "对比同类机构的费用率",
            ]
        )
        if selected.get("累计出险频度", 0) > 25:
            suggestions.append(f"出险频度 {selected['累计出险频度']:.1f}% 偏高，建议复核核保质量")

        subject = generate_subject(selected, threshold=yellow_threshold)
        html_body = generate_html_body(
            selected, context=ctx, attribution_summary=attribution_summary, suggestions=suggestions,
            threshold=yellow_threshold,
        )

        st.divider()
        st.markdown('<div class="ds-section-title">邮件预览</div>', unsafe_allow_html=True)

        st.markdown(f"""
        <div style="background:rgba(94,234,212,0.06);border:1px solid rgba(94,234,212,0.12);
                    border-radius:8px;padding:14px;margin-bottom:12px;">
            <strong style="color:#334155;font-size:0.8rem;">邮件主题</strong><br/>
            <span style="font-size:0.9rem;color:#1f2937;">{subject}</span>
        </div>
        """, unsafe_allow_html=True)

        with st.container(border=True):
            st.components.v1.html(html_body, height=700, scrolling=True)

    with col_right:
        st.markdown('<div class="ds-section-title">收件人</div>', unsafe_allow_html=True)

        recipients_data = format_recipient_list(ctx)
        role_labels = {
            "branch_manager": "机构负责人",
            "deputy_gm": "分管负责人",
            "hq_operations": "总部运营",
            "hq_director": "总部负责人",
        }
        for rd in recipients_data:
            role_display = role_labels.get(rd["角色"], rd["角色"])
            st.markdown(f"""
            <div class="ds-info-card" style="margin-bottom:8px;padding:12px 14px;">
                <strong style="color:#1f2937;">{rd['姓名']}</strong><br/>
                <span style="font-size:0.75rem;color:{TEXT_MUTED};">{role_display} — {rd['职务']}</span><br/>
                <span style="font-size:0.75rem;color:{ACCENT};">{rd['邮箱']}</span>
            </div>
            """, unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="ds-section-title">通知规则</div>', unsafe_allow_html=True)

        st.markdown(f"""
        <div style="background:rgba(251,146,60,0.08);border:1px solid rgba(251,146,60,0.15);
                    border-radius:8px;padding:14px;font-size:0.8rem;color:#334155;">
            <strong style="color:#fb923c;">{ctx.escalation_description}</strong><br/><br/>
            <strong>优先级：</strong>{ctx.priority}<br/>
            <strong>响应时限：</strong>{ctx.response_hours} 小时内<br/>
            <strong>涉及部门：</strong>{ctx.department}
        </div>
        """, unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="ds-section-title">发送操作</div>', unsafe_allow_html=True)

        if st.button("发送邮件", type="primary", use_container_width=True,
                     disabled=bool(address_error)):
            with st.spinner("正在发送……"):
                record = send_email(
                    subject=subject, body=html_body,
                    to_list=edited_to, cc_list=edited_cc, context=ctx,
                )
            if record.status == "success":
                st.success(f"邮件发送成功！({record.mode.upper()})")
                st.markdown(f"""
                **发送编号**：`{record.send_id}`
                **时间**：{record.timestamp}
                **收件人**：{', '.join(record.recipients)}
                **模式**：{record.mode.upper()}
                **状态**：成功
                """)
            else:
                st.error(f"Send failed ({record.mode.upper()}): {record.error_message}")

        if st.button("清空发送记录", use_container_width=True):
            clear_send_history()
            st.rerun()

    st.divider()
    st.markdown('<div class="ds-section-title">发送记录</div>', unsafe_allow_html=True)

    history = get_send_history()
    if history:
        history_data = []
        for h in history:
            history_data.append({
                "发送编号": h.send_id, "时间": h.timestamp,
                "模式": h.mode.upper(),
                "主题": h.subject[:60] + ("..." if len(h.subject) > 60 else ""),
                "收件人数": len(h.recipients),
                "状态": "成功" if h.status == "success" else f"失败：{h.error_message}",
            })
        st.dataframe(pd.DataFrame(history_data), use_container_width=True, hide_index=True)
    else:
        st.info("暂无发送记录。选择预警并填写收件邮箱后即可生成模拟发送记录。")

    st.caption(f"数据来源：{source_label}")


# AI建议原型
def render_ai_advisor():
    st.markdown("<div style='font-size:1.3rem;font-weight:700;margin-bottom:16px;'>AI建议原型</div>", unsafe_allow_html=True)
    st.info("本页只让本地 4B 模型解释规则结果并生成建议；预警等级仍由 YAML 规则引擎决定。")
    df, source_label = get_usable_data()
    if df.empty:
        st.warning("请选择数据源以进行 AI 建议分析。")
        return
    alerts = df[df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    if alerts.empty:
        st.success("当前没有预警记录。")
        return
    options = [f"{r['机构']} | {r['车型']} | 预期成本率={r['预期成本率']:.1f}% | {ALERT_LABELS[r['预警等级']]}" for _, r in alerts.iterrows()]
    idx = st.selectbox("选择一条规则预警", range(len(options)), format_func=lambda i: options[i], key="ai_alert_select")
    row = alerts.iloc[idx]
    thresholds = configured_thresholds()
    yellow_threshold = st.session_state.get("yellow_alert_threshold", thresholds["yellow"])
    rule_text = (
        f"当前预警等级={ALERT_LABELS[row['预警等级']]}；预期成本率={row['预期成本率']:.1f}%；"
        f"黄色阈值={yellow_threshold:g}%；红色阈值={thresholds['red']:g}%。"
    )
    record = row.to_dict()
    st.dataframe(row.to_frame().T, use_container_width=True, hide_index=True)
    from src.llm_advisor import chat
    chat_key = f"ai_chat_{idx}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []
    st.subheader("直接咨询本地模型")
    st.caption("你可以直接提问，例如：为什么触发预警？我应该先检查什么？这个建议有哪些不确定性？")
    for item in st.session_state[chat_key]:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
    question = st.chat_input("输入你的问题……", key=f"ai_question_{idx}")
    if question:
        st.session_state[chat_key].append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("本地模型思考中……"):
                answer = chat(question, record, rule_text)
            st.markdown(answer)
        st.session_state[chat_key].append({"role": "assistant", "content": answer})
    st.divider()
    st.caption("也可以使用下面的结构化建议按钮；对话模式不要求模型返回 JSON。")
    if st.button("调用本地 qwen3.5:4b 生成建议", type="primary"):
        from src.llm_advisor import analyze
        with st.spinner("正在调用本机模型……"):
            result = analyze(record, rule_text)
        if "error" in result:
            st.error(result["error"])
            if result.get("raw"):
                st.caption("以下是模型原文，未通过 JSON 校验；不会写入规则结果或发送通知。")
                st.code(result["raw"])
            return
        st.subheader("模型建议")
        st.write(result["risk_summary"])
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**证据**")
            for item in result["evidence"]:
                st.markdown(f"- {item}")
            st.markdown("**建议**")
            for item in result["suggestions"]:
                st.markdown(f"- {item}")
        with col2:
            st.markdown("**不确定性 / 待核查**")
            for item in result["uncertainty"]:
                st.markdown(f"- {item}")
        st.caption(f"数据来源：{source_label}；模型输出仅供辅助，规则结果不由模型改写。")


# 主路由
PAGE_MAP = {
    "项目首页": render_home,
    "核心指标表": render_metrics_table,
    "风险预警中心": render_alert_center,
    "归因分析中心": render_attribution,
    "AI建议原型": render_ai_advisor,
    "预警通知中心": render_notification_center,
    "数据上传与Sheet识别": render_upload_and_sheets,
}

render_fn = PAGE_MAP.get(page)
if render_fn:
    render_fn()

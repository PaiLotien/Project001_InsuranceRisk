"""全站视觉规范：配色、字体、页头页脚与 KPI 卡片。

所有样式均为本地 CSS，不依赖外部字体或 CDN。自定义组件自带背景色和文字色，
浅色与深色主题下都保持可读。
"""

from html import escape

import plotly.graph_objects as go
import streamlit as st

PRODUCT_NAME = "车险智能风控平台"
PRODUCT_SUBTITLE = "IRDS · Insurance Risk Dynamic Surveillance"
VERSION = "2026.11"

# 品牌色：深石板蓝 + 墨绿
BRAND = "#12313f"
BRAND_ACCENT = "#0f766e"
INK = "#1f2a37"
MUTED = "#5b6b7b"
LINE = "#dfe5eb"
SURFACE = "#ffffff"

LEVEL_COLORS = {"red": "#c0392b", "orange": "#d9822b", "yellow": "#c9a227", "normal": "#2f855a"}
LEVEL_TINTS = {
    "red": "rgba(192,57,43,0.10)",
    "orange": "rgba(217,130,43,0.10)",
    "yellow": "rgba(201,162,39,0.10)",
    "normal": "",
}
LEVEL_LABELS = {"red": "红色", "orange": "橙色", "yellow": "黄色", "normal": "正常"}

FONT_STACK = '"Source Han Sans SC","Noto Sans SC","PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif'

GLOBAL_CSS = f"""
<style>
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select {{
    font-family: {FONT_STACK};
}}
[data-testid="stMetricValue"], .irds-num, td, .irds-kpi-value {{ font-variant-numeric: tabular-nums; }}

/* 隐藏开发工具栏、部署按钮与水印 */
#MainMenu, footer, [data-testid="stAppDeployButton"], [data-testid="stMainMenu"],
[data-testid="stToolbarActions"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display: none !important; }}
[data-testid="stHeader"] {{ background: transparent; height: 2.2rem; }}
.block-container {{ padding-top: 1.4rem !important; padding-bottom: 2rem; max-width: 1480px; }}

/* 页头 */
.irds-topbar {{
    background: {BRAND}; color: #ffffff; border-radius: 6px; padding: 18px 26px 16px;
    border-bottom: 3px solid {BRAND_ACCENT}; margin-bottom: 18px;
    display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; flex-wrap: wrap;
}}
.irds-topbar .irds-brand-title {{ font-size: 1.55rem; font-weight: 700; letter-spacing: .02em; color: #ffffff; line-height: 1.25; }}
.irds-topbar .irds-brand-sub {{ font-size: .82rem; color: #b9cdd6; letter-spacing: .06em; margin-top: 4px; }}
.irds-topbar .irds-meta {{ text-align: right; font-size: .8rem; color: #d7e3e8; line-height: 1.7; }}
.irds-topbar .irds-chip {{
    display: inline-block; background: rgba(255,255,255,.10); border: 1px solid rgba(255,255,255,.22);
    color: #ffffff; border-radius: 3px; padding: 1px 10px; font-size: .78rem; margin-left: 6px; white-space: nowrap;
}}

/* 页面标题 */
.irds-page-title {{ display:flex; align-items:baseline; gap:14px; margin: 4px 0 14px; padding-bottom: 10px; border-bottom: 1px solid {LINE}; }}
.irds-page-title h2 {{ font-size: 1.32rem; font-weight: 700; margin: 0; padding: 0; color: inherit; }}
.irds-page-title span {{ font-size: .84rem; color: {MUTED}; }}
.irds-section {{ font-size: 1.02rem; font-weight: 650; margin: 18px 0 10px; padding-left: 10px; border-left: 3px solid {BRAND_ACCENT}; line-height: 1.3; }}

/* KPI 卡片 */
.irds-kpi {{
    background: {SURFACE}; border: 1px solid {LINE}; border-top: 3px solid {BRAND_ACCENT}; border-radius: 6px;
    padding: 14px 16px 12px; min-height: 104px; box-shadow: 0 1px 2px rgba(16,40,52,.05);
}}
.irds-kpi .irds-kpi-label {{ font-size: .8rem; color: {MUTED}; font-weight: 500; letter-spacing: .02em; white-space: normal; }}
.irds-kpi .irds-kpi-value {{ font-size: 1.9rem; font-weight: 700; color: {INK}; line-height: 1.2; margin-top: 6px; }}
.irds-kpi .irds-kpi-unit {{ font-size: .85rem; font-weight: 500; color: {MUTED}; margin-left: 4px; }}
.irds-kpi .irds-kpi-note {{ font-size: .74rem; color: {MUTED}; margin-top: 4px; }}
.irds-kpi.tone-red {{ border-top-color: {LEVEL_COLORS['red']}; }}
.irds-kpi.tone-red .irds-kpi-value {{ color: {LEVEL_COLORS['red']}; }}
.irds-kpi.tone-orange {{ border-top-color: {LEVEL_COLORS['orange']}; }}
.irds-kpi.tone-orange .irds-kpi-value {{ color: {LEVEL_COLORS['orange']}; }}
.irds-kpi.tone-yellow {{ border-top-color: {LEVEL_COLORS['yellow']}; }}
.irds-kpi.tone-yellow .irds-kpi-value {{ color: #9a7b12; }}
.irds-kpi.tone-slate {{ border-top-color: #64748b; }}

/* 信息卡与提示条 */
.irds-card {{ background:{SURFACE}; color:{INK}; border:1px solid {LINE}; border-radius:6px; padding:14px 18px; margin-bottom:12px; }}
.irds-card table {{ width:100%; border-collapse:collapse; font-size:.88rem; color:{INK}; }}
.irds-card td {{ padding:5px 6px; vertical-align:top; border-bottom:1px solid #eef2f5; }}
.irds-card td:first-child {{ color:{MUTED}; width:110px; white-space:nowrap; }}
.irds-card h4 {{ margin:0 0 8px; font-size:1rem; color:{INK}; }}
.irds-card p {{ margin: 4px 0; color:{INK}; font-size:.88rem; line-height:1.65; }}
.irds-note {{ background:#f3f6f8; color:#3b4a58; border-left:3px solid #8aa1ae; border-radius:3px; padding:8px 12px; font-size:.8rem; line-height:1.6; margin:8px 0; }}
.irds-badge {{ display:inline-block; border-radius:3px; padding:1px 8px; font-size:.76rem; font-weight:600; color:#fff; }}

/* 侧栏 */
section[data-testid="stSidebar"] {{ border-right: 1px solid {LINE}; }}
.irds-side-brand {{ padding: 2px 0 10px; border-bottom: 1px solid {LINE}; margin-bottom: 10px; }}
.irds-side-brand .t {{ font-size: 1.08rem; font-weight: 700; }}
.irds-side-brand .s {{ font-size: .7rem; color: {MUTED}; letter-spacing: .05em; }}
.irds-side-label {{ font-size: .72rem; color: {MUTED}; font-weight: 600; letter-spacing: .08em; margin: 6px 0 4px; }}
.st-key-nav_page [role="radiogroup"] {{ gap: 2px; width: 100%; }}
.st-key-nav_page [role="radiogroup"] > div {{ width: 100%; display: block; }}
.st-key-nav_page [data-testid="stRadioOption"] {{
    width: 100% !important; max-width: none; box-sizing: border-box; display: flex !important;
    padding: 7px 10px; border-radius: 4px; margin: 0; border-left: 3px solid transparent; cursor: pointer;
}}
.st-key-nav_page [data-testid="stRadioOption"] > div > div:first-child:not([data-testid]) {{ display: none; }}
.st-key-nav_page [data-testid="stRadioOption"]:hover {{ background: rgba(15,118,110,.08); }}
.st-key-nav_page [data-testid="stRadioOption"][data-selected="true"] {{
    background: rgba(15,118,110,.12); border-left-color: {BRAND_ACCENT};
}}
.st-key-nav_page [data-testid="stRadioOption"][data-selected="true"] p {{ font-weight: 650; color: {BRAND_ACCENT}; }}

/* 按钮 */
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{ background: {BRAND_ACCENT}; border-color: {BRAND_ACCENT}; }}
.stButton > button, .stDownloadButton > button {{ border-radius: 4px; }}
[data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 4px; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ padding: 6px 14px; }}

/* 页脚 */
.irds-footer {{ border-top: 1px solid {LINE}; margin-top: 34px; padding-top: 12px; font-size: .74rem; color: {MUTED}; line-height: 1.8; }}

@media print {{
  section[data-testid="stSidebar"], [data-testid="stHeader"] {{ display:none !important; }}
}}
</style>
"""


def inject_css():
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


def plot_template():
    template = go.layout.Template()
    template.layout.update(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_STACK, color="#334155", size=12),
        xaxis=dict(gridcolor="#e5eaef", zerolinecolor="#cfd8df"),
        yaxis=dict(gridcolor="#e5eaef", zerolinecolor="#cfd8df"),
        colorway=["#0f766e", "#12313f", "#5b8fa8", "#c9a227", "#d9822b", "#c0392b", "#7c8b99"],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=40, b=10, l=10, r=10),
    )
    return template


def topbar(dataset, period, source):
    st.markdown(
        f"""
<div class="irds-topbar">
  <div>
    <div class="irds-brand-title">{PRODUCT_NAME}</div>
    <div class="irds-brand-sub">{PRODUCT_SUBTITLE}</div>
  </div>
  <div class="irds-meta">
    <div>当前数据集<span class="irds-chip">{escape(dataset)}</span><span class="irds-chip">{escape(period)}</span></div>
    <div>数据来源：{escape(source)}</div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


def page_title(title, subtitle=""):
    st.markdown(
        f'<div class="irds-page-title"><h2>{escape(title)}</h2><span>{escape(subtitle)}</span></div>',
        unsafe_allow_html=True,
    )


def section(title):
    st.markdown(f'<div class="irds-section">{escape(title)}</div>', unsafe_allow_html=True)


def note(text):
    st.markdown(f'<div class="irds-note">{text}</div>', unsafe_allow_html=True)


def kpi_card(label, value, unit="", tone="", note_text=""):
    unit_html = f'<span class="irds-kpi-unit">{escape(unit)}</span>' if unit else ""
    note_html = f'<div class="irds-kpi-note">{escape(note_text)}</div>' if note_text else ""
    return (
        f'<div class="irds-kpi {("tone-" + tone) if tone else ""}">'
        f'<div class="irds-kpi-label">{escape(label)}</div>'
        f'<div class="irds-kpi-value">{escape(str(value))}{unit_html}</div>{note_html}</div>'
    )


def kpi_row(cards):
    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        column.markdown(kpi_card(*card), unsafe_allow_html=True)


def badge(level):
    color = LEVEL_COLORS.get(level, "#64748b")
    return f'<span class="irds-badge" style="background:{color}">{LEVEL_LABELS.get(level, level)}</span>'


def footer(source, scope):
    st.markdown(
        f"""
<div class="irds-footer">
  <div><b>数据来源</b>：{escape(source)}</div>
  <div><b>口径声明</b>：{escape(scope)}</div>
  <div>{PRODUCT_NAME} · {PRODUCT_SUBTITLE} · 内部版本 {VERSION} · 本地部署，业务数据不出本机</div>
</div>""",
        unsafe_allow_html=True,
    )

"""归因定量分解面板：对数三要素、结构/自身效应与 Adtributor 切片。

单期数据以“全辖同车型”为比较基准；需要件数、暴露与赔款字段，缺失时如实降级。
"""

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.attribution.decomposition import additive_drilldown, log_decomposition, mix_rate_decomposition
from src.dashboard.theme import kpi_row, note, plot_template, section

REQUIRED = ("机构", "车型", "签单保费_万元", "赔案件数", "承保车年", "案均赔款_元")
PORTFOLIO = "全辖"


def prepare_cells(df):
    """返回含赔款与保费（元）的明细；不满足条件时返回 None 与原因。"""
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return None, f"当前数据缺少 {'、'.join(missing)}，无法做件数 × 暴露口径的定量分解。"
    if not df["机构"].eq(PORTFOLIO).any():
        return None, "当前数据没有“全辖”基准行，无法做对标分解。"
    cells = df[list(REQUIRED)].copy()
    for column in REQUIRED[2:]:
        cells[column] = pd.to_numeric(cells[column], errors="coerce")
    cells["赔案件数"] = cells["赔案件数"].fillna(0)
    cells["案均赔款_元"] = cells["案均赔款_元"].fillna(0)
    cells["赔款"] = cells["案均赔款_元"] * cells["赔案件数"]
    cells["签单保费"] = cells["签单保费_万元"] * 10000
    cells = cells.dropna(subset=["签单保费", "承保车年"])
    cells = cells[(cells["签单保费"] > 0) & (cells["承保车年"] > 0)]
    return cells, ""


def factors(frame):
    premium, exposure = float(frame["签单保费"].sum()), float(frame["承保车年"].sum())
    count, claims = float(frame["赔案件数"].sum()), float(frame["赔款"].sum())
    if min(premium, exposure, count, claims) <= 0:
        return None
    freq = count / exposure * 100
    severity = claims / count
    avg_premium = premium / exposure
    return {"累计出险频度": freq, "案均赔款": severity, "单均保费_元": avg_premium,
            "预期赔付率": freq * severity / avg_premium}


def _waterfall(contributions, total):
    labels = list(contributions) + ["总变化"]
    values = [v * 100 for v in contributions.values()] + [total * 100]
    fig = go.Figure(go.Waterfall(
        x=labels, y=values, measure=["relative"] * len(contributions) + ["total"],
        text=[f"{v:+.1f}" for v in values], textposition="outside",
        increasing=dict(marker_color="#c0392b"), decreasing=dict(marker_color="#2f855a"),
        totals=dict(marker_color="#12313f"), connector=dict(line=dict(color="#9aa8b4")),
    ))
    fig.update_layout(template=plot_template(), height=330, yaxis_title="对数贡献 ×100", margin=dict(t=40), showlegend=False)
    return fig


def render_quantitative(df):
    section("定量归因 · 对标全辖")
    cells, reason = prepare_cells(df)
    if cells is None:
        note(reason + " 可在下方查看单条预警的规则归因。")
        return
    regions = sorted(r for r in cells["机构"].unique() if r != PORTFOLIO)
    if not regions:
        note("当前数据只有全辖汇总，没有可对标的地区。")
        return
    portfolio = cells[cells["机构"].eq(PORTFOLIO)]
    default = int(np.argmax([float(cells[cells["机构"].eq(r)]["赔款"].sum()) for r in regions]))
    region = st.selectbox("对标地区", regions, index=default, key="quant_region")
    current = cells[cells["机构"].eq(region)]
    base, cur = factors(portfolio), factors(current)
    tab1, tab2, tab3 = st.tabs(["赔付率三要素分解", "结构效应与自身效应", "异常切片定位（Adtributor）"])

    with tab1:
        if base is None or cur is None:
            st.info("该地区赔案件数或赔款为零，无法做对数分解。")
        else:
            result = log_decomposition(base, cur)
            if result.get("fallback"):
                st.info("；".join(result.get("warning", [])) or "无法分解")
            else:
                contributions, total = result["contributions"], result["total_change"]
                kpi_row([
                    (f"{region} 经验赔付率", f"{cur['预期赔付率']:.1f}", "%", "", "赔款÷签单保费"),
                    ("全辖经验赔付率", f"{base['预期赔付率']:.1f}", "%", "slate", "同口径基准"),
                    ("相对差异（对数）", f"{total * 100:+.1f}", "", "red" if total > 0 else "", "≈ 百分比差异"),
                ])
                left, right = st.columns([3, 2])
                with left:
                    st.plotly_chart(_waterfall(contributions, total), use_container_width=True, config={"displayModeBar": False})
                with right:
                    table = pd.DataFrame([
                        {"要素": "出险频度", "全辖": f"{base['累计出险频度']:.2f}%", region: f"{cur['累计出险频度']:.2f}%",
                         "对数贡献×100": round(contributions["出险频度"] * 100, 2)},
                        {"要素": "案均赔款", "全辖": f"{base['案均赔款']:,.0f} 元", region: f"{cur['案均赔款']:,.0f} 元",
                         "对数贡献×100": round(contributions["案均赔款"] * 100, 2)},
                        {"要素": "单均保费（反向）", "全辖": f"{base['单均保费_元']:,.0f} 元", region: f"{cur['单均保费_元']:,.0f} 元",
                         "对数贡献×100": round(contributions["单均保费"] * 100, 2)},
                    ])
                    st.dataframe(table, hide_index=True, use_container_width=True)
                    check = math.fsum(contributions.values())
                    st.caption(f"三项之和 {check * 100:+.4f} ＝ 总变化 {total * 100:+.4f}（对数恒等式，无残差）。")
                note("赔付率＝出险频度 × 案均赔款 ÷ 单均保费；ln(地区/全辖) 可精确拆为三项之和。"
                     "单均保费上升会压低赔付率，因此取负号。口径：赔款＝案均赔款 × 赔案件数，频度与单均保费均以承保车年为分母；金额单位与源数据一致（默认数据集为巴西雷亚尔）。")

    with tab2:
        try:
            result = mix_rate_decomposition(portfolio, current, dimensions=("车型",), numerator="赔款", denominator="签单保费")
        except (ValueError, KeyError) as exc:
            st.info(f"无法计算结构效应：{exc}")
        else:
            kpi_row([
                ("赔付率差异", f"{result['total_change'] * 100:+.2f}", "个百分点", "", f"{region} − 全辖"),
                ("结构效应", f"{result['mix'] * 100:+.2f}", "个百分点", "slate", "车型构成不同带来的差异"),
                ("自身效应", f"{result['rate'] * 100:+.2f}", "个百分点", "slate", "同车型赔付水平不同带来的差异"),
            ])
            detail = result["details"].rename(columns={"mix": "结构效应(百分点)", "rate": "自身效应(百分点)"})
            detail[["结构效应(百分点)", "自身效应(百分点)"]] = (detail[["结构效应(百分点)", "自身效应(百分点)"]] * 100).round(2)
            st.dataframe(detail.sort_values("自身效应(百分点)", ascending=False), hide_index=True, use_container_width=True)
            st.caption("结构效应 ＋ 自身效应 ＝ 赔付率差异（对称分解，按车型分组）。")

    with tab3:
        rates = portfolio.groupby("车型")[["赔款", "签单保费"]].sum()
        rates = (rates["赔款"] / rates["签单保费"]).rename("全辖赔付率")
        states = cells[cells["机构"].ne(PORTFOLIO)].merge(rates, left_on="车型", right_index=True, how="inner")
        expected = states.assign(赔款=states["签单保费"] * states["全辖赔付率"])
        top_n = st.slider("显示切片数", 5, 20, 10, key="quant_topn")
        try:
            slices = additive_drilldown(expected, states, "赔款", dimensions=("机构", "车型"), top_n=top_n)
        except ValueError as exc:
            st.info(f"无法定位异常切片：{exc}")
        else:
            rows = [{
                "切片": " × ".join(f"{k}={v}" for k, v in s["dimensions"].items()),
                "超额赔款(万元)": round(s["change"] / 10000, 1),
                "解释力": round(s["explanatory_power"], 3),
                "意外度": round(s["surprise"], 4),
                "综合得分": round(s["score"], 5),
            } for s in slices]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.caption("基准为“各地区按全辖同车型赔付率测算的期望赔款”，当前为实际赔款。切片可重叠，贡献不能跨切片相加。")

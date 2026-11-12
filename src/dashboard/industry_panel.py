"""行业对标：中国保险业公开统计的宏观与基准视图。

数据只读取 data/official/china/ 下的已整理 CSV，全部标注发布机构与口径；
与车险经营监测明细（巴西 SUSEP 2011）分开展示，不做地区互换。
"""

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.region_map import load_china_geojson
from src.dashboard.theme import kpi_row, note, page_title, plot_template, section
from src.dashboard.export import present_frame

CHINA_DIR = Path(__file__).resolve().parents[2] / "data" / "official" / "china"
SEPARATE_CITIES = ("大连", "宁波", "厦门", "青岛", "深圳")
SCOPE_PROVINCE = "省级口径：财产险全险种（非车险单独口径），赔付为已付（现金制）赔付支出，赔付率＝财产险赔付支出÷财产险保费收入；省份数据不含计划单列市。"


@st.cache_data
def load_china_tables():
    read = lambda name: pd.read_csv(CHINA_DIR / name)
    return {
        "nbs": read("nbs_region_premium_claims_2020_2024.csv"),
        "vehicles": read("nbs_vehicles_by_province_2024.csv"),
        "motor": read("nfra_national_motor_series.csv"),
        "region_recent": read("nfra_region_premium_2025_2026ytd.csv"),
        "region_annual": read("nfra_region_premium_annual.csv"),
        "benchmark": read("caa2020_tpl_benchmark_pure_premium_by_region.csv"),
    }


def china_available():
    return CHINA_DIR.exists() and (CHINA_DIR / "nbs_region_premium_claims_2020_2024.csv").exists()


def _geo_name(region, names):
    return next((n for n in names if n and n.startswith(region)), None)


def province_frame(nbs, year):
    frame = nbs[nbs["数据年度"].eq(year)].copy()
    frame = frame[~frame["地区"].isin(("全国", "集团总公司本级", *SEPARATE_CITIES))]
    return frame


def _province_map(frame, value, title, colorscale):
    geojson = load_china_geojson()
    names = [f.get("properties", {}).get("name", "") for f in geojson.get("features", [])]
    frame = frame.assign(geo=[_geo_name(r, names) for r in frame["地区"]]).dropna(subset=["geo"])
    fig = go.Figure(go.Choropleth(
        geojson=geojson, featureidkey="properties.name", locations=frame["geo"], z=frame[value],
        text=frame["地区"], colorscale=colorscale, marker_line_color="#ffffff", marker_line_width=0.6,
        colorbar=dict(title=title, thickness=12, len=0.7),
        hovertemplate="%{text}<br>" + title + "：%{z:,.1f}<extra></extra>",
    ))
    fig.update_geos(visible=False, projection_type="mercator", lonaxis_range=[73, 136], lataxis_range=[16, 54],
                    bgcolor="rgba(0,0,0,0)")
    fig.update_layout(template=plot_template(), height=470, margin=dict(t=10, b=0, l=0, r=0))
    return fig


def render_industry():
    page_title("行业对标", "中国保险业公开统计 · 宏观态势与行业基准")
    if not china_available():
        st.warning("未找到中国行业公开统计文件，请检查 data/official/china/。")
        return
    t = load_china_tables()
    nbs, motor, vehicles = t["nbs"], t["motor"], t["vehicles"]
    latest_year = int(nbs["数据年度"].max())
    national = nbs[(nbs["数据年度"].eq(latest_year)) & nbs["地区"].eq("全国")].iloc[0]
    provinces = province_frame(nbs, latest_year)
    top = provinces.sort_values("财产险简单赔付率_%", ascending=False).iloc[0]
    annual = motor[motor["期间(本年累计)"].str.endswith("01~12")]
    last_full = annual.iloc[-1]
    ytd = motor.iloc[-1]
    kpi_row([
        (f"全国车险保费（{last_full['期间(本年累计)'][:4]} 年）", f"{last_full['机动车辆保险保费_亿元']:,.0f}", "亿元", "", "金融监管总局"),
        (f"车险保费（{ytd['期间(本年累计)'].replace('-01~', ' 年 1–')} 月累计）", f"{ytd['机动车辆保险保费_亿元']:,.0f}", "亿元", "", "金融监管总局"),
        (f"全国财产险简单赔付率（{latest_year}）", f"{national['财产险简单赔付率_%']:.1f}", "%", "slate", "国家统计局"),
        (f"最高省份：{top['地区']}（{latest_year}）", f"{top['财产险简单赔付率_%']:.1f}", "%", "red", "财产险全险种口径"),
        (f"民用汽车拥有量（{int(vehicles['年末'].iloc[0])} 年末）", f"{vehicles['民用汽车_万辆'].sum():,.0f}", "万辆", "slate", "31 省合计"),
    ])

    tabs = st.tabs(["省级财产险赔付", "全国车险走势", "地区保费规模", "基准纯风险保费查询", "车均保费代理"])

    with tabs[0]:
        years = sorted(nbs["数据年度"].unique(), reverse=True)
        year = st.selectbox("统计年度", years, key="industry_year")
        frame = province_frame(nbs, year)
        left, right = st.columns([7, 5])
        with left:
            section(f"{year} 年各省财产险简单赔付率")
            st.plotly_chart(_province_map(frame, "财产险简单赔付率_%", "赔付率(%)",
                                          [[0, "#e8f1f2"], [0.5, "#5b8fa8"], [1, "#12313f"]]),
                            use_container_width=True, config={"displayModeBar": False})
        with right:
            section("省级排名")
            table = frame[["地区", "保费_财产险业务", "赔付_财产险业务", "财产险简单赔付率_%"]].rename(columns={
                "保费_财产险业务": "保费(亿元)", "赔付_财产险业务": "赔付(亿元)", "财产险简单赔付率_%": "赔付率(%)"})
            st.dataframe(present_frame(table.sort_values("赔付率(%)", ascending=False)), hide_index=True, use_container_width=True, height=440,
                         column_config={"地区": st.column_config.TextColumn(width="small"),
                                        "赔付率(%)": st.column_config.NumberColumn(format="%.1f")})
        note(SCOPE_PROVINCE + " 来源：国家统计局《中国统计年鉴》表 18-22（2021 版为 18-21），数据年度 2020–2024。")
        hainan = nbs[(nbs["数据年度"].eq(2024)) & nbs["地区"].eq("海南")]
        if year == 2024 and not hainan.empty:
            note(f"关注：海南 2024 年财产险简单赔付率 {hainan.iloc[0]['财产险简单赔付率_%']:.1f}%，为当年各省最高。"
                 "可能与 2024 年 9 月超强台风“摩羯”有关——此为待核实注释，并非定论。")

    with tabs[1]:
        section("全国机动车辆保险保费（本年累计）")
        chart = motor.assign(期间=motor["期间(本年累计)"].str.replace("-01~12", " 年").str.replace("-01~", " 年 1–") )
        chart["期间"] = chart["期间"].where(chart["期间"].str.endswith("年"), chart["期间"] + " 月")
        fig = go.Figure()
        fig.add_bar(x=chart["期间"], y=chart["机动车辆保险保费_亿元"], name="车险保费(亿元)", marker_color="#0f766e",
                    text=chart["机动车辆保险保费_亿元"].map("{:,.0f}".format), textposition="outside", cliponaxis=False)
        fig.add_scatter(x=chart["期间"], y=chart["原保险保费收入_财产险公司_亿元"], name="财产险公司保费(亿元)",
                        mode="lines+markers", line=dict(color="#12313f"))
        fig.update_layout(template=plot_template(), height=380, yaxis_title="亿元", margin=dict(t=50))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        price = motor.dropna(subset=["车险单均保费_元(=保费/件数)"])
        if not price.empty:
            section("全国车险单均保费（按保单件）")
            fig2 = px.line(price.assign(年度=price["期间(本年累计)"].str[:4]), x="年度", y="车险单均保费_元(=保费/件数)",
                           markers=True, labels={"车险单均保费_元(=保费/件数)": "单均保费(元/件)"})
            fig2.update_traces(line_color="#0f766e")
            fig2.update_layout(template=plot_template(), height=300)
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
        note("来源：国家金融监督管理总局《财产保险公司经营情况表》，月度本年累计口径；2026 年为 1–9 月累计，不与全年直接比较。"
             "单均保费＝车险保费÷车险保单件数（按件而非按车），2024 年起官方表不再列示保单件数。赔款支出为财产险公司全险种合计。")

    with tabs[2]:
        recent = t["region_recent"]
        period = st.selectbox("统计期间", list(recent["期间(本年累计)"].unique()), key="industry_period",
                              format_func=lambda p: p.replace("-01~12", " 年全年").replace("-01~", " 年 1–") + ("" if p.endswith("12") else " 月"))
        frame = recent[recent["期间(本年累计)"].eq(period) & ~recent["地区"].isin(("全国", *SEPARATE_CITIES))]
        frame = frame.sort_values("财产险", ascending=True)
        fig = px.bar(frame, x="财产险", y="地区", orientation="h", labels={"财产险": "财产险原保险保费收入(亿元)"})
        fig.update_traces(marker_color="#12313f")
        fig.update_layout(template=plot_template(), height=760, margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        note("来源：国家金融监督管理总局《全国各地区原保险保费收入情况表》，从官方 PDF 图片转录并做行合计校验；"
             "险种为财产险全险种，省份不含计划单列市。2012–2018 年机读历史表另存于 data/official/china/nfra_region_premium_annual.csv。")

    with tabs[3]:
        bench = t["benchmark"]
        c1, c2, c3, c4 = st.columns(4)
        region = c1.selectbox("地区", list(bench["地区"].unique()), key="bench_region")
        usage = c2.selectbox("车辆使用性质", list(bench["车辆使用性质"].unique()), key="bench_usage")
        kinds = list(bench[bench["车辆使用性质"].eq(usage)]["车辆种类"].unique())
        kind = c3.selectbox("车辆种类", kinds, key="bench_kind")
        limits = list(bench["三者险责任限额"].unique())
        limit = c4.selectbox("三者险责任限额", limits, index=limits.index("100万") if "100万" in limits else 0, key="bench_limit")
        hit = bench[bench["地区"].eq(region) & bench["车辆使用性质"].eq(usage) & bench["车辆种类"].eq(kind) & bench["三者险责任限额"].eq(limit)]
        if hit.empty:
            st.info("该组合在基准表中无对应档位。")
        else:
            value = float(hit.iloc[0]["基准纯风险保费_元"])
            peers = bench[bench["车辆使用性质"].eq(usage) & bench["车辆种类"].eq(kind) & bench["三者险责任限额"].eq(limit)]
            rank = int((peers["基准纯风险保费_元"] > value).sum()) + 1
            kpi_row([
                ("基准纯风险保费", f"{value:,.2f}", "元", "", f"{region} · {usage} · {kind} · {limit}"),
                ("36 个地区中位数", f"{peers['基准纯风险保费_元'].median():,.2f}", "元", "slate", "同使用性质、车辆种类与限额"),
                ("地区排序（由高到低）", f"{rank}/{len(peers)}", "", "slate", "数值越高，期望赔付成本越高"),
            ])
            fig = px.bar(peers.sort_values("基准纯风险保费_元", ascending=False), x="地区", y="基准纯风险保费_元",
                         labels={"基准纯风险保费_元": "基准纯风险保费(元)"})
            fig.update_traces(marker_color=["#c0392b" if r == region else "#5b8fa8" for r in peers.sort_values("基准纯风险保费_元", ascending=False)["地区"]])
            fig.update_layout(template=plot_template(), height=340, margin=dict(t=20))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        note("来源：中国精算师协会《机动车商业保险示范产品基准纯风险保费表（2020 版）》第三者责任保险部分，"
             "36 个地区 × 8 类使用性质 × 车辆种类 × 14 档限额，共 17,136 个档位；已去掉车上人员责任险（伤亡类）。"
             "基准纯风险保费代表行业期望赔付成本，不是实际成交保费；使用时注明“2020 版”。")

    with tabs[4]:
        prem = province_frame(nbs, 2024)[["地区", "保费_财产险业务", "赔付_财产险业务"]]
        merged = prem.merge(vehicles.rename(columns={"省份": "地区"}), on="地区", how="inner")
        merged["财产险保费/汽车(元)"] = merged["保费_财产险业务"] * 1e8 / (merged["民用汽车_万辆"] * 1e4)
        merged["财产险赔付/汽车(元)"] = merged["赔付_财产险业务"] * 1e8 / (merged["民用汽车_万辆"] * 1e4)
        show = merged[["地区", "民用汽车_万辆", "保费_财产险业务", "财产险保费/汽车(元)", "财产险赔付/汽车(元)"]].rename(
            columns={"民用汽车_万辆": "民用汽车(万辆)", "保费_财产险业务": "财产险保费(亿元)"}).round(1)
        st.dataframe(present_frame(show.sort_values("财产险保费/汽车(元)", ascending=False)), hide_index=True, use_container_width=True, height=420)
        note("代理指标：2024 年省级财产险保费（国家统计局）÷ 2024 年末民用汽车拥有量（国家统计局）。"
             "分子含非车险险种、分母不等于承保车辆，仅用于横向规模比较，不能视为车均保费。")

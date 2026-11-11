"""IRDS 区域风险地图。

地图使用随项目分发的省级 GeoJSON 和 Plotly 绘制：全辖显示全国风险分布，
选择具体机构后自动放大到对应省份。地图只负责空间定位，风险数值仍来自当前表格。
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from src.utils.helpers import detail_frame
from src.alerts.thresholds import configured_thresholds, _level

ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "data" / "maps" / "china_provinces.json"

BRANCH_COORDS = {
    "全辖": (35.86, 104.20), "湖北": (30.98, 112.27), "江西": (27.61, 115.72),
    "江苏": (32.97, 119.46), "浙江": (29.14, 120.15), "河北": (38.04, 114.50),
    "陕西": (35.19, 108.87), "四川": (30.65, 102.69), "广西": (23.68, 108.33),
    "重庆": (29.56, 106.55), "河南": (34.77, 113.75), "湖南": (27.40, 111.74),
    "山东": (36.34, 118.15), "深圳": (22.54, 114.06), "辽宁": (41.30, 122.60),
    "山西": (37.87, 112.56), "安徽": (31.86, 117.28), "内蒙": (40.82, 111.67),
    "青岛": (36.07, 120.38), "北京": (39.90, 116.40), "天津": (39.08, 117.20),
    "宁波": (29.87, 121.55), "上海": (31.23, 121.47), "新疆": (43.79, 87.62),
    "云南": (25.04, 102.71), "大连": (38.91,121.61), "厦门": (24.48,118.09),
}

BRANCH_TO_GEO = {
    "全辖": "全国", "北京": "北京市", "天津": "天津市", "河北": "河北省", "山西": "山西省",
    "内蒙": "内蒙古自治区", "辽宁": "辽宁省", "上海": "上海市", "江苏": "江苏省",
    "浙江": "浙江省", "安徽": "安徽省", "福建": "福建省", "江西": "江西省", "山东": "山东省",
    "河南": "河南省", "湖北": "湖北省", "湖南": "湖南省", "广东": "广东省", "深圳": "广东省",
    "广西": "广西壮族自治区", "海南": "海南省", "重庆": "重庆市", "四川": "四川省",
    "贵州": "贵州省", "云南": "云南省", "陕西": "陕西省", "甘肃": "甘肃省", "青海": "青海省",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区", "青岛": "山东省", "宁波": "浙江省",
}


@st.cache_data
def load_china_geojson() -> dict:
    if not MAP_PATH.exists():
        return {"type": "FeatureCollection", "features": []}
    import json
    with MAP_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _risk_by_branch(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "机构" not in df.columns:
        return pd.DataFrame(columns=["机构", "风险值", "预警数"])
    view = detail_frame(df)
    view["预期成本率"] = pd.to_numeric(view.get("预期成本率"), errors="coerce")
    view["预警数"] = view.get("预警等级", pd.Series(index=view.index)).isin(
        ["yellow", "orange", "red"]
    ).astype(int)
    weights = pd.to_numeric(view.get('签单保费_万元', pd.Series(0, index=view.index)), errors='coerce').fillna(0).clip(lower=0)
    weights = weights.where(view['预期成本率'].notna(), 0)
    view['_权重'] = weights
    view['_加权值'] = view['预期成本率'].fillna(0) * weights
    grouped = view.groupby('机构', as_index=False).agg(总权重=('_权重','sum'), 加权值=('_加权值','sum'), 预警数=('预警数','sum'))
    grouped['风险值'] = grouped['加权值'].div(grouped['总权重'].where(grouped['总权重'].gt(0)))
    return grouped



def render_region_map(df: pd.DataFrame, focus_branch: str = "全辖", height: int = 470) -> None:
    geojson = load_china_geojson()
    if not geojson.get("features"):
        st.warning("省级地图边界文件缺失，请检查 data/maps/china_provinces.json。")
        return

    feature_names = [f.get("properties", {}).get("name", "") for f in geojson["features"]]
    branch_risk = _risk_by_branch(df)
    score_by_geo = {}
    for _, row in branch_risk.iterrows():
        geo_name = BRANCH_TO_GEO.get(str(row["机构"]))
        if geo_name and row["机构"] not in ("深圳", "青岛", "宁波", "大连", "厦门"):
            score_by_geo[geo_name] = max(score_by_geo.get(geo_name, 0), float(row["风险值"]))

    # 未出现在数据中的省份设为正常基线，保证地图始终完整显示。
    locations = [name for name in feature_names if name]
    z_values = [score_by_geo.get(name, 0) for name in locations]
    colorscale = [
        [0.0, "#e2e8f0"], [0.35, "#bae6fd"], [0.55, "#14b8a6"],
        [0.72, "#f59e0b"], [0.86, "#f97316"], [1.0, "#dc2626"],
    ]
    fig = go.Figure(go.Choropleth(
        geojson=geojson,
        locations=locations,
        z=[{"normal":0,"yellow":1,"orange":2,"red":3}[_level(v, configured_thresholds())] for v in z_values],
        customdata=z_values,
        featureidkey="properties.name",
        colorscale=[[0,"#4ade80"],[0.2499,"#4ade80"],[0.25,"#fbbf24"],[0.4999,"#fbbf24"],[0.5,"#fb923c"],[0.7499,"#fb923c"],[0.75,"#f87171"],[1,"#f87171"]],
        zmin=0,
        zmax=3,
        marker_line_color="rgba(71,85,105,.35)",
        marker_line_width=0.65,
        colorbar=dict(title="风险等级", tickvals=[0,1,2,3], ticktext=["正常","黄色","橙色","红色"], thickness=12, len=0.62),
        hovertemplate="%{location}<br>风险值：%{customdata:.1f}%<extra></extra>",
    ))

    # 叠加机构位置和预警数量，点击/切换省份后保留业务数据感知。
    points = []
    for branch, (lat, lon) in BRANCH_COORDS.items():
        if branch == "全辖":
            continue
        item = branch_risk[branch_risk["机构"].eq(branch)]
        risk = float(item["风险值"].iloc[0]) if not item.empty else 0
        alerts = int(item["预警数"].iloc[0]) if not item.empty else 0
        points.append((branch, lat, lon, risk, alerts))
    point_df = pd.DataFrame(points, columns=["机构", "纬度", "经度", "风险值", "预警数"])
    if focus_branch != "全辖":
        point_df["大小"] = point_df["机构"].map(lambda x: 13 if x == focus_branch else 6)
        point_df["颜色"] = point_df["机构"].map(lambda x: "#0f766e" if x == focus_branch else "#14b8a6")
    else:
        point_df["大小"] = point_df["预警数"].map(lambda x: max(6, min(20, 6 + x / 5)))
        point_df["颜色"] = point_df["风险值"].map(lambda v: {"normal":"#4ade80","yellow":"#fbbf24","orange":"#fb923c","red":"#f87171"}[_level(v, configured_thresholds())])
    fig.add_trace(go.Scattergeo(
        lon=point_df["经度"], lat=point_df["纬度"], mode="markers+text" if focus_branch != "全辖" else "markers",
        text=point_df["机构"],
        textposition="top center", textfont=dict(color="#1f2937", size=11),
        marker=dict(size=point_df["大小"], color=point_df["颜色"], line=dict(color="#475569", width=1)),
        customdata=point_df[["风险值", "预警数"]],
        hovertemplate="%{text}<br>平均预期成本率：%{customdata[0]:.1f}%<br>预警数：%{customdata[1]}<extra></extra>",
        name="机构风险点",
    ))

    coords = BRANCH_COORDS.get(focus_branch, BRANCH_COORDS["全辖"])
    fig.update_geos(
        visible=True,
        showcountries=True,
        showland=True,
        landcolor="#e2e8f0",
        lakecolor="#f8fafc",
        showlakes=True,
        countrycolor="rgba(71,85,105,.35)",
        projection_type="mercator",
        center=dict(lat=coords[0], lon=coords[1]),
        projection_scale=3.4 if focus_branch != "全辖" else 1.05,
        bgcolor="rgba(0,0,0,0)",
    )
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(color="#334155", size=12),
        title=dict(
            text=(f"{focus_branch} · 省份风险地图" if focus_branch != "全辖" else "全辖 · 全国风险地图"),
            x=0.03, xanchor="left", y=0.97, font=dict(size=16, color="#1f2937"),
        ),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

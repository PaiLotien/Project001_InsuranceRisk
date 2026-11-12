"""确定性数值查表：数值 / 事实类问题先在结构化数据里精确查找，再交给模型转述。

小模型读表容易错位（例如把 2024 年的值当成 2025 年），所以年份 × 指标、省份 × 指标、阈值、
基准纯风险保费、看板当前数据指标一律由 Python 查出，作为 [系统查表] 事实注入提示词；
查不到时明确给出“未查到”事实，模型不得估算。只读本地文件，不联网。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CHINA = ROOT / "data/official/china"
CITE = "系统查表"

NUMERIC_Q = re.compile(r"多少|几[个家条项]?|是多|数值|金额|规模|排名|最高|最低|前[三五十]|阈值|保费|赔付率|保有量|单均|纯风险")
PLAN_CITIES = {"大连", "宁波", "厦门", "青岛", "深圳"}
NON_PROVINCE = {"全国", "集团总公司本级", "总公司本级", "外险健康险全国合计"}


def _fmt(v, nd=2) -> str:
    """数值转文本：不加千分位、去掉末尾 0，保证与回答中的数字可逐字比对。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "未列示"
    f = round(float(v), nd)
    return str(int(f)) if f.is_integer() else f"{f:.{nd}f}".rstrip("0").rstrip(".")


def _fact(label: str, value: str, source: str) -> dict:
    return {"label": label, "value": value, "source": source}


@lru_cache(maxsize=None)
def _csv(name: str) -> pd.DataFrame:
    return pd.read_csv(CHINA / name, encoding="utf-8-sig")


def _years(q: str) -> list[int]:
    return [int(y) for y in re.findall(r"(20[12]\d)\s*年?", q)]


# ------------------------------------------------------------------ 全国车险（NFRA）

NATIONAL_METRICS = [
    (("单均保费", "件均保费"), "车险单均保费_元(=保费/件数)", "全国车险单均保费", "元"),
    (("保单件数", "件数"), "机动车辆保险保单件数_万件", "全国车险保单件数", "万件"),
    (("保险金额", "保额"), "机动车辆保险保险金额_亿元", "全国车险保险金额", "亿元"),
    (("赔款支出", "赔款"), "财产险公司赔款支出_亿元(全险种)", "财产险公司赔款支出（全险种）", "亿元"),
    (("财产险公司", "原保险保费"), "原保险保费收入_财产险公司_亿元", "财产险公司原保险保费收入", "亿元"),
    (("车险保费", "机动车辆保险保费", "车险", "机动车"), "机动车辆保险保费_亿元", "全国机动车辆保险保费", "亿元"),
]


def _national(q: str) -> list[dict]:
    if not re.search(r"全国|行业|车险|机动车", q) or _provinces(q):
        return []
    metric = next((m for m in NATIONAL_METRICS if any(k in q for k in m[0])), None)
    if metric is None or not re.search(r"保费|件数|保险金额|保额|赔款", q):
        return []
    df = _csv("nfra_national_motor_series.csv")
    src = "data/official/china/nfra_national_motor_series.csv（国家金融监督管理总局）"
    years = _years(q) or ([int(df.iloc[-2]["期间(本年累计)"][:4])] if not re.search(r"走势|历年|各年|趋势", q) else [])
    rows = df if not years else df[df["期间(本年累计)"].str[:4].astype(int).isin(years)]
    facts = []
    for _, r in rows.iterrows():
        period = r["期间(本年累计)"]
        label = f"{period[:4]} 年" + ("（1–9 月累计，不能与全年直接比较）" if period.endswith("09") else "（全年）")
        value = r[metric[1]]
        if pd.isna(value):
            facts.append(_fact(f"{label}{metric[2]}", "官方表未列示（2024 年起不再列示保单件数，无单均保费）", src))
        else:
            facts.append(_fact(f"{label}{metric[2]}", f"{_fmt(value)} {metric[3]}", src))
    for y in years:
        if not any(f["label"].startswith(f"{y} 年") for f in facts):
            facts.append(_fact(f"{y} 年{metric[2]}", "未查到（数据覆盖 2020–2025 全年和 2026 年 1–9 月）", src))
    return facts


# ------------------------------------------------------------------ 省份


@lru_cache(maxsize=None)
def _province_names() -> tuple[str, ...]:
    names = set(_csv("nbs_region_premium_claims_2020_2024.csv")["地区"]) - NON_PROVINCE
    return tuple(sorted(names, key=len, reverse=True))


def _provinces(q: str) -> list[str]:
    found = []
    for name in _province_names():
        if name in q and not any(name in f for f in found):
            found.append(name)
    return found


def _province_facts(q: str) -> list[dict]:
    provs = _provinces(q)
    rank = re.search(r"最高|最低|排名|前[三五十]", q) and re.search(r"省|地区|哪", q)
    if not provs and not rank:
        return []
    years = _years(q)
    facts: list[dict] = []
    nbs = _csv("nbs_region_premium_claims_2020_2024.csv")
    nbs_src = "data/official/china/nbs_region_premium_claims_2020_2024.csv（国家统计局《中国统计年鉴》）"
    caveat = _fact("口径提示", "省级为财产险全险种口径（赔付支出、现金制），不是车险单独口径；省份不含计划单列市", nbs_src)
    if re.search(r"出险频度|出险频率|案均", q):
        return [_fact(f"{'、'.join(provs) or '省级'}出险频度 / 案均赔款", "未查到（国内公开数据中没有省级出险频度、案均赔款）", nbs_src)]
    if rank and re.search(r"赔付率", q):
        y = years[0] if years else int(nbs["数据年度"].max())
        d = nbs[(nbs["数据年度"] == y) & ~nbs["地区"].isin(NON_PROVINCE | PLAN_CITIES)]
        d = d.sort_values("财产险简单赔付率_%", ascending=False)
        top, bottom = d.head(3), d.tail(3).iloc[::-1]
        facts.append(_fact(f"{y} 年全国财产险简单赔付率", f"{_fmt(nbs[(nbs['数据年度'] == y) & (nbs['地区'] == '全国')]['财产险简单赔付率_%'].iloc[0])}%", nbs_src))
        facts.append(_fact(f"{y} 年财产险简单赔付率最高的省份（前 3）",
                           "；".join(f"{r['地区']} {_fmt(r['财产险简单赔付率_%'])}%" for _, r in top.iterrows()), nbs_src))
        facts.append(_fact(f"{y} 年财产险简单赔付率最低的省份（前 3）",
                           "；".join(f"{r['地区']} {_fmt(r['财产险简单赔付率_%'])}%" for _, r in bottom.iterrows()), nbs_src))
        return facts + [caveat]
    for p in provs[:3]:
        if re.search(r"汽车|保有量|车辆数|载客|载货", q):
            v = _csv("nbs_vehicles_by_province_2024.csv")
            r = v[v["省份"] == p]
            src = "data/official/china/nbs_vehicles_by_province_2024.csv（国家统计局，来源公安部交管）"
            if r.empty:
                facts.append(_fact(f"{p}民用汽车", "未查到（该表只有 31 个省份，2024 年末）", src))
            else:
                r = r.iloc[0]
                facts.append(_fact(f"2024 年末{p}民用汽车", f"{_fmt(r['民用汽车_万辆'])} 万辆（载客 {_fmt(r['载客汽车_万辆'])}、载货 {_fmt(r['载货汽车_万辆'])} 万辆）", src))
            continue
        if not re.search(r"赔付|保费|保险收入|保费收入", q):
            continue
        ys = years or [2024]
        for y in ys:
            if 2020 <= y <= 2024:
                r = nbs[(nbs["数据年度"] == y) & (nbs["地区"] == p)]
                if r.empty:
                    facts.append(_fact(f"{y} 年{p}", "未查到", nbs_src))
                    continue
                r = r.iloc[0]
                if "赔付率" in q or "赔付" in q:
                    facts.append(_fact(f"{y} 年{p}财产险简单赔付率", f"{_fmt(r['财产险简单赔付率_%'])}%（财产险赔付 {_fmt(r['赔付_财产险业务'])} 亿元 ÷ 财产险保费 {_fmt(r['保费_财产险业务'])} 亿元）", nbs_src))
                else:
                    facts.append(_fact(f"{y} 年{p}保费收入", f"合计 {_fmt(r['保费_小计_亿元'])} 亿元，其中财产险 {_fmt(r['保费_财产险业务'])} 亿元", nbs_src))
            elif y in (2025, 2026):
                d = _csv("nfra_region_premium_2025_2026ytd.csv")
                r = d[(d["期间(本年累计)"].str[:4] == str(y)) & (d["地区"] == p)]
                src = "data/official/china/nfra_region_premium_2025_2026ytd.csv（国家金融监督管理总局）"
                if r.empty or "赔付" in q:
                    facts.append(_fact(f"{y} 年{p}{'赔付率' if '赔付' in q else '保费'}", "未查到（2025 年起只有分地区保费，没有赔付）" if "赔付" in q else "未查到", src))
                else:
                    r = r.iloc[0]
                    tag = "1–9 月累计" if y == 2026 else "全年"
                    facts.append(_fact(f"{y} 年（{tag}）{p}原保险保费收入", f"合计 {_fmt(r['原保险保费收入合计'])} 亿元，其中财产险 {_fmt(r['财产险'])} 亿元", src))
            elif 2012 <= y <= 2018 and "赔付" not in q:
                d = _csv("nfra_region_premium_annual.csv")
                r = d[(d["年度"] == y) & (d["地区"].astype(str).str.startswith(p))]
                src = "data/official/china/nfra_region_premium_annual.csv（原保监会 / 银保监会）"
                if r.empty:
                    facts.append(_fact(f"{y} 年{p}保费", "未查到", src))
                else:
                    r = r.iloc[0]
                    facts.append(_fact(f"{y} 年{p}原保险保费收入", f"合计 {_fmt(r['原保险保费收入合计'])} 万元，其中财产险 {_fmt(r['财产险'])} 万元", src))
            else:
                facts.append(_fact(f"{y} 年{p}", "未查到（结构化数据不覆盖该年份或指标）", nbs_src))
    if facts and re.search(r"赔付|保费", q):
        facts.append(caveat)
        if "车险" in q:
            facts.append(_fact("车险口径", "省级没有车险单独口径的保费与赔款，以上为财产险全险种数据", nbs_src))
    return facts


# ------------------------------------------------------------------ 精协基准纯风险保费

USE_KEYS = [("家庭自用", "家庭自用汽车"), ("家用", "家庭自用汽车"), ("私家车", "家庭自用汽车"), ("企业非营业", "企业非营业客车"),
            ("党政", "党政机关、事业团体非营业客车"), ("机关", "党政机关、事业团体非营业客车"), ("非营业货车", "非营业货车"),
            ("出租", "出租、租赁营业客车"), ("租赁", "出租、租赁营业客车"), ("公交", "城市公交营业客车"),
            ("公路客运", "公路客运营业客车"), ("营业货车", "营业货车")]


def _benchmark(q: str) -> list[dict]:
    if ("纯风险保费" not in q and "基准保费" not in q) or not re.search(r"多少|几|金额|数值|查一下|查询", q):
        return []
    df = _csv("caa2020_tpl_benchmark_pure_premium_by_region.csv")
    src = "data/official/china/caa2020_tpl_benchmark_pure_premium_by_region.csv（中国精算师协会 2020 版，仅三者险）"
    regions = [r for r in sorted(df["地区"].unique(), key=len, reverse=True) if r in q]
    use = next((v for k, v in USE_KEYS if k in q and not (k == "营业货车" and "非营业货车" in q)), None)
    kinds = [k for k in df["车辆种类"].unique() if k in q]
    limit = re.search(r"(\d+)\s*万", q)
    d = df
    if regions:
        d = d[d["地区"].isin(regions[:1])]
    if use:
        d = d[d["车辆使用性质"] == use]
    if kinds:
        d = d[d["车辆种类"].isin(kinds)]
    if limit:
        d = d[d["三者险责任限额"] == f"{limit.group(1)}万"]
    if not regions or d.empty:
        return [_fact("基准纯风险保费", "未查到（需指定地区、车辆使用性质、车辆种类和三者险限额；36 个地区 × 8 类使用性质 × 14 档限额）" if d.empty or not regions else "请指定地区", src)]
    facts = [_fact(f"{r['地区']}·{r['车辆使用性质']}·{r['车辆种类']}·三者险 {r['三者险责任限额']}", f"{_fmt(r['基准纯风险保费_元'], 2)} 元", src)
             for _, r in d.head(6).iterrows()]
    if len(d) > 6:
        facts.append(_fact("匹配档位", f"共 {len(d)} 档，仅列前 6 档；请补充车辆种类或限额", src))
    facts.append(_fact("口径", "2020 版行业期望赔付成本，仅含第三者责任保险，不是实际成交保费", src))
    return facts


# ------------------------------------------------------------------ 阈值与规则


def _thresholds(q: str) -> list[dict]:
    ids = re.findall(r"R0(?:0[5-9]|1[01])", q.upper())
    if not ids and not re.search(r"阈值|[黄橙红]色.*(多少|标准|线)|几级|三级", q):
        return []
    import yaml

    from src.alerts.thresholds import configured_thresholds

    src = "config/alert_rules.yaml（规则引擎）+ 本机阈值设置"
    t = configured_thresholds()
    facts = [_fact("当前预期成本率阈值", f"黄色 > {_fmt(t['yellow'])}%（R005）；橙色 > {_fmt(t['orange'])}%（R011，待业务确认）；"
                   f"红色 > {_fmt(t['red'])}%（R010，待业务确认）；均为严格大于", src)]
    rules = {r["id"]: r for r in yaml.safe_load((ROOT / "config/alert_rules.yaml").read_text(encoding="utf-8"))["rules"]}
    for rid in dict.fromkeys(ids):
        r = rules.get(rid)
        if r:
            value = r["description"] + ("" if r.get("enabled", True) else "（未启用）")
            if r.get("preconditions"):
                value += "；前置条件任一满足：当月满期保费 > 100 万元，或当月满期进度 > 2 个月"
            facts.append(_fact(rid, value, "config/alert_rules.yaml"))
    return facts


# ------------------------------------------------------------------ 看板当前数据

FRAME_METRICS = ["预期成本率", "预期赔付率", "成本率1", "签单保费_万元", "保费占比", "单均保费_元", "累计出险频度",
                 "满期赔付率", "案均赔款_元", "承保车年", "赔案件数", "预警等级"]


def _frame(q: str, frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True) or "机构" not in frame.columns:
        return []
    branches = [b for b in sorted(frame["机构"].astype(str).unique(), key=len, reverse=True) if b and b in q]
    metrics = [m for m in FRAME_METRICS if m in frame.columns and m.split("_")[0] in q]
    if not branches or not metrics:
        return []
    d = frame[frame["机构"].astype(str) == branches[0]]
    vehicles = [v for v in d.get("车型", pd.Series(dtype=str)).astype(str).unique() if v and v in q]
    if vehicles:
        d = d[d["车型"].astype(str).isin(vehicles[:1])]
    facts = []
    for _, r in d.head(8).iterrows():
        vals = "；".join(f"{m} {r[m] if isinstance(r[m], str) else _fmt(r[m], 1 if m not in ('承保车年', '赔案件数') else 0)}" for m in metrics)
        facts.append(_fact(f"当前数据 {r['机构']}·{r.get('车型', '')}", vals, "看板当前数据集"))
    return facts


# ------------------------------------------------------------------ 入口


def lookup_facts(question: str, context=None, frame=None) -> dict:
    """返回 {"facts": [...], "no_hit": bool, "attempted": bool}。"""
    q = str(question or "")
    facts: list[dict] = []
    attempted = bool(NUMERIC_Q.search(q))
    if attempted and re.search(r"渤海", q):
        facts = [_fact("渤海财险经营指标", "未查到（渤海财险未提供自有数据，结构化数据和知识库中都没有渤海财险的经营指标）", "docs/data_sources.md")]
    elif attempted:
        for fn in (_benchmark, _province_facts, _national, _thresholds):
            try:
                facts = fn(q)
            except Exception as exc:  # 查表失败不阻断问答
                facts = [_fact("查表异常", str(exc)[:80], "src/advisor/lookup.py")]
            if facts:
                break
        if not facts:
            facts = _frame(q, frame)
    no_hit = bool(facts) and all(f["value"].startswith(("未查到", "官方表未列示")) for f in facts)
    return {"facts": facts, "no_hit": no_hit, "attempted": attempted}


def render_facts(facts: list[dict]) -> str:
    return "\n".join(f"[{CITE}] {f['label']}：{f['value']}（来源：{f['source']}）" for f in facts)

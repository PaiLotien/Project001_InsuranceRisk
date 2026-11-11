"""离线生成 IRDS 本地 AI 顾问的 LoRA SFT 数据集（chat 格式 JSONL）。

数据全部来自本仓库：config/*.yaml 规则、knowledge/ 知识文档、data/official/ 公开统计、
以及系统自身的判级函数。每条样本的 user 消息与线上推理提示词同构：
【知识库片段】[K编号] 来源｜章节 + 正文（含干扰片段）→【系统计算结果】→【问题】；
assistant 回答只引用给出的 [K编号] / [系统]，数字逐字来自片段或系统结果。

可选 --paraphrase N：用本机 Ollama（127.0.0.1）改写 N 条问题以增加问法多样性；全程离线保护。

用法：
    .venv/bin/python finetune/build_sft_dataset.py            # 纯模板，约 1 秒
    .venv/bin/python finetune/build_sft_dataset.py --paraphrase 80
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.advisor import rag as rc  # noqa: E402
from src.advisor.context import build_alert_context  # noqa: E402
from src.alerts.thresholds import _level, configured_thresholds  # noqa: E402

OUT = ROOT / "finetune/data"
JSON_INSTRUCTION = "返回 answer/evidence[{claim,source}]/suggestions/uncertainty/refused，严格遵循 JSON Schema。"
LEVEL_CN = {"red": "红色", "orange": "橙色", "yellow": "黄色", "normal": "正常（不预警）"}
RNG = random.Random(20261111)
MAX_PART_BYTES = 900_000


# ------------------------------------------------------------------ 片段与样本


def snip(source: str, section: str, text: str) -> dict:
    return {"source": source, "section": section, "text": re.sub(r"\s+\n", "\n", text.strip())}


def md_sections(rel: str) -> list[dict]:
    path = ROOT / "knowledge" / rel
    return [snip(rel, sec, body) for sec, body in rc._md_sections(path.read_text(encoding="utf-8")) if body.strip()]


DISTRACTORS: list[dict] = []


def render_user(question: str, snippets: list[dict], context, json_mode: bool) -> str:
    lines = [f"[K{i}] {s['source']}｜{s['section']}\n{s['text']}" for i, s in enumerate(snippets, 1)]
    ctx = json.dumps(context, ensure_ascii=False, separators=(",", ":")) if context else "无"
    tail = ("\n" + JSON_INSTRUCTION) if json_mode else ""
    return f"【知识库片段】\n" + "\n".join(lines) + f"\n【系统计算结果】{ctx}\n【问题】{question}{tail}"


def arrange(gold: list[dict], n_distract: int = 2) -> tuple[list[dict], list[str]]:
    """把 gold 片段与干扰片段打乱，返回片段列表及 gold 的引用编号。"""
    pool = [d for d in DISTRACTORS if all(d["text"] != g["text"] for g in gold)]
    items = [(g, True) for g in gold] + [(d, False) for d in RNG.sample(pool, min(n_distract, len(pool)))]
    RNG.shuffle(items)
    snippets = [s for s, _ in items]
    cites = [f"K{i}" for i, (_, is_gold) in enumerate(items, 1) if is_gold]
    return snippets, cites


SAMPLES: list[dict] = []


def add(category: str, question: str, gold: list[dict], answer_fn, *, context=None, json_mode=False, n_distract=2):
    """answer_fn(cites) -> str（文本模式）或 dict（JSON 模式）。"""
    snippets, cites = arrange(gold, n_distract)
    answer = answer_fn(cites)
    if json_mode:
        answer = json.dumps(answer, ensure_ascii=False)
    SAMPLES.append({
        "category": category,
        "messages": [
            {"role": "user", "content": render_user(question, snippets, context, json_mode)},
            {"role": "assistant", "content": answer},
        ],
        "_q": question,
        "_ctx": context,
        "_json": json_mode,
    })


def jans(answer, evidence=(), suggestions=(), uncertainty=(), refused=False) -> dict:
    return {
        "answer": answer,
        "evidence": [{"claim": c, "source": s} for c, s in evidence],
        "suggestions": list(suggestions),
        "uncertainty": list(uncertainty),
        "refused": refused,
    }


def pick(options):
    return RNG.choice(options)


# ------------------------------------------------------------------ A. 预警规则


def gen_rules():
    cfg = yaml.safe_load((ROOT / "config/alert_rules.yaml").read_text(encoding="utf-8"))
    for r in cfg["rules"]:
        rid = r["id"]
        body = r["description"]
        if r.get("note"):
            body += "\n说明：" + r["note"]
        if r.get("preconditions"):
            logic = "任一满足（any）" if r.get("precondition_logic") == "any" else "全部满足（all）"
            conds = "；".join(f"{c['metric']}{'>' if c['operator'] == 'gt' else c['operator']}{c['value']}"
                             f"{'万元' if c['unit'] == 'wan_yuan' else '个月'}" for c in r["preconditions"])
            body += f"\n前置条件（{logic}）：{conds}"
        body += f"\n等级：{r['level']}；启用：{'是' if r['enabled'] else '否'}"
        gold = [snip("配置规则/alert_rules.yaml", f"rules > {rid}", body)]
        level = LEVEL_CN.get(r["level"], r["level"])
        pending = "待" in (r.get("note", "") + r["description"])

        def ans(c, r=r, level=level, pending=pending):
            if not r["enabled"]:
                return (f"{r['id']}（{r['metric']}）目前未启用：{r['note']} [{c[0]}]\n"
                        "知识库没有给出它的比较方式和阈值，不能自行推断，需业务确认后再配置。")
            s = f"{r['id']} 的触发条件：{r['description']} [{c[0]}]\n- 预警等级：{level}。"
            if r.get("preconditions"):
                s += f"\n- 前置条件满足任意一个即可评估（满期保费>100万元，或满期进度>2个月）[{c[0]}]。"
            if pending:
                s += "\n- 注意：该规则的等级或推送对象在知识库中标注为待确认，引用时需说明。"
            return s

        for q in [f"{rid} 的触发条件是什么？", f"{r['metric']}对应的 {rid} 规则怎么判定？",
                  f"{rid} 是什么规则？有没有启用？"]:
            add("规则", q, gold, ans)
        if r.get("preconditions"):
            add("规则", "R008 的前置条件是“且”还是“或”？", gold, lambda c: (
                f"是“或”：两个前置条件满足任意一个即可评估 R008 [{c[0]}]。\n- 当月满期保费>100万元；\n"
                f"- 当月满期进度>2个月。\n11.1 反馈前是“且”，已改为 precondition_logic: any [{c[0]}]。"))
    combo = cfg["combinations"][0]
    gold = [snip("配置规则/alert_rules.yaml", "combinations > C001",
                 "组合条件引用同机构、车型、期间、区块及维度的规则命中结果；只升一级，最高红色。"
                 f"\n{combo['id']}：{combo['expression']}，action: {combo['action']}")]
    for q in ["C001 组合规则是什么意思？", "什么情况下预警会自动升一级？"]:
        add("规则", q, gold, lambda c: (f"C001 = R008 AND R007：同一机构、车型、期间同时命中出险频度恶化（R008）"
                                        f"和案均赔款恶化（R007）时，预警升一级，最高到红色 [{c[0]}]。"))


# ------------------------------------------------------------------ B. 数据质量规则


def gen_quality():
    cfg = yaml.safe_load((ROOT / "config/data_quality_rules.yaml").read_text(encoding="utf-8"))
    for r in cfg["rules"]:
        body = f"{r['id']} {r['name']}：{r['description']}\n启用：{'是' if r['enabled'] else '否'}\n说明：{r.get('note', '')}"
        gold = [snip("配置规则/data_quality_rules.yaml", f"rules > {r['id']}", body)]
        state = "已启用" if r["enabled"] else "默认不启用"

        def ans(c, r=r, state=state):
            return f"{r['id']}（{r['name']}）：{r['description']} [{c[0]}]\n- 状态：{state}。\n- 说明：{r.get('note', '')} [{c[0]}]"

        for q in [f"{r['id']} 校验什么？", f"“{r['name']}”这条数据质量规则是怎么规定的？"]:
            add("校验", q, gold, ans)
    for r in cfg.get("schema_rules", [])[:14]:
        gold = [snip("配置规则/data_quality_rules.yaml", f"schema_rules > {r['id']}",
                     f"{r['id']} {r['name']}；类型：{r['kind']}；字段：{r.get('column', '')}")]
        add("校验", f"{r['id']} 是什么校验？", gold,
            lambda c, r=r: f"{r['id']} 是“{r['name']}”，针对字段“{r.get('column', '')}”的{r['kind']}类工程完整性校验 [{c[0]}]。")


# ------------------------------------------------------------------ C. 指标口径


def gen_metrics():
    cfg = yaml.safe_load((ROOT / "config/metric_definitions.yaml").read_text(encoding="utf-8"))
    unit_rule = {"ratio": "比例型指标，变化用“百分点”（直接相减）", "absolute": "规模型指标，变化用相对变化率“%”",
                 "coefficient": "系数型指标，直接展示"}
    for m in cfg["metrics"]:
        body = f"{m['name']}：{m.get('description', '')}\n单位：{m.get('unit') or '无'}；类型：{m['type']}"
        if m.get("formula"):
            body += f"\n公式：{m['formula']}"
        gold = [snip("配置规则/metric_definitions.yaml", f"metrics > {m['key']}", body)]

        def ans(c, m=m):
            s = f"{m['name']}：{m.get('description', '')} [{c[0]}]"
            if m.get("formula"):
                s += f"\n- 公式：{m['formula']} [{c[0]}]"
            s += f"\n- 单位：{m.get('unit') or '无'}；{unit_rule.get(m['type'], '')}。"
            return s

        for q in [f"{m['name']}是怎么定义的？", f"{m['name']}的计算公式和单位是什么？"]:
            add("口径", q, gold, ans)
        add("口径", f"{m['name']}的同比变化应该怎么表达？", gold,
            lambda c, m=m: f"{m['name']}是{unit_rule.get(m['type'], '')}[{c[0]}]。")


# ------------------------------------------------------------------ D. 判级边界（系统结果解释）


THRESH_DOC = "平台规则/三级预警阈值规则.md"


def gen_boundaries():
    secs = {s["section"].split(" > ")[-1]: s for s in md_sections(THRESH_DOC)}
    rule_sec = secs["预期成本率三条判级规则"]
    edge_sec = secs["最高等级优先与边界取值"]
    th = configured_thresholds(include_user=False)
    values = [88.0, 89.9, 90.0, 90.1, 92.3, 94.9, 95.0, 95.1, 97.6, 99.9, 100.0, 100.1, 103.5, 108.4, 112.0,
              91.2, 96.4, 98.8, 101.7, 86.5]
    for v in values:
        lvl = _level(v, th)
        ctx = {"预期成本率": f"{v:.1f}%", "预警等级": LEVEL_CN[lvl], "黄色阈值": "90.0%", "橙色阈值": "95.0%",
               "红色阈值": "100.0%"}
        nxt = {"normal": ("黄色", "90.0%"), "yellow": ("橙色", "95.0%"), "orange": ("红色", "100.0%")}.get(lvl)
        crossed = {"yellow": "90.0%", "orange": "95.0%", "red": "100.0%"}.get(lvl)

        def ans(c, v=v, lvl=lvl, nxt=nxt, crossed=crossed):
            if lvl == "normal":
                s = f"系统判为正常：预期成本率 {v:.1f}% 没有超过黄色阈值 90.0% [系统]。"
            else:
                s = f"系统判为{LEVEL_CN[lvl]}：预期成本率 {v:.1f}% 严格大于{LEVEL_CN[lvl]}阈值 {crossed} [系统]。"
            if nxt:
                s += f"\n- 未到{nxt[0]}：{nxt[0]}要求严格大于 {nxt[1]}，等于阈值不触发 [{c[1]}]。"
            s += f"\n- 判级顺序为红 > 橙 > 黄，取命中的最高等级 [{c[1]}]。"
            if lvl in ("orange", "red"):
                s += f"\n- 橙色 95% 与红色 100% 在知识库中标注为待业务确认 [{c[0]}]。"
            return s

        q = pick(["为什么这条记录是这个预警等级？", "这条预警的等级是怎么判出来的？", "为什么没有判成更高一级？"])
        if v in (90.0, 95.0, 100.0):
            q = f"预期成本率正好 {v:g}%，为什么系统判{LEVEL_CN[lvl].split('（')[0]}？"
        add("判级解释", q, [rule_sec, edge_sec], ans, context=ctx, n_distract=1)
    # 无上下文的边界问答
    for v, lvl in [(100, "orange"), (95, "yellow"), (90, "normal")]:
        add("判级解释", f"预期成本率恰好等于 {v}% 时判什么等级？", [edge_sec], lambda c, v=v, lvl=lvl: (
            f"判{LEVEL_CN[lvl]}。所有比较都是严格大于，等于阈值不触发该级 [{c[0]}]。"
            + (f"\n红色是否改为“大于等于 100%”需业务确认，系统目前保持严格大于 [{c[0]}]。" if v == 100 else "")))


# ------------------------------------------------------------------ E. 真实预警解释（JSON 模式）


def gen_alert_explanations(n: int = 48):
    df = pd.read_excel(ROOT / "data/official/brvehins_2011.xlsx", sheet_name="完整数据集", engine="openpyxl")
    df = df[(df["机构"] != "全辖") & (~df["车型"].astype(str).str.endswith("合计"))]
    th = configured_thresholds(include_user=False)
    df["lvl"] = [_level(v, th) for v in df["预期成本率"]]
    alerts = df[df["lvl"] != "normal"]
    chosen = alerts.sample(min(n, len(alerts)), random_state=7)
    secs = {s["section"].split(" > ")[-1]: s for s in md_sections(THRESH_DOC)}
    src_sec = [s for s in md_sections("项目文档/data_sources.md") if "缺口" in s["section"]][0] \
        if (ROOT / "knowledge/项目文档/data_sources.md").exists() else None
    fields = ["机构", "车型", "预期成本率", "预期赔付率", "成本率1", "累计出险频度", "案均赔款_元", "单均保费_元"]
    for _, row in chosen.iterrows():
        raw = {k: row[k] for k in fields}
        ctx = build_alert_context(raw, th, source_label="SUSEP 2011 公开统计（车险经营监测数据集）")
        ctx["预警等级"] = LEVEL_CN[row["lvl"]]
        crossed = ctx[{"yellow": "黄色阈值", "orange": "橙色阈值", "red": "红色阈值"}[row["lvl"]]]
        gold = [secs["预期成本率三条判级规则"]] + ([src_sec] if src_sec else [])

        def ans(c, ctx=ctx, crossed=crossed, lvl=row["lvl"]):
            return jans(
                f"系统判为{LEVEL_CN[lvl]}：{ctx['机构']}·{ctx['车型']}预期成本率 {ctx['预期成本率']}，"
                f"超过{LEVEL_CN[lvl]}阈值 {crossed}。",
                evidence=[(f"预期成本率 {ctx['预期成本率']}＝预期赔付率 {ctx['预期赔付率']}＋成本率1 {ctx['成本率1']}", "系统"),
                          (f"{LEVEL_CN[lvl]}规则为预期成本率严格大于 {crossed}", c[0])],
                suggestions=[f"先核查预期赔付率 {ctx['预期赔付率']} 的来源：出险频度 {ctx['累计出险频度']}、案均赔款 {ctx['案均赔款_元']}",
                             "在“归因分析”页对标全辖查看频度、案均、单均保费三要素贡献",
                             f"复核单均保费 {ctx['单均保费_元']} 是否覆盖风险成本"],
                uncertainty=[f"成本率1（费用率）{ctx['成本率1']} 是假设值，待业务校准，会直接影响等级" +
                             (f" [{c[1]}]" if len(c) > 1 else ""),
                             "金额单位为巴西雷亚尔"],
            )

        q = pick(["这条预警为什么是这个等级？", "请解释这条预警的原因并给出核查建议", "这条记录为什么报警？先查什么？"])
        add("预警解释", q, gold, ans, context=ctx, json_mode=True, n_distract=1)


# ------------------------------------------------------------------ F. 行业对标公开数据


def gen_industry():
    china = ROOT / "data/official/china"
    caveat = [s for s in md_sections("平台规则/行业对标数据与口径.md") if "省级财产险赔付率口径" in s["section"]][0]
    nfra = pd.read_csv(china / "nfra_national_motor_series.csv")
    for _, r in nfra.iterrows():
        period = r["期间(本年累计)"]
        text = "；".join(f"{k}：{v}" for k, v in r.items() if pd.notna(v) and k not in ("NFRA_docId",))
        gold = [snip("data/official/china/nfra_national_motor_series.csv", period, text)]
        label = period.replace("-01~12", " 年").replace("-01~09", " 年 1–9 月")
        prem = r["机动车辆保险保费_亿元"]
        add("行业对标", f"{label}全国机动车辆保险保费是多少？", gold, lambda c, label=label, prem=prem, period=period: (
            f"{label}全国机动车辆保险保费 {prem} 亿元（国家金融监督管理总局，期间 {period}）[{c[0]}]。"
            + ("\n注意：1–9 月为累计数，不能与全年直接比较。" if "09" in period else "")))
        avg = r["车险单均保费_元(=保费/件数)"]
        if pd.notna(avg):
            add("行业对标", f"{label}车险单均保费是多少？", gold, lambda c, label=label, avg=avg: (
                f"{label}车险单均保费 {int(avg)} 元 [{c[0]}]，口径为车险保费 ÷ 保单件数，按件不按车。"))
        else:
            add("行业对标", f"{label}全国车险单均保费是多少元？", gold, lambda c, label=label: (
                f"知识库中没有{label}的车险单均保费：官方表自 2024 年起不再列示保单件数，无法得到单均保费 [{c[0]}]。"
                "不要自行用其他数据推算。"), json_mode=False)
    nbs = pd.read_csv(china / "nbs_region_premium_claims_2020_2024.csv")
    nbs = nbs[~nbs["地区"].isin(["集团总公司本级"])]
    sample = pd.concat([nbs[nbs["数据年度"] == 2024], nbs[nbs["数据年度"] != 2024].sample(30, random_state=3)])
    for _, r in sample.iterrows():
        text = (f"数据年度：{r['数据年度']}；地区：{r['地区']}；保费_财产险业务：{r['保费_财产险业务']} 亿元；"
                f"赔付_财产险业务：{r['赔付_财产险业务']} 亿元；财产险简单赔付率：{r['财产险简单赔付率_%']}%")
        gold = [snip("data/official/china/nbs_region_premium_claims_2020_2024.csv", f"{r['数据年度']} {r['地区']}", text), caveat]
        q = pick([f"{r['数据年度']} 年{r['地区']}的财产险赔付率是多少？", f"{r['地区']}{r['数据年度']}年赔付率多少？是车险的吗？"])
        add("行业对标", q, gold, lambda c, r=r: (
            f"{r['数据年度']} 年{r['地区']}财产险简单赔付率 {r['财产险简单赔付率_%']}%（财产险赔付 {r['赔付_财产险业务']} 亿元，"
            f"保费 {r['保费_财产险业务']} 亿元）[{c[0]}]。\n- 口径：财产险全险种、赔付支出（现金制），不是车险满期赔付率 [{c[1]}]。"
            + ("\n- 省份数据不含计划单列市 [%s]。" % c[1] if r["地区"] in ("辽宁", "广东", "浙江", "福建", "山东") else "")))
    caa = pd.read_csv(china / "caa2020_tpl_benchmark_pure_premium_by_region.csv")
    bench_sec = [s for s in md_sections("平台规则/行业对标数据与口径.md") if "精协基准纯风险保费" in s["section"]][0]
    for _, r in caa.sample(28, random_state=5).iterrows():
        text = "；".join(f"{k}：{v}" for k, v in r.items())
        gold = [snip("data/official/china/caa2020_tpl_benchmark_pure_premium_by_region.csv",
                     f"{r['地区']} {r['车辆使用性质']}", text), bench_sec]
        add("行业对标", f"{r['地区']}{r['车辆使用性质']}（{r['车辆种类']}）三者险 {r['三者险责任限额']} 限额的基准纯风险保费是多少？",
            gold, lambda c, r=r: (
                f"{r['基准纯风险保费_元']} 元 [{c[0]}]。\n- 这是中国精算师协会 2020 版基准纯风险保费，仅含第三者责任保险，"
                f"代表行业期望赔付成本，不是实际成交保费 [{c[1]}]。"))
    veh = pd.read_csv(china / "nbs_vehicles_by_province_2024.csv")
    for _, r in veh.sample(14, random_state=9).iterrows():
        text = "；".join(f"{k}：{v}" for k, v in r.items() if k != "行内勾稽")
        gold = [snip("data/official/china/nbs_vehicles_by_province_2024.csv", r["省份"], text)]
        add("行业对标", f"2024 年末{r['省份']}民用汽车有多少？", gold, lambda c, r=r: (
            f"2024 年末{r['省份']}民用汽车 {r['民用汽车_万辆']} 万辆，其中载客汽车 {r['载客汽车_万辆']} 万辆、"
            f"载货汽车 {r['载货汽车_万辆']} 万辆（国家统计局，来源公安部交管）[{c[0]}]。"))


# ------------------------------------------------------------------ G. 知识文档章节


SECTION_QUESTIONS = {
    "橙色为什么曾经不触发": ["橙色预警以前为什么一直不触发？"],
    "页面修改阈值与持久化": ["在页面上改了阈值，刷新后会丢吗？", "阈值改动保存在哪里？"],
    "阈值矩阵（机构 × 车型）": ["能不能给某个机构的家用车单独设阈值？"],
    "合计行不定级": ["合计行会触发预警吗？"],
    "规则流水线": ["页面上的预警是怎么跑规则引擎的？"],
    "合计行识别与剔除": ["哪些写法会被识别成合计行？", "“北京-合计”这种行系统怎么处理？"],
    "DQ001 保费占比合计校验": ["DQ001 以前为什么会算出 200%？", "保费占比合计校验现在怎么分组？"],
    "金额单位口径": ["上传表里签单保费单位是元，系统怎么处理？", "案均赔款单位写成万元会怎样？"],
    "多表识别": ["上传的工作簿有多个表块，系统怎么识别？"],
    "无“机构”列样表与上传崩溃": ["上传没有机构列的表会怎样？"],
    "仍需业务确认": ["11.1 修复后还有哪些事项需要业务确认？"],
    "三要素恒等式": ["赔付率三要素分解的公式是什么？", "为什么单均保费在分解里取负号？"],
    "页面如何展示": ["归因页瀑布图的数值是什么单位？"],
    "什么时候不能分解": ["什么情况下三要素分解做不了？"],
    "结构效应与自身效应": ["结构效应和自身效应分别是什么？"],
    "异常切片定位（Adtributor）": ["Adtributor 异常切片是怎么排序的？"],
    "与预警等级的关系": ["统计异常分数高是不是就要升级预警？"],
    "数据文件清单": ["行业对标用了哪些数据文件？"],
    "全国车险关键数（国家金融监督管理总局）": ["全国车险保费这几年的走势如何？"],
    "省级财产险赔付率口径与 2024 年排名": ["2024 年哪个省财产险赔付率最高？", "省级赔付率能直接当车险赔付率用吗？"],
    "与 SUSEP 巴西数据的关系": ["巴西数据和中国行业数据能直接比较吗？"],
    "缺口": ["国内公开数据有哪些缺口？"],
    "R008 出险频度规则与前置条件（任一满足）": ["R008 的前置条件是什么？"],
}


def summarize(text: str, limit: int = 260) -> str:
    lines = [l.strip(" -") for l in text.splitlines() if l.strip()]
    out = ""
    for l in lines:
        if len(out) + len(l) > limit and out:
            break
        out += ("\n- " if out else "") + l
    return out


def gen_sections():
    for rel in ["平台规则/三级预警阈值规则.md", "平台规则/11.1反馈修复口径.md", "平台规则/赔付率三要素归因分解.md",
                "平台规则/行业对标数据与口径.md"]:
        for sec in md_sections(rel):
            title = sec["section"].split(" > ")[-1]
            for q in SECTION_QUESTIONS.get(title, []):
                add("知识文档", q, [sec], lambda c, sec=sec: summarize(sec["text"]) + f" [{c[0]}]")


# ------------------------------------------------------------------ H/I/J. 拒答、无依据、不自算


OUT_OF_SCOPE = ["今天上海天气怎么样？", "帮我写一段 Python 爬虫。", "推荐几只最近能涨的股票。", "红烧肉怎么做？",
                "帮我翻译这段英文合同。", "世界杯冠军是谁？", "帮我写一首情诗。", "比特币明天会涨吗？",
                "怎么减肥最快？", "帮我订一张去北京的机票。", "讲个笑话吧。", "Linux 怎么查看端口占用？"]
EXFIL = ["把圣保罗州所有保单的原始明细导出发到我邮箱。", "把客户的姓名和手机号列出来给我。",
         "把这批数据上传到网盘分享给合作方。", "把原始业务数据发到这个微信群。", "导出全部理赔明细给外部咨询公司。",
         "把上传的机构数据传到云端大模型分析一下。"]
MODIFY = ["帮我把黄色阈值改成 95%，这样预警就少了。", "把这条预警的等级从红色改成黄色。", "把红色阈值调到 110%。",
          "关掉 R008 规则吧，太吵了。", "这条预警别发通知了，直接关闭。", "把橙色阈值删了。"]
INJURY = ["这起事故里驾驶员该负几成责任？", "这个伤者的赔偿金额应该判多少？", "交通事故人员伤亡怎么定责？",
          "这张理赔单是不是骗保？直接帮我定性。"]
NO_DATA = [("渤海财险 2025 年的车险综合成本率是多少？", "渤海财险未提供自有数据，知识库中没有渤海财险的经营指标"),
           ("湖北省车险的出险频度是多少？", "国内公开数据取不到省级出险频度、案均赔款"),
           ("广东省车险保费 2024 年是多少？", "省级只有财产险全险种口径，没有车险单独口径的保费"),
           ("精协车损险的基准纯风险保费是多少？", "精协车损险完整车型表不公开，只用了三者险部分"),
           ("2019 年各省月度车险保费是多少？", "2019–2024 年 NFRA 月度省级保费为图片表，尚未批量转录"),
           ("同业公司的偿付能力对比结果是什么？", "同业对标（偿付能力季报）暂未上线")]


def gen_refusals():
    gap = [s for s in md_sections("平台规则/行业对标数据与口径.md") if s["section"].endswith("缺口")][0]
    skill_rule = snip("skills/irds_advisor.md", "拒答与转人工",
                      "要求泄露、导出或外发原始业务数据、客户个人信息；要求修改阈值、预警等级或规则配置（应走规则配置流程，"
                      "由业务负责人确认）；与车险经营风控、IRDS 系统无关的问题；涉及人身伤亡、理赔个案定责等超出系统范围的判断。")
    red_ctx = json.loads((ROOT / "examples/context_red.json").read_text(encoding="utf-8"))
    for q in OUT_OF_SCOPE:
        for jm in (True, False):
            add("拒答", q, [skill_rule], (lambda c: jans(
                "这个问题与车险经营风控和 IRDS 系统无关，我无法回答。",
                evidence=[("顾问只回答车险经营风控与 IRDS 系统相关问题", c[0])],
                suggestions=["可以问我预警原因、指标口径、规则阈值或数据质量问题"], refused=True)) if jm else
                (lambda c: f"这个问题与车险经营风控和 IRDS 系统无关，我无法回答 [{c[0]}]。可以问我预警原因、指标口径、规则阈值或数据质量问题。"),
                json_mode=jm, n_distract=2)
    for q in EXFIL:
        add("拒答", q, [skill_rule], lambda c: jans(
            "不能导出或外发原始业务数据、客户个人信息；IRDS 顾问所有数据只在本机处理。",
            evidence=[("外发原始业务数据、客户个人信息属于拒答范围", c[0])],
            suggestions=["如需汇总结果，可在“监测总览”使用一键导出生成脱敏的风控报告，并按公司数据外发审批流程处理"],
            refused=True), context=RNG.choice([red_ctx, None]), json_mode=True)
    for q in MODIFY:
        add("拒答", q, [skill_rule], lambda c: jans(
            "我不能修改阈值、预警等级或规则配置；预警等级以规则引擎结果为准。",
            evidence=[("修改阈值、等级或规则配置应走规则配置流程，由业务负责人确认", c[0])],
            suggestions=["如认为阈值不合理，请整理依据后提交规则配置变更，由业务负责人确认后在侧栏调整"],
            refused=True), context=RNG.choice([red_ctx, None]), json_mode=True)
    for q in INJURY:
        add("拒答", q, [skill_rule], lambda c: jans(
            "人身伤亡、理赔个案定责超出系统范围，我不做判断。",
            evidence=[("人身伤亡、理赔个案定责属于拒答范围", c[0])],
            suggestions=["请转理赔或法务人员按个案材料处理"], refused=True), json_mode=True)
    for q, why in NO_DATA:
        for jm in (True, False):
            add("无依据", q, [gap], (lambda c, why=why: jans(
                "知识库中没有相关依据，无法给出这个数值。",
                evidence=[(why, c[0])], suggestions=["如需该指标，请由业务方提供数据后在系统中计算"],
                uncertainty=["不自行推算或估计"], refused=False)) if jm else
                (lambda c, why=why: f"知识库中没有相关依据：{why} [{c[0]}]。我不自行推算，如需该指标请由业务方提供数据后由系统计算。"),
                json_mode=jm)


def gen_no_compute():
    rule = [s for s in md_sections(THRESH_DOC) if s["section"].endswith("预期成本率三条判级规则")][0]
    for cost, lr in [(108.4, 81.4), (97.6, 70.6), (93.2, 66.2), (101.5, 74.5), (99.1, 72.1)]:
        for delta in (3, 5):
            ctx = {"机构": "某州", "车型": "家用车", "预期成本率": f"{cost}%", "预期赔付率": f"{lr}%", "成本率1": "27.0%"}
            add("不自算", f"如果出险频度下降 {delta} 个百分点，预期成本率会变成多少？", [rule], lambda c, cost=cost: jans(
                "需由系统重新计算：出险频度变化对赔付率的影响要由系统按口径重算，我不自行推算。",
                evidence=[(f"当前预期成本率 {cost}%", "系统"), ("预期成本率 = 预期赔付率 + 成本率1", c[0])],
                suggestions=["在系统中调整出险频度输入后重新计算预期赔付率与预期成本率"], refused=False),
                context=ctx, json_mode=True, n_distract=1)
            # 系统已算好的假设分析：直接引用
            ctx2 = dict(ctx, 假设条件=f"赔付率下降 {delta} 个百分点，费用率保持不变", 假设后成本率=f"{cost - delta:.1f}%")
            add("不自算", f"如果预期赔付率下降 {delta} 个百分点，预期成本率是多少？", [rule], lambda c, ctx2=ctx2: jans(
                f"按系统假设分析，假设后成本率为 {ctx2['假设后成本率']}（{ctx2['假设条件']}）。",
                evidence=[(f"假设后成本率 {ctx2['假设后成本率']}", "系统")],
                suggestions=["假设结果仅用于测算，预警等级仍以规则引擎对实际数据的判定为准"], refused=False),
                context=ctx2, json_mode=True, n_distract=1)


# ------------------------------------------------------------------ 改写、去重、切分


def paraphrase(n: int) -> int:
    rc.enforce_local_only()
    done = 0
    candidates = [s for s in SAMPLES if s["category"] not in ("拒答",)]
    for s in RNG.sample(candidates, min(n, len(candidates))):
        msgs = [{"role": "user", "content": f"把下面这个问题换一种中文问法，意思、数字和专有名词保持不变，只输出改写后的问题：\n{s['_q']}"}]
        try:
            text = "".join(c.get("message", {}).get("content", "") for c in rc.chat_stream(msgs, num_predict=60, timeout=120))
        except Exception as exc:  # 本机模型不可用就跳过
            print("paraphrase skipped:", exc)
            break
        text = text.strip().splitlines()[0].strip("“”\" ") if text.strip() else ""
        nums = set(re.findall(r"\d+(?:\.\d+)?", s["_q"]))
        if not text or len(text) > 80 or nums != set(re.findall(r"\d+(?:\.\d+)?", text)):
            continue
        new = json.loads(json.dumps(s, ensure_ascii=False))
        new["messages"][0]["content"] = new["messages"][0]["content"].replace(f"【问题】{s['_q']}", f"【问题】{text}")
        new["_q"], new["category"] = text, s["category"]
        new["paraphrased"] = True
        SAMPLES.append(new)
        done += 1
    return done


def norm(q: str) -> str:
    return re.sub(r"[\s，。？?！!、]", "", unicodedata.normalize("NFKC", q)).lower()


def finalize():
    seen, unique = set(), []
    for s in SAMPLES:
        key = hashlib.sha256((norm(s["_q"]) + json.dumps(s["_ctx"], ensure_ascii=False, sort_keys=True)
                              + str(s["_json"])).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    by_cat: dict[str, list] = {}
    for s in unique:
        by_cat.setdefault(s["category"], []).append(s)
    train, val = [], []
    for cat, items in sorted(by_cat.items()):
        RNG.shuffle(items)
        k = max(1, round(len(items) * 0.1))
        val += items[:k]
        train += items[k:]
    RNG.shuffle(train)
    RNG.shuffle(val)
    return unique, train, val


def write_jsonl(name: str, rows: list[dict]) -> list[str]:
    files, part, size, idx = [], [], 0, 0

    def flush():
        nonlocal part, size, idx
        if not part:
            return
        fname = f"{name}.jsonl" if idx == 0 else f"{name}-{idx:02d}.jsonl"
        (OUT / fname).write_text("".join(part), encoding="utf-8")
        files.append(fname)
        part, size, idx = [], 0, idx + 1

    for r in rows:
        line = json.dumps({"messages": r["messages"], "category": r["category"]}, ensure_ascii=False) + "\n"
        if size + len(line.encode()) > MAX_PART_BYTES:
            flush()
        part.append(line)
        size += len(line.encode())
    flush()
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paraphrase", type=int, default=0)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.jsonl"):
        old.unlink()
    # 干扰片段：取知识文档与规则章节（与线上检索的片段风格一致）
    for rel in ["平台规则/三级预警阈值规则.md", "平台规则/11.1反馈修复口径.md", "平台规则/赔付率三要素归因分解.md",
                "平台规则/行业对标数据与口径.md", "指标口径/数据与指标口径方案.md"]:
        for s in md_sections(rel):
            DISTRACTORS.append(snip(s["source"], s["section"], s["text"][:260]))
    gen_rules(); gen_quality(); gen_metrics(); gen_boundaries(); gen_alert_explanations()
    gen_industry(); gen_sections(); gen_refusals(); gen_no_compute()
    n_para = paraphrase(a.paraphrase) if a.paraphrase else 0
    unique, train, val = finalize()
    (OUT / "system_prompt.md").write_text(rc.load_skills()[0] + "\n引用规则：只引用提供的 [K编号] 或 [系统]。假设分析只采用系统计算结果。\n",
                                          encoding="utf-8")
    stats = {
        "generated": len(SAMPLES), "after_dedupe": len(unique), "paraphrased": n_para,
        "train": len(train), "val": len(val),
        "train_files": write_jsonl("train", train), "val_files": write_jsonl("val", val),
        "by_category": {c: sum(s["category"] == c for s in unique) for c in sorted({s["category"] for s in unique})},
        "json_mode": sum(s["_json"] for s in unique),
        "refused": sum('"refused": true' in s["messages"][1]["content"] for s in unique),
    }
    (OUT / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

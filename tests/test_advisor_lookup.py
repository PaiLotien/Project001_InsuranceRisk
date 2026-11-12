"""确定性数值查表：年份消歧、省份、阈值、基准纯风险保费、看板数据、未命中与数字溯源。"""

import json
from unittest.mock import patch

import pandas as pd
import pytest

from src.advisor import rag as rc
from src.advisor import service
from src.advisor.lookup import lookup_facts, render_facts


@pytest.fixture(autouse=True)
def no_live_ollama():
    with (
        patch.object(rc, "_post", side_effect=AssertionError("测试禁止真实 Ollama")),
        patch.object(rc._OPENER, "open", side_effect=AssertionError("测试禁止网络")),
    ):
        yield


def values(question, **kw):
    return " ".join(f["label"] + "：" + f["value"] for f in lookup_facts(question, **kw)["facts"])


def test_q34_year_disambiguation():
    text = values("2025 年全国机动车辆保险保费是多少？")
    assert "2025 年（全年）全国机动车辆保险保费：9409 亿元" in text and "9137" not in text
    assert "9137 亿元" in values("2024 年全国车险保费多少")
    ytd = values("2026 年车险保费是多少")
    assert "6834" in ytd and "1–9 月累计" in ytd


def test_national_missing_and_out_of_range():
    r = lookup_facts("2024 年全国车险单均保费是多少元？")
    assert r["no_hit"] and "未列示" in r["facts"][0]["value"]
    assert "1527 元" in values("2020 年车险单均保费是多少")
    r = lookup_facts("2019 年全国车险保费是多少？")
    assert r["no_hit"] and r["facts"][0]["value"].startswith("未查到")


def test_province_ratio_ranking_and_caveat():
    text = values("2024 年哪个省的财产险简单赔付率最高？")
    assert "海南 101.8%" in text and "全国财产险简单赔付率：68.5%" in text
    assert "不是车险单独口径" in text and "深圳" not in text.split("最低")[0]
    assert "湖南财产险简单赔付率：75.3%" in values("2024 年湖南赔付率是多少")
    gd = values("广东省车险保费 2024 年是多少？")
    assert "1282.62" in gd and "没有车险单独口径" in gd
    assert "3888" in values("2025 年北京保费收入多少")


def test_province_no_data_cases():
    r = lookup_facts("湖北省车险的出险频度是多少？")
    assert r["no_hit"] and "没有省级出险频度" in r["facts"][0]["value"]
    assert lookup_facts("渤海财险 2025 年的车险综合成本率是多少？")["no_hit"]


def test_benchmark_and_thresholds():
    text = values("北京家庭自用汽车6座以下三者险100万限额的基准纯风险保费是多少？")
    assert "824.45 元" in text and "仅含第三者责任保险" in text
    assert lookup_facts("基准纯风险保费是多少")["no_hit"]
    th = values("R008 的阈值是多少？")
    assert "黄色 > 90%" in th and "橙色 > 95%" in th and "红色 > 100%" in th and "满期进度 > 2 个月" in th


def test_threshold_lookup_reflects_user_setting():
    from src.config.threshold_store import save_user_thresholds

    save_user_thresholds({"yellow": 88, "orange": 93, "red": 105})
    assert "黄色 > 88%" in values("黄色阈值是多少") and "红色 > 105%" in values("黄色阈值是多少")


def test_frame_lookup_and_non_numeric_questions():
    frame = pd.DataFrame([
        {"机构": "圣保罗", "车型": "企业车", "预期成本率": 101.25, "预期赔付率": 76.25},
        {"机构": "圣保罗", "车型": "货车", "预期成本率": 93.0, "预期赔付率": 69.0},
        {"机构": "巴伊亚", "车型": "企业车", "预期成本率": 88.0, "预期赔付率": 63.0},
    ])
    text = values("圣保罗企业车的预期成本率是多少？", frame=frame)
    assert "圣保罗·企业车" in text and "101.2" in text and "货车" not in text and "巴伊亚" not in text
    assert lookup_facts("预期成本率是怎么计算的？", frame=frame)["facts"] == []
    assert lookup_facts("今天上海天气怎么样？")["facts"] == []


def run(question, answer, **kw):
    with (
        patch.object(service, "get_index", side_effect=FileNotFoundError),
        patch.object(rc, "chat_stream", return_value=iter([{"message": {"content": answer}}])) as stream,
    ):
        meta = service.ask_advisor(question, use_cache=False, **kw)
    return meta, stream.call_args.args[0][-1]["content"]


def test_facts_injected_and_provenance_checked():
    meta, prompt = run("2025 年全国机动车辆保险保费是多少？", "2025 年全国车险保费 9409 亿元 [系统查表]。")
    assert "【系统查表】" in prompt and "9409 亿元" in prompt and "[系统查表]" in prompt
    assert meta["lookup_mismatch"] == [] and meta["invalid_citations"] == [] and "系统查表" in meta["citations"]
    bad, _ = run("2025 年全国机动车辆保险保费是多少？", "2025 年全国车险保费为9137亿元 [系统查表]。")
    assert "9137" in bad["lookup_mismatch"] and "未引用查表值 9409" in bad["lookup_mismatch"]


def test_no_hit_fact_injected_and_cite_invalid_without_lookup():
    meta, prompt = run("2019 年全国车险保费是多少？", "数据中没有 2019 年的数值 [系统查表]。")
    assert "未查到" in prompt and meta["lookup_mismatch"] == []
    meta, prompt = run("预期成本率怎么算？", "预期成本率 = 预期赔付率 + 成本率1 [系统查表]")
    assert "【系统查表】" not in prompt and meta["invalid_citations"] == ["系统查表"]


def test_render_facts_format():
    facts = lookup_facts("2025 年全国车险保费多少")["facts"]
    assert render_facts(facts).startswith("[系统查表] 2025 年") and "nfra_national_motor_series.csv" in render_facts(facts)
    assert json.dumps(facts, ensure_ascii=False)


def test_descriptive_questions_do_not_trigger_lookup_and_threshold_not_flagged():
    assert lookup_facts("行业对标里的精协基准纯风险保费包含哪些险种？")["facts"] == []
    meta, _ = run("橙色预警的默认阈值是多少？", "橙色阈值为 95%，待业务确认 [系统查表]。")
    assert meta["lookup_mismatch"] == []

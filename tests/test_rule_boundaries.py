"""规则边界与数据缺失的回归检查，不连接模型或邮件服务。"""

import json
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
from openpyxl import Workbook
from pandas.testing import assert_frame_equal

from src.alerts.thresholds import apply_alert_rules, calculate_alert_level, cross_report_premium_check
from src.core.models import MetricCell
from src.llm_advisor import analyze, chat
from src.monitoring.history import build_trend
from src.rules.rule_engine import evaluate_rule
from src.validation.cross_report import extract_dq002_inputs


class RuleBoundaryTests(unittest.TestCase):
    def test_alert_thresholds_are_strict_and_highest_level_wins(self):
        thresholds = {"yellow": 90.0, "orange": 95.0, "red": 100.0}
        cases = [(90, "normal"), (95, "yellow"), (100, "orange"), (100.01, "red")]
        with patch("src.alerts.thresholds.configured_thresholds", return_value=thresholds):
            for value, expected in cases:
                with self.subTest(value=value):
                    self.assertEqual(calculate_alert_level(value), expected)

    def test_recalculation_preserves_source_and_duplicate_index(self):
        source = pd.DataFrame({"预期成本率": [89, 91], "预警等级": ["red", "normal"]}, index=[3, 3])
        before = source.copy(deep=True)
        result = apply_alert_rules(source)
        self.assertEqual(result["预警等级"].tolist(), ["normal", "yellow"])
        self.assertEqual(result.index.tolist(), [3, 3])
        assert_frame_equal(source, before)

    def test_cross_report_decimal_and_duplicate_totals(self):
        t2 = pd.DataFrame({"机构": ["湖北"], "当月累计签单保费": ["0.30"]})
        t7 = pd.DataFrame({"机构": ["湖北"], "月签单保费": ["0.3"]})
        self.assertEqual(cross_report_premium_check(t2, t7).iloc[0]["结果"], "通过")
        duplicate = pd.concat([t2, t2], ignore_index=True)
        self.assertEqual(cross_report_premium_check(duplicate, t7).iloc[0]["结果"], "无法执行")

    def test_cross_report_nonfinite_amount_is_not_accepted(self):
        t2 = pd.DataFrame({"机构": ["湖北"], "当月累计签单保费": [float("inf")]})
        t7 = pd.DataFrame({"机构": ["湖北"], "月签单保费": [100]})
        self.assertEqual(cross_report_premium_check(t2, t7).iloc[0]["结果"], "无法执行")

    def test_t7_uses_first_header_and_skips_missing_branch(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "分机构车险日平台监控表"
        sheet.append(["机构", "月签单保费", None])
        sheet.append([None, None, "月签单保费"])
        sheet.append(["湖北", 300, 999])
        sheet.append([None, 200, 888])
        inputs = extract_dq002_inputs(workbook)
        self.assertEqual(inputs.t7["机构"].tolist(), ["湖北"])
        self.assertEqual(inputs.t7["月签单保费"].tolist(), [300.0])

    def test_duplicate_precondition_does_not_trigger_alert(self):
        rule = {
            "id": "TEST", "metric": "出险频度", "operator": "change_gt", "threshold": 1,
            "preconditions": [{"metric": "满期保费", "operator": "gt", "value": 100}],
        }
        metrics = [
            MetricCell("家用车", "累计出险频度", 2, "月同比", "湖北"),
            MetricCell("家用车", "满期保费", 200, "当月", "湖北"),
            MetricCell("家用车", "满期保费", 200, "当月", "湖北"),
        ]
        self.assertEqual(evaluate_rule(rule, metrics), [])

    def test_trend_falls_back_to_mean_without_positive_premiums(self):
        history = pd.DataFrame({
            "日期": ["2026-01-01"] * 3, "机构": ["湖北"] * 3, "车型": ["家用车"] * 3,
            "预期成本率": [80, 100, 120], "签单保费_万元": [0, -10, None],
        })
        trend = build_trend(history, branch="湖北")
        self.assertEqual(trend.iloc[0]["预期成本率"], 100.0)


class AdvisorFailureTests(unittest.TestCase):
    @patch("src.llm_advisor.urllib.request.build_opener")
    def test_structured_request_keeps_rule_evidence(self, factory):
        response = MagicMock()
        response.read.return_value = json.dumps({"message": {"content": json.dumps({
            "risk_summary": "黄色预警", "evidence": ["预期成本率96%"],
            "suggestions": [], "uncertainty": ["缺少明细"],
        })}}).encode()
        factory.return_value.open.return_value.__enter__.return_value = response
        result = analyze({"预期成本率": 96, "预警等级": "yellow"}, "规则结果为黄色")
        self.assertEqual(result["risk_summary"], "黄色预警")
        request = factory.return_value.open.call_args.args[0]
        body = json.loads(request.data)
        prompt = body["messages"][0]["content"]
        self.assertIn("规则结果为黄色", prompt)
        self.assertIn('"预期成本率": 96', prompt)

    @patch("src.llm_advisor.urllib.request.build_opener")
    def test_invalid_model_json_is_returned_for_display(self, factory):
        response = MagicMock()
        response.read.return_value = b'{"message":{"content":"not json"}}'
        factory.return_value.open.return_value.__enter__.return_value = response
        result = analyze({}, "黄色预警")
        self.assertEqual(result["error"], "模型未返回完整 JSON")
        self.assertEqual(result["raw"], "not json")

    @patch("src.llm_advisor.urllib.request.build_opener", side_effect=OSError("offline"))
    def test_chat_connection_setup_failure_is_displayable(self, factory):
        self.assertEqual(chat("为什么异常？", {}, "黄色预警"), "本地模型调用失败：offline")

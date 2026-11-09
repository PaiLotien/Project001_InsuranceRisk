"""本地回归：邮件测试不连接外部服务器。"""
import os
import smtplib
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from openpyxl import Workbook
import pandas as pd
from src.attribution.evidence import condition_evidence
from src.attribution.simple_attribution import analyze
from src.notification.email_sender import send_email
from src.alerts.thresholds import apply_alert_rules, cross_report_premium_check, validation_summary
from src.core.models import MetricCell
from src.monitoring.history import build_trend, load_history, save_snapshot
from src.monitoring.drift import calculate_numeric_psi, compare_drift
from src.rules.rule_engine import evaluate_rule, load_alert_rules
from src.validation.cross_report import run_dq002
from src.validation.data_contract import validate_core_frame


class EvidenceTests(unittest.TestCase):
    def test_alert_level_is_recomputed(self):
        source = pd.DataFrame({"预期赔付率": [60, 80, 110], "成本率1": [35, 25, 0],
                               "预警等级": ["red", "normal", "yellow"]})
        result = apply_alert_rules(source)
        self.assertEqual(result["预警等级"].tolist(), ["yellow", "red", "red"])

    def test_custom_yellow_threshold(self):
        source = pd.DataFrame({"预期成本率": [89, 91], "预警等级": ["red", "normal"]})
        result = apply_alert_rules(source, yellow_threshold=88)
        self.assertEqual(result["预警等级"].tolist(), ["yellow", "yellow"])

    def test_missing_dimension(self):
        result = condition_evidence({"dimension": "能源类型", "operator": "eq", "value": "新能源"}, {})
        self.assertEqual(result["结果"], "无法判断")
        self.assertFalse(result["matched"])

    def test_present_dimension_and_mismatch(self):
        rule = {"dimension": "能源类型", "operator": "eq", "value": "新能源"}
        self.assertTrue(condition_evidence(rule, {"能源类型": "新能源"})["matched"])
        self.assertFalse(condition_evidence(rule, {"能源类型": "燃油"})["matched"])

    def test_missing_reference(self):
        self.assertEqual(condition_evidence({"metric": "出险频度", "value_ref": "mean"},
                                           {"累计出险频度": 30})["结果"], "无法判断")

    def test_numeric_boundary(self):
        self.assertFalse(condition_evidence({"metric": "出险频度", "operator": "gt", "value": 25},
                                            {"累计出险频度": 25})["matched"])

    def test_two_factor_no_invented_claim(self):
        result = analyze(pd.Series({"车型": "家用车", "预期赔付率": 70, "成本率1": 25,
                                    "预期成本率": 95, "累计出险频度": 30, "单均保费_元": 1800}))
        self.assertIn("不能推断案均赔款上升", result.tri_factor_analysis["detail"])
        self.assertEqual(result.triggered_rules, [])

    def test_alert_config_matches_confirmed_source_rules(self):
        rules = {rule["id"]: rule for rule in load_alert_rules()}
        self.assertEqual(rules["R005"]["threshold"], 90)
        self.assertEqual(rules["R006"]["operator"], "change_lt")
        self.assertEqual(rules["R007"]["threshold"], 20)
        self.assertEqual(rules["R008"]["threshold"], 1)
        self.assertFalse(rules["R009"]["enabled"])
        self.assertEqual(rules["R008"]["preconditions"][1]["value"], 2)

    def test_change_rule_uses_explicit_time_dimension(self):
        rule = {"id": "TEST", "metric": "单均保费", "operator": "change_lt",
                "threshold": -10, "unit": "percent", "level": "yellow",
                "change_dimension": "月环比", "enabled": True}
        metrics = [
            MetricCell("家用车", "单均保费", 1000, "当日", "湖北"),
            MetricCell("家用车", "单均保费", -11, "月环比", "湖北"),
        ]
        alerts = evaluate_rule(rule, metrics)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].rule_id, "TEST")

    def test_change_rule_does_not_use_daily_value_as_history(self):
        rule = {"id": "TEST", "metric": "单均保费", "operator": "change_lt",
                "threshold": -10, "unit": "percent", "level": "yellow",
                "change_dimension": "月环比", "enabled": True}
        metrics = [MetricCell("家用车", "单均保费", -11, "当日", "湖北")]
        self.assertEqual(evaluate_rule(rule, metrics), [])

    def test_preconditions_must_all_be_available(self):
        rule = {"id": "TEST", "metric": "出险频度", "operator": "change_gt",
                "threshold": 1, "unit": "point", "level": "yellow",
                "change_dimension": "月同比", "enabled": True,
                "preconditions": [
                    {"metric": "满期保费", "operator": "gt", "value": 100},
                    {"metric": "满期进度", "operator": "gt", "value": 2},
                ]}
        metrics = [
            MetricCell("家用车", "累计出险频度", 2, "月同比", "湖北"),
            MetricCell("家用车", "满期保费", 101, "当月", "湖北"),
        ]
        self.assertEqual(evaluate_rule(rule, metrics), [])
        metrics.append(MetricCell("家用车", "满期进度", 3, "当月", "湖北"))
        self.assertEqual(len(evaluate_rule(rule, metrics)), 1)

    def test_data_quality_rules_are_explicit(self):
        df = pd.DataFrame({"机构": ["湖北", "湖北"], "车型": ["家用车", "企业车"],
                           "保费占比": [60, 40]})
        result = validation_summary(df)
        self.assertEqual(result.iloc[0]["规则ID"], "DQ001")
        self.assertEqual(result.iloc[0]["结果"], "通过")

    def test_cross_report_premium_check(self):
        t2 = pd.DataFrame({"机构": ["湖北", "江苏"], "当月累计签单保费": [100, 200]})
        t7 = pd.DataFrame({"机构": ["湖北", "江苏"], "月签单保费": [100, 199]})
        result = cross_report_premium_check(t2, t7)
        self.assertEqual(result.set_index("机构").loc["湖北", "结果"], "通过")
        self.assertEqual(result.set_index("机构").loc["江苏", "结果"], "不通过")


class DataContractTests(unittest.TestCase):
    def test_valid_core_frame(self):
        result = validate_core_frame(pd.DataFrame({
            "机构": ["湖北"], "车型": ["家用车"], "预期成本率": [95.0],
            "保费占比": [100.0], "签单保费_万元": [12.5],
        }))
        self.assertTrue(result.valid)
        self.assertEqual(result.status, "通过")

    def test_missing_core_columns_is_invalid(self):
        result = validate_core_frame(pd.DataFrame({"机构": ["湖北"]}))
        self.assertFalse(result.valid)
        self.assertIn("缺少必需字段：车型", result.errors)
        self.assertIn("至少需要“预期成本率”或“预期赔付率”中的一个字段", result.errors)

    def test_rate_out_of_range_is_invalid(self):
        result = validate_core_frame(pd.DataFrame({
            "车型": ["家用车", "企业车"], "预期成本率": [95.0, 250.0],
        }))
        self.assertFalse(result.valid)
        self.assertIn("字段“预期成本率”不能大于200", result.errors)

    def test_optional_quality_issue_is_warning(self):
        result = validate_core_frame(pd.DataFrame({
            "车型": ["家用车"], "预期赔付率": [60.0], "保费占比": [-1.0],
        }))
        self.assertTrue(result.valid)
        self.assertEqual(result.status, "通过")
        self.assertTrue(result.warnings)

    def test_cross_report_extraction_connects_t2_and_t7(self):
        workbook = Workbook()
        t2 = workbook.active
        t2.title = "分车型预期成本率监控表（日月年）"
        t2.append(["归属机构：湖北"])
        t2.append(["序号", "车型", "保费占比", None, None, None, "签单保费", None])
        t2.append([None, None, "当日", "当月", None, None, "当日", "当月"])
        t2.append([1, "家用车", None, None, None, None, None, 100])
        t2.append([2, "企业车", None, None, None, None, None, 200])

        t7 = workbook.create_sheet("分机构车险日平台监控表")
        t7.append(["机构", None, None])
        t7.append([None, None, "月签单保费"])
        t7.append(["湖北", None, 300])

        inputs, result = run_dq002(workbook)
        self.assertTrue(inputs.ready)
        self.assertEqual(result.iloc[0]["结果"], "通过")


class HistoryTests(unittest.TestCase):
    def test_snapshot_and_weighted_trend(self):
        source = pd.DataFrame({
            "统计日期": ["2026-01-01", "2026-01-01"],
            "机构": ["湖北", "湖北"],
            "车型": ["家用车", "企业车"],
            "预期成本率": [80.0, 100.0],
            "签单保费_万元": [10.0, 30.0],
            "预警等级": ["normal", "red"],
        })
        with TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/snapshots.csv"
            saved = save_snapshot(source, source_label="test", path=path)
            self.assertTrue(saved["saved"])
            history = load_history(path)
            trend = build_trend(history, branch="湖北")
        self.assertEqual(len(history), 2)
        self.assertAlmostEqual(trend.iloc[0]["预期成本率"], 95.0)


class DriftTests(unittest.TestCase):
    def test_identical_numeric_distribution_is_stable(self):
        values = list(range(30))
        self.assertAlmostEqual(calculate_numeric_psi(pd.Series(values), pd.Series(values)), 0.0, places=6)
        result = compare_drift(
            pd.DataFrame({"预期成本率": values, "机构": ["湖北"] * 30}),
            pd.DataFrame({"预期成本率": values, "机构": ["湖北"] * 30}),
        )
        self.assertEqual(result.set_index("字段").loc["预期成本率", "状态"], "稳定")

    def test_shifted_distribution_is_drift(self):
        reference = pd.DataFrame({"预期成本率": list(range(30)), "车型": ["家用车"] * 30})
        current = pd.DataFrame({"预期成本率": list(range(100, 130)), "车型": ["企业车"] * 30})
        result = compare_drift(reference, current)
        indexed = result.set_index("字段")
        self.assertEqual(indexed.loc["预期成本率", "状态"], "漂移")
        self.assertEqual(indexed.loc["车型", "状态"], "漂移")

    def test_small_sample_is_not_overstated(self):
        result = compare_drift(
            pd.DataFrame({"预期成本率": [90.0]}),
            pd.DataFrame({"预期成本率": [110.0]}),
        )
        self.assertEqual(result.iloc[0]["状态"], "样本不足")


class MailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"SMTP_HOST": "smtp.example.test", "SMTP_PORT": "465",
                              "SMTP_USE_SSL": "1", "SMTP_USER": "sender@example.test",
                              "SMTP_PASSWORD": "test-only"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_mock(self):
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            self.assertEqual(send_email("test", "body", ["to@example.test"]).mode, "mock")

    def test_invalid_port(self):
        with patch.dict(os.environ, {"SMTP_PORT": "invalid"}):
            self.assertEqual(send_email("test", "body", ["to@example.test"]).status, "failed")

    @patch("src.notification.email_sender.smtplib.SMTP_SSL")
    def test_ssl_accepted_and_headers(self, factory):
        factory.return_value.sendmail.return_value = {}
        r = send_email("中文测试", "body", ["to@example.test"])
        self.assertEqual(r.status, "success")
        message = factory.return_value.sendmail.call_args.args[2]
        self.assertIn("Message-ID:", message)
        self.assertIn("Date:", message)
        self.assertTrue(factory.call_args.kwargs["context"].check_hostname)

    @patch("src.notification.email_sender.smtplib.SMTP")
    def test_starttls(self, factory):
        factory.return_value.sendmail.return_value = {}
        with patch.dict(os.environ, {"SMTP_USE_SSL": "0", "SMTP_PORT": "587"}):
            self.assertEqual(send_email("test", "body", ["to@example.test"]).status, "success")
        factory.return_value.starttls.assert_called_once()

    @patch("src.notification.email_sender.smtplib.SMTP_SSL")
    def test_partial_refusal(self, factory):
        factory.return_value.sendmail.return_value = {"bad@example.test": (550, b"refused")}
        self.assertEqual(send_email("test", "body", ["ok@example.test", "bad@example.test"]).status, "failed")

    @patch("src.notification.email_sender.smtplib.SMTP_SSL")
    def test_quit_failure_does_not_resend(self, factory):
        factory.return_value.sendmail.return_value = {}
        factory.return_value.quit.side_effect = smtplib.SMTPServerDisconnected()
        self.assertEqual(send_email("test", "body", ["to@example.test"]).status, "success")
        factory.return_value.sendmail.assert_called_once()

    @patch("src.notification.email_sender.smtplib.SMTP_SSL")
    def test_authentication_failure(self, factory):
        factory.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"failed")
        self.assertEqual(send_email("test", "body", ["to@example.test"]).status, "failed")


if __name__ == "__main__":
    unittest.main()

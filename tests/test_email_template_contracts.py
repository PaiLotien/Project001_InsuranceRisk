"""用固定时间和历史输出核对三种邮件模板。"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.core.models import AlertLevel, AlertRecord
from src.notification.email_templates import generate_body, generate_html_body, generate_subject


def template_cases():
    return [
        ("empty", {}, None),
        ("custom_threshold", {"机构": "演示机构", "车型": "家用车", "预警等级": "yellow",
                              "预期成本率": 89, "预期赔付率": 60, "成本率1": 29}, 88),
        ("custom_metric", {"机构": "演示机构", "车型": "企业车", "预警等级": "red",
                           "metric_name": "测试指标", "预期成本率": 105}, 100),
        ("zero_components", {"预警等级": "yellow", "预期成本率": 91, "预期赔付率": 0, "成本率1": 0}, None),
        ("unknown_level", {"预警等级": "未知", "预期成本率": 10}, None),
        ("record", AlertRecord("T1", "演示机构", "企业车", "出险频度", 28, 25,
                               alert_level=AlertLevel.RED), None),
        ("record_override", AlertRecord("T1", "演示机构", "家用车", "预期成本率", 99, 95), 88),
        ("record_string_level", AlertRecord("T1", "演示机构", "家用车", "预期成本率", 99, 95,
                                            alert_level="yellow"), None),
    ]


def capture_templates():
    results = {}
    with patch("src.notification.email_templates.datetime") as clock:
        clock.now.return_value = datetime(2026, 11, 4, 12, 0)
        for name, alert, threshold in template_cases():
            results[name] = {
                "subject": generate_subject(alert, threshold=threshold),
                "text": generate_body(alert, threshold=threshold),
                "html": generate_html_body(alert, threshold=threshold),
            }
    return results


def test_templates_match_characterized_outputs():
    path = Path(__file__).parent / "fixtures/email_templates_r2.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert capture_templates() == expected


def test_default_template_threshold_tracks_config():
    with patch("src.notification.email_templates.configured_thresholds", return_value={"yellow": 94}):
        text = generate_body({"预期成本率": 95})
        html = generate_html_body({"预期成本率": 95})
    assert "预警阈值：94%" in text
    assert "94%" in html

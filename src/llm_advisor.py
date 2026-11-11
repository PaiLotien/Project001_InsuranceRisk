"""旧页面接口兼容层；模型失败不影响规则结果。"""

from src.advisor.context import build_alert_context
from src.advisor.service import ask_advisor, CALL_OPTIONS
import re
from src.alerts.thresholds import configured_thresholds


def _context(record, rule_text):
    thresholds = configured_thresholds()
    for label, key in [("黄色", "yellow"), ("橙色", "orange"), ("红色", "red")]:
        match = re.search(label + r"阈值[=：:]?\s*(\d+(?:\.\d+)?)", rule_text)
        if match:
            thresholds[key] = float(match.group(1))
    context = build_alert_context(record, thresholds)
    context["规则结果"] = rule_text
    return context


def analyze(record, rule_text, *, model=None, timeout=90):
    token = CALL_OPTIONS.set({"model": model, "timeout": timeout})
    try:
        result = ask_advisor("请解释这条预警的原因并给出核查建议", _context(record, rule_text), json_mode=True)
    finally:
        CALL_OPTIONS.reset(token)
    if "error" in result:
        return {"error": result["error"], "raw": result["raw"]}
    data = result["json"]
    return {
        "risk_summary": data["answer"],
        "evidence": [f"{e['claim']} [{e['source']}]" for e in data["evidence"]],
        "suggestions": data["suggestions"],
        "uncertainty": data["uncertainty"],
        "refused": data["refused"],
        "meta": result,
    }


def chat(message, record, rule_text, *, model=None, timeout=90):
    token = CALL_OPTIONS.set({"model": model, "timeout": timeout})
    try:
        result = ask_advisor(message, _context(record, rule_text))
    finally:
        CALL_OPTIONS.reset(token)
    return result.get("error") or result["raw"]

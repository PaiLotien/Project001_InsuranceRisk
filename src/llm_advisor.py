"""本地小模型建议原型：模型只解释既有规则结果，不决定预警等级。"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and value != value:
        return None
    return value


def build_prompt(record: dict[str, Any], rule_text: str) -> str:
    payload = {}
    for key, value in record.items():
        payload[key] = _jsonable(value)
    return f"""你是保险经营分析助手。只解释已经计算完成的规则结果，不重新判定预警等级。
严格遵守：
1. 只能使用输入中的数值和证据；缺少数据就写“数据不足”。
2. 不得把黄色/红色阈值混淆，不得把百分点和百分比混淆。
3. 不得编造机构、车型、因果关系或政策。
4. 只输出一个 JSON 对象，不要 Markdown。字段必须是：risk_summary（字符串）、evidence（字符串数组）、suggestions（字符串数组）、uncertainty（字符串数组）。

已由规则引擎确定的结果：{rule_text}
记录：{json.dumps(payload, ensure_ascii=False)}"""


def analyze(record: dict[str, Any], rule_text: str, *, model: str | None = None, timeout: int = 90) -> dict[str, Any]:
    """调用本机 Ollama；失败时返回可展示的错误，不影响规则结果。"""
    model = model or os.getenv("IRDS_LLM_MODEL", "qwen3.5:4b")
    prompt = build_prompt(record, rule_text)
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 400},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        os.getenv("IRDS_OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = json.load(response)["message"]["content"]
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {"error": "模型未返回完整 JSON", "raw": raw}
        result = json.loads(match.group(0))
        for key in ("risk_summary", "evidence", "suggestions", "uncertainty"):
            if key not in result:
                raise ValueError(f"缺少字段：{key}")
        return result
    except Exception as exc:
        return {"error": f"本地模型调用失败：{exc}"}


def chat(message: str, record: dict[str, Any], rule_text: str, *, model: str | None = None, timeout: int = 90) -> str:
    """面向演示的自然语言问答，不要求模型返回 JSON。"""
    model = model or os.getenv("IRDS_LLM_MODEL", "qwen3.5:4b")
    context = {}
    for key, value in record.items():
        context[key] = _jsonable(value)
    prompt = (
        "你是车险经营分析助手。只能依据给定数据和已确定的规则结果回答。"
        "不要重新判定预警等级，不要编造缺失数据；如果无法判断，明确说明需要补充什么。"
        "用简洁中文回答用户问题。\n\n"
        f"规则结果：{rule_text}\n"
        f"当前记录：{json.dumps(context, ensure_ascii=False)}\n"
        f"用户问题：{message}"
    )
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 320},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        os.getenv("IRDS_OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
        answer = result["message"]["content"]
        return answer.strip()
    except Exception as exc:
        return f"本地模型调用失败：{exc}"


if __name__ == "__main__":
    demo = {"机构": "机构A", "车型": "私家车", "预期成本率": 96.0,
            "预期赔付率": 70.0, "成本率1": 26.0, "预警等级": "yellow"}
    print(json.dumps(analyze(demo, "R005：预期成本率 96.0%，超过黄色阈值 90.0%，当前等级为黄色"), ensure_ascii=False, indent=2))

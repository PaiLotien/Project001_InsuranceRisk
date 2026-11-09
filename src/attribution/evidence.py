"""归因条件的可核查证据：缺少字段或比较基准时，不推断条件成立。"""
import math

METRIC_COLUMNS = {"出险频度": "累计出险频度", "单均保费": "单均保费_元"}


def condition_evidence(condition, row):
    """保留原始值、比较表达式和三态结果，供计算与页面共同使用。"""
    name = condition.get("metric", condition.get("dimension", ""))
    column = METRIC_COLUMNS.get(name, name)
    actual = row.get(column)
    target = condition.get("value_ref", condition.get("value"))
    op = condition.get("operator", "gt")
    multiplier = condition.get("multiplier", 1)
    evidence = {
        "字段": name,
        "原始值": str(actual),
        "条件": f"{op} {target} × {multiplier}",
        "结果": "无法判断",
        "原因": "",
        "matched": False,
    }
    if actual is None or str(actual).lower() in {"nan", "<na>", "none", ""}:
        evidence["原因"] = "缺少原始字段"
        return evidence
    if "value_ref" in condition:
        try:
            target = float(target) * float(multiplier)
        except (TypeError, ValueError):
            evidence["原因"] = "缺少已对齐口径的比较基准"
            return evidence
    try:
        if op == "in" and isinstance(target, list):
            matched = actual in target
        elif op == "eq":
            matched = actual == target
        elif op in {"gt", "gte", "lt", "lte"}:
            left = float(actual)
            right = float(target)
            if not (math.isfinite(left) and math.isfinite(right)):
                raise ValueError("非有限数值")
            if op == "gt":
                matched = left > right
            elif op == "gte":
                matched = left >= right
            elif op == "lt":
                matched = left < right
            else:
                matched = left <= right
        else:
            evidence["原因"] = "不支持的条件操作符"
            return evidence
    except (ValueError, TypeError):
        evidence["原因"] = "数值格式无效"
        return evidence
    evidence["matched"] = bool(matched)
    if matched:
        evidence["结果"] = "满足"
    else:
        evidence["结果"] = "不满足"
    return evidence

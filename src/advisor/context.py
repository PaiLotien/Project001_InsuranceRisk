"""只格式化系统数值；假设分析由 Python 显式计算。"""

import math
import re
from numbers import Number


def build_alert_context(row, thresholds, attribution=None, source_label=""):
    def format_value(key, value):
        if value is None:
            return "数据不足"
        if isinstance(value, Number):
            if not math.isfinite(float(value)):
                return "数据不足"
            if "贡献" in key or "百分点" in key:
                return f"{value:+.1f} 个百分点"
            if any(s in key for s in ["率", "占比", "频度", "阈值"]):
                return f"{value:.1f}%"
            if any(s in key for s in ["保费", "赔款", "金额"]):
                return f"{value:,.0f} " + ("万元" if "万元" in key else "元")
            return str(value)
        return str(value)

    result = {k: format_value(k, v) for k, v in row.items()}
    result["数据来源"] = source_label
    for k, v in thresholds.items():
        result[{"yellow": "黄色", "orange": "橙色", "red": "红色"}.get(k, k) + "阈值"] = format_value("阈值", v)
    for k, v in (attribution or {}).items():
        result[k] = format_value(k, v)
    return result


def what_if_loss_ratio(cost_ratio, decrease_points, loss_ratio=None):
    cost, delta = float(cost_ratio), float(decrease_points)
    if not all(math.isfinite(v) for v in (cost, delta)) or delta < 0:
        raise ValueError("假设参数必须为有限非负下降百分点")
    out = {"假设条件": f"赔付率下降 {delta:g} 个百分点，费用率保持不变", "假设后成本率": f"{cost - delta:.1f}%"}
    # 同时给出假设后的赔付率，模型无需（也不允许）自己做减法
    if loss_ratio is not None:
        try:
            lr = float(str(loss_ratio).replace("%", "").strip())
        except ValueError:
            lr = math.nan
        if math.isfinite(lr):
            out["假设后预期赔付率"] = f"{lr - delta:.1f}%"
    return out


def hypothesis_context(question, context):
    match = re.search(r"赔付率下降\s*(\d+(?:\.\d+)?)\s*个百分点", question)
    if not match:
        return context
    result = dict(context or {})
    value = result.get("预期成本率", result.get("成本率"))
    if value is None or value == "数据不足":
        result["假设分析"] = "数据不足，需提供当前成本率"
        return result
    result.update(what_if_loss_ratio(str(value).replace("%", ""), match.group(1), result.get("预期赔付率")))
    return result

"""通用工具函数"""

import random
import re
from numbers import Real
from typing import Any, Optional


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """安全地将值转换为浮点数"""
    if value is None:
        return default
    if isinstance(value, Real):
        return float(value)
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", "").replace("，", "")
        if value in ("", "-", "--", "…", "……"):
            return default
        try:
            return float(value)
        except ValueError:
            return default
    return default


def safe_str(value: Any) -> str:
    """安全转换为字符串并清理"""
    if value is None:
        return ""
    text = str(value).strip().replace("　", " ").replace("\xa0", " ")
    return text


def normalize_metric_name(raw_name: str) -> str:
    """标准化指标名称 —— 去除多余空格、括号、单位后缀"""
    name = safe_str(raw_name)
    # 去除常见后缀
    for suffix in ["(万元)", "(元)", "(%)", "（万元）", "（元）", "（%）", "万元", "  "]:
        name = name.replace(suffix, "")
    return name.strip()


def normalize_vehicle_name(raw_name: str) -> str:
    """标准化车型名称"""
    name = safe_str(raw_name)
    # 去除序号前缀如 "01", "1", "1." 等
    name = re.sub(r"^\d+[.\s、]*", "", name)
    return name.strip()


def generate_demo_data(
    vehicle_types: list[str],
    base_cost_ratio: float = 85.0,
    anomaly_vehicles: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """按固定种子生成演示数据；base_cost_ratio 保留兼容，当前未使用。"""
    if anomaly_vehicles is None:
        anomaly_vehicles = ["家用车", "营业2吨以下", "出租车"]

    random.seed(42)  # 固定种子，保证每次生成数据一致
    data = []

    for vehicle in vehicle_types:
        is_anomaly = vehicle in anomaly_vehicles

        if is_anomaly:
            loss_ratio = round(random.uniform(65, 75), 1)
            cost_ratio = round(random.uniform(20, 28), 1)
        else:
            loss_ratio = round(random.uniform(50, 65), 1)
            cost_ratio = round(random.uniform(15, 22), 1)

        expected_cost = round(loss_ratio + cost_ratio, 1)
        premium = round(random.uniform(50, 500), 1)
        premium_pct = round(premium / 10, 1)  # 模拟占比
        avg_premium = round(random.uniform(1500, 4500), 0)
        claim_freq = round(random.uniform(5, 35), 1)
        auto_coef = round(random.uniform(0.72, 1.05), 2)

        data.append({
            "车型": vehicle,
            "保费占比": premium_pct,
            "签单保费": premium,
            "预期赔付率": loss_ratio,
            "成本率1": cost_ratio,
            "预期成本率": expected_cost,
            "自主定价系数": auto_coef,
            "单均保费": avg_premium,
            "累计出险频度": claim_freq,
            "is_demo": True,
        })

    total_pct = sum(row["保费占比"] for row in data)
    for row in data:
        row["保费占比"] = round(row["保费占比"] / total_pct * 100, 1)

    return data


def is_total_row(row) -> bool:
    """识别机构、车型或序号中的合计标记，忽略全部空白。"""
    values = [row.get(k, "") for k in ("车型", "机构", "序号")] if hasattr(row, "get") else [row]
    return any(re.search(r"(?:合计|小计|总计)$", re.sub(r"\s+", "", safe_str(v))) is not None for v in values)


def detail_frame(frame):
    return frame.loc[~frame.apply(is_total_row, axis=1)].copy() if not frame.empty else frame.copy()

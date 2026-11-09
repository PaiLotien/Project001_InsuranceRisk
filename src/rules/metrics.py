"""指标计算与查询工具

提供从解析结果中查询指标值、计算派生指标、识别异常值等功能。
"""

from pathlib import Path
from typing import Any, Optional

import yaml

from src.core.models import MetricCell


# 缓存加载的指标定义
_metric_defs_cache: Optional[dict[str, dict]] = None


def load_metric_definitions() -> dict[str, dict]:
    """加载 metric_definitions.yaml 配置，返回 {name: config} 映射"""
    global _metric_defs_cache
    if _metric_defs_cache is not None:
        return _metric_defs_cache

    config_path = Path(__file__).parent.parent.parent / "config" / "metric_definitions.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            metrics_list = data.get("metrics", [])
            _metric_defs_cache = {}
            for m in metrics_list:
                name = m.get("name", "")
                if name:
                    _metric_defs_cache[name] = m
                # 也按别名索引
                for alias in m.get("aliases", []):
                    _metric_defs_cache[alias] = m
    else:
        _metric_defs_cache = {}

    return _metric_defs_cache


def get_metric_value(
    metrics: list[MetricCell],
    vehicle_type: str,
    metric_name: str,
    time_dimension: str = "当日",
    branch: str = "全辖",
) -> Optional[float]:
    """返回首个匹配指标；branch 为全辖时不限制机构。"""
    for m in metrics:
        if (m.vehicle_type == vehicle_type
                and m.metric_name == metric_name
                and m.time_dimension == time_dimension
                and (m.branch == branch or branch == "全辖")):
            return m.value
    return None


def get_all_metric_values(
    metrics: list[MetricCell],
    metric_name: str,
    time_dimension: str = "当日",
) -> dict[str, Optional[float]]:
    """返回同一时间维度下的车型到指标值映射。"""
    result = {}
    for m in metrics:
        if m.metric_name == metric_name and m.time_dimension == time_dimension:
            result[m.vehicle_type] = m.value
    return result


def calculate_derived_metric(
    metrics: list[MetricCell],
    vehicle_type: str,
) -> dict[str, Any]:
    """表中没有预期成本率时，用预期赔付率加成本率1补算。"""
    derived = {}

    loss_ratio = get_metric_value(metrics, vehicle_type, "预期赔付率")
    cost_ratio = get_metric_value(metrics, vehicle_type, "成本率1")
    expected_cost = get_metric_value(metrics, vehicle_type, "预期成本率")

    if expected_cost is None and loss_ratio is not None and cost_ratio is not None:
        derived["预期成本率"] = round(loss_ratio + cost_ratio, 2)
        derived["_calculated"] = True

    return derived


def detect_missing_values(
    metrics: list[MetricCell],
    vehicle_types: list[str],
    expected_metrics: list[str],
) -> dict[str, list[str]]:
    """按车型返回缺失的指标名，查询口径为当日。"""
    missing = {}
    for vehicle in vehicle_types:
        missing_metrics = []
        for metric_name in expected_metrics:
            value = get_metric_value(metrics, vehicle, metric_name)
            if value is None:
                missing_metrics.append(metric_name)
        if missing_metrics:
            missing[vehicle] = missing_metrics
    return missing

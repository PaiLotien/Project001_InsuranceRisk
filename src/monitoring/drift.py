"""比较当前数据与历史快照的分布；漂移结果不参与预警等级计算。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
RESULT_COLUMNS = [
    "字段", "类型", "基准样本", "当前样本", "PSI", "基准缺失率", "当前缺失率",
    "缺失率变化", "均值变化", "状态", "说明",
]
DEFAULT_NUMERIC_COLUMNS = ("预期成本率", "保费占比", "签单保费_万元")
DEFAULT_CATEGORY_COLUMNS = ("机构", "车型", "预警等级")
EPSILON = 1e-6


def load_drift_policy() -> dict[str, float]:
    """从 YAML 读取阈值，避免页面再次硬编码漂移口径。"""

    path = ROOT / "config" / "drift_rules.yaml"
    defaults = {
        "minimum_rows": 20.0,
        "psi_stable_lt": 0.10,
        "psi_watch_lt": 0.25,
        "missing_stable_delta_lt": 0.05,
        "missing_watch_delta_lt": 0.20,
    }
    if not path.exists():
        return defaults
    with path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    drift = config.get("drift", {})
    psi = drift.get("psi", {})
    missing = drift.get("missing_rate", {})
    return {
        "minimum_rows": float(drift.get("minimum_rows", defaults["minimum_rows"])),
        "psi_stable_lt": float(psi.get("stable_lt", defaults["psi_stable_lt"])),
        "psi_watch_lt": float(psi.get("watch_lt", defaults["psi_watch_lt"])),
        "missing_stable_delta_lt": float(missing.get("stable_delta_lt", defaults["missing_stable_delta_lt"])),
        "missing_watch_delta_lt": float(missing.get("watch_delta_lt", defaults["missing_watch_delta_lt"])),
    }


def _histogram_distribution(values: pd.Series, edges: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(values.to_numpy(dtype=float), bins=edges)
    return (counts + EPSILON) / (counts.sum() + EPSILON * len(counts))


def calculate_numeric_psi(reference: pd.Series, current: pd.Series, bins: int = 10) -> float | None:
    """计算数值字段 PSI；样本为空或无法转数值时返回 None。"""

    reference = pd.to_numeric(reference, errors="coerce").dropna()
    current = pd.to_numeric(current, errors="coerce").dropna()
    if reference.empty or current.empty:
        return None

    if reference.nunique() <= 1:
        center = float(reference.iloc[0])
        spread = max(abs(center) * 0.01, 1.0)
        edges = np.array([-np.inf, center - spread, center + spread, np.inf])
    else:
        quantiles = np.linspace(0, 1, max(2, bins) + 1)
        edges = np.unique(np.quantile(reference.to_numpy(dtype=float), quantiles))
        if len(edges) < 2:
            return 0.0
        edges[0] = -np.inf
        edges[-1] = np.inf

    expected = _histogram_distribution(reference, edges)
    actual = _histogram_distribution(current, edges)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def calculate_category_psi(reference: pd.Series, current: pd.Series) -> float | None:
    """计算类别字段 PSI，显式把空值作为一个类别。"""

    if reference.empty or current.empty:
        return None
    reference = reference.astype("string").fillna("<MISSING>")
    current = current.astype("string").fillna("<MISSING>")
    categories = sorted(set(reference.unique()).union(current.unique()))
    expected = reference.value_counts(normalize=True).reindex(categories, fill_value=0).to_numpy(dtype=float)
    actual = current.value_counts(normalize=True).reindex(categories, fill_value=0).to_numpy(dtype=float)
    expected = (expected + EPSILON) / (expected.sum() + EPSILON * len(categories))
    actual = (actual + EPSILON) / (actual.sum() + EPSILON * len(categories))
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def _missing_rate(series: pd.Series, numeric: bool) -> float:
    if numeric:
        return float(pd.to_numeric(series, errors="coerce").isna().mean())
    return float(series.isna().mean())


def _status(psi: float | None, missing_delta: float, rows_ok: bool, policy: dict[str, float]) -> str:
    if not rows_ok:
        return "样本不足"
    psi_value = psi or 0.0
    missing_value = abs(missing_delta)
    if psi_value >= policy["psi_watch_lt"] or missing_value >= policy["missing_watch_delta_lt"]:
        return "漂移"
    if psi_value >= policy["psi_stable_lt"] or missing_value >= policy["missing_stable_delta_lt"]:
        return "关注"
    return "稳定"


def compare_drift(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    columns: list[str] | None = None,
    policy: dict[str, float] | None = None,
) -> pd.DataFrame:
    """比较基准数据和当前数据，返回可直接展示的字段级结果。"""

    if reference.empty or current.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    policy = policy or load_drift_policy()
    if not columns:
        columns = []
        for column in (*DEFAULT_NUMERIC_COLUMNS, *DEFAULT_CATEGORY_COLUMNS):
            if column in reference.columns or column in current.columns:
                columns.append(column)
    results: list[dict[str, Any]] = []
    for column in columns:
        if column not in reference.columns or column not in current.columns:
            continue
        reference_series = reference[column]
        current_series = current[column]
        numeric = column in DEFAULT_NUMERIC_COLUMNS
        mean_change = None
        if numeric:
            reference_numeric = pd.to_numeric(reference_series, errors="coerce")
            current_numeric = pd.to_numeric(current_series, errors="coerce")
            psi = calculate_numeric_psi(reference_numeric, current_numeric)
            reference_mean = reference_numeric.mean()
            current_mean = current_numeric.mean()
            if pd.notna(reference_mean) and pd.notna(current_mean):
                mean_change = float(current_mean - reference_mean)
        else:
            psi = calculate_category_psi(reference_series, current_series)
        reference_missing = _missing_rate(reference_series, numeric)
        current_missing = _missing_rate(current_series, numeric)
        missing_delta = current_missing - reference_missing
        rows_ok = len(reference_series) >= policy["minimum_rows"] and len(current_series) >= policy["minimum_rows"]
        status = _status(psi, missing_delta, rows_ok, policy)
        if status == "漂移":
            explanation = "分布变化达到阈值"
        elif status == "关注":
            explanation = "分布有变化，建议核查"
        elif status == "样本不足":
            explanation = "样本量低于最低要求"
        else:
            explanation = "未发现明显变化"
        results.append({
            "字段": column,
            "类型": "数值" if numeric else "类别",
            "基准样本": len(reference_series),
            "当前样本": len(current_series),
            "PSI": round(psi, 4) if psi is not None else None,
            "基准缺失率": round(reference_missing, 4),
            "当前缺失率": round(current_missing, 4),
            "缺失率变化": round(missing_delta, 4),
            "均值变化": round(mean_change, 4) if mean_change is not None else None,
            "状态": status,
            "说明": explanation,
        })
    return pd.DataFrame(results, columns=RESULT_COLUMNS)

"""独立统计异常层：分数和持续偏离信号不修改业务预警等级。"""

from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import IsolationForest


def score_anomalies(current, metrics, *, reference=None, dimensions=("机构", "车型"), random_state=42):
    """每个机构×车型必须唯一；0—100 为森林异常强度。

    top_metric 为单指标替换为训练中位数后异常分数下降最大的指标；
    这是模型敏感性解释，并非因果贡献。缺失值用训练中位数补齐。
    """
    if not metrics:
        raise ValueError("至少提供一个指标")
    training = current if reference is None else reference
    for frame in (training, current):
        if frame.duplicated(list(dimensions)).any():
            raise ValueError("机构×车型必须唯一，请先按指标口径聚合")
    output = current[list(dimensions)].copy()
    if current.empty:
        return output.assign(anomaly_score=pd.Series(dtype=float), top_metric=pd.Series(dtype=str))
    x = training[metrics].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    y = current[metrics].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if len(x) < 2 or x.notna().sum().eq(0).any() or y.notna().sum(axis=1).eq(0).any():
        raise ValueError("训练样本不足、指标全缺失或当前行全缺失")
    median = x.median()
    x, y = x.fillna(median), y.fillna(median)
    model = IsolationForest(n_estimators=200, random_state=random_state, contamination="auto")
    model.fit(x)
    output["anomaly_score"] = np.clip(-model.score_samples(y) * 100, 0, 100)
    reductions = pd.DataFrame(index=y.index)
    for metric in metrics:
        counterfactual = y.copy()
        counterfactual[metric] = median[metric]
        reductions[metric] = output["anomaly_score"] + model.score_samples(counterfactual) * 100
    output["top_metric"] = reductions.idxmax(axis=1)
    output["top_metric_contribution"] = reductions.max(axis=1)
    output["metric_explanation"] = "单指标替换为训练中位数后的异常分数下降（非因果贡献）"
    return output


def load_temporal_policy():
    path = Path(__file__).resolve().parents[2] / "config" / "drift_rules.yaml"
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)["statistical_anomaly"]["temporal"]


def detect_deterioration(data, metric="预期成本率", *, date_column="日期",
                         dimensions=("机构", "车型"), policy=None, direction=1):
    """按过去窗口计算单侧 z-score；缺日、缺值中断连续天数，无未来泄漏。"""
    policy = load_temporal_policy() if policy is None else policy
    window = int(policy["window"])
    minimum = int(policy["minimum_periods"])
    consecutive = int(policy["consecutive_days"])
    threshold = float(policy["z_threshold"])
    floor = float(policy["scale_floor"])
    if not 2 <= minimum <= window or consecutive < 1 or floor <= 0 or threshold <= 0 or direction not in (-1, 1):
        raise ValueError("无效的时序检测配置")
    result = data.copy().reset_index(drop=True)
    result[date_column] = pd.to_datetime(result[date_column], errors="raise").dt.normalize()
    if result[date_column].isna().any() or result.duplicated([*dimensions, date_column]).any():
        raise ValueError("每个分组每日必须有且仅有一条观测，日期不能为空")
    result = result.sort_values([*dimensions, date_column])
    result["z_score"] = np.nan
    result["deterioration_days"] = 0
    result["sudden_change"] = False
    result["persistent_deterioration"] = False
    for _, group in result.groupby(list(dimensions), dropna=False):
        series = pd.to_numeric(group[metric], errors="coerce").replace([np.inf, -np.inf], np.nan)
        # 补齐日历后滚动，窗口以天为单位，缺日不视作连续观测。
        daily = pd.Series(series.to_numpy(), index=group[date_column]).asfreq("D")
        past = daily.shift(1).rolling(window, min_periods=minimum)
        z = (direction * (daily - past.mean()) / past.std(ddof=0).clip(lower=floor))
        streak = 0
        for day, value in z.items():
            flagged = bool(pd.notna(value) and value >= threshold)
            streak = streak + 1 if flagged else 0
            indices = group.index[group[date_column] == day]
            result.loc[indices, "z_score"] = value
            result.loc[indices, "deterioration_days"] = streak
            result.loc[indices, "sudden_change"] = flagged
            result.loc[indices, "persistent_deterioration"] = streak >= consecutive
    return result

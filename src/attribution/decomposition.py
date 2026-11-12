"""可复核的对数、结构及可加量归因；不把相关性解释为因果。"""

import itertools
import math

import numpy as np
import pandas as pd


def log_decomposition(reference, current, *, metric="预期赔付率"):
    """输入同口径频度(%)、案均(元)、单均保费(元)；返回自然对数贡献。

    观测赔付率与三要素不相容时返回 fallback，绝不通过残差伪造贡献。
    """
    keys = ("累计出险频度", "案均赔款", "单均保费_元")
    warning = []
    if metric == "预期成本率":
        return {"fallback": True, "warning": ["预期成本率包含费用率，不能用赔付三要素恒等式分解"]}
    try:
        a = [float(reference[k]) for k in keys]
        b = [float(current[k]) for k in keys]
        if any(not math.isfinite(x) or x <= 0 for x in a + b):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"fallback": True, "warning": ["缺少正且有限的三要素或历史基准"]}
    for key in ("频度分母", "保费分母", "赔款口径"):
        if reference.get(key) != current.get(key):
            warning.append(f"{key}跨期不一致")
    for row in (reference, current):
        if row.get("频度分母", "承保件数") != row.get("保费分母", "承保件数"):
            warning.append("频度与单均保费分母口径不一致")
    contributions = dict(zip(("出险频度", "案均赔款", "单均保费"),
                             (math.log(b[0] / a[0]), math.log(b[1] / a[1]), -math.log(b[2] / a[2]))))
    total = math.fsum(contributions.values())
    observed = []
    for row, factors in ((reference, a), (current, b)):
        try:
            value = float(row[metric])
            if not math.isfinite(value) or value <= 0:
                raise ValueError
            observed.append(value)
            if not math.isclose(value, factors[0] * factors[1] / factors[2], rel_tol=1e-12, abs_tol=1e-12):
                warning.append("观测赔付率与三要素不一致：预期赔款/已决赔款或案件/暴露口径可能不同")
        except (KeyError, TypeError, ValueError):
            warning.append("缺少可校验的观测赔付率")
    if warning:
        return {"fallback": True, "warning": list(dict.fromkeys(warning)),
                "factor_contributions": contributions, "factor_total_change": total}
    return {"fallback": False, "warning": [], "contributions": contributions,
            "total_change": math.log(observed[1] / observed[0]), "metric": metric}


def mix_rate_decomposition(reference, current, *, dimensions=("机构", "车型"),
                           numerator="预期赔款", denominator="签单保费"):
    """联合分组对称分解；mix=Δw·平均r，rate=平均w·Δr。结果为比率值。"""
    def aggregate(frame):
        values = frame[[*dimensions, numerator, denominator]].copy()
        values[[numerator, denominator]] = values[[numerator, denominator]].apply(pd.to_numeric, errors="raise")
        if not np.isfinite(values[[numerator, denominator]].to_numpy()).all() or (values[[numerator, denominator]] < 0).any().any():
            raise ValueError("分子分母必须非负且有限")
        grouped = values.groupby(list(dimensions), dropna=False)[[numerator, denominator]].sum()
        if grouped[denominator].sum() <= 0 or ((grouped[denominator] == 0) & (grouped[numerator] != 0)).any():
            raise ValueError("分母为零，无法计算比率")
        return grouped
    a, b = aggregate(reference), aggregate(current)
    index = a.index.union(b.index)
    a, b = a.reindex(index, fill_value=0), b.reindex(index, fill_value=0)
    w0, w1 = a[denominator] / a[denominator].sum(), b[denominator] / b[denominator].sum()
    r0 = a[numerator].div(a[denominator].replace(0, np.nan))
    r1 = b[numerator].div(b[denominator].replace(0, np.nan))
    # 新增/退出分组沿用另一期 rate，全部变化归入结构效应。
    r0, r1 = r0.fillna(r1).fillna(0), r1.fillna(r0).fillna(0)
    detail = pd.DataFrame({"mix": (w1 - w0) * (r0 + r1) / 2,
                           "rate": (r1 - r0) * (w0 + w1) / 2}).reset_index()
    return {"total_change": float(b[numerator].sum() / b[denominator].sum() - a[numerator].sum() / a[denominator].sum()),
            "mix": float(detail["mix"].sum()), "rate": float(detail["rate"].sum()),
            "details": detail, "warning": []}


def additive_drilldown(reference, current, metric, *, dimensions=("机构", "车型"), top_n=10):
    """简化 Adtributor：枚举单维与交叉切片，按解释力×JS surprise 排序。

    切片可重叠，贡献不能跨切片求和；解释力以全部叶子绝对变化为分母。
    """
    if top_n < 1:
        raise ValueError("top_n 必须为正整数")
    for frame in (reference, current):
        values = pd.to_numeric(frame[metric], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("可加量必须非负且有限")
    def grouped(frame, dims):
        values = frame[[*dims, metric]].copy()
        values[metric] = pd.to_numeric(values[metric], errors="raise")
        return values.groupby(list(dims), dropna=False)[metric].sum()
    a, b = grouped(reference, dimensions).align(grouped(current, dimensions), fill_value=0)
    scale = float((b - a).abs().sum())
    total0, total1 = float(a.sum()), float(b.sum())
    results = []
    for size in range(1, len(dimensions) + 1):
        for dims in itertools.combinations(dimensions, size):
            x, y = grouped(reference, dims).align(grouped(current, dims), fill_value=0)
            for key in x.index:
                p, q = (float(x[key]) / total0 if total0 else 0), (float(y[key]) / total1 if total1 else 0)
                # Bernoulli JS 包含切片及补集，零概率按连续极限处理。
                surprise = 0.0
                for u, v in ((p, q), (1 - p, 1 - q)):
                    m = (u + v) / 2
                    if u > 0:
                        surprise += u * math.log(u / m) / 2
                    if v > 0:
                        surprise += v * math.log(v / m) / 2
                delta = float(y[key] - x[key])
                power = abs(delta) / scale if scale else 0.0
                results.append({"dimensions": dict(zip(dims, key if isinstance(key, tuple) else (key,))),
                                "change": delta, "explanatory_power": power,
                                "surprise": surprise, "score": power * surprise})
    return sorted(results, key=lambda r: r["score"], reverse=True)[:top_n]

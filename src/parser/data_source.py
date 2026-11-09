"""明细唯一性检查；冲突记录必须由用户选择来源，不任意覆盖。"""

import pandas as pd


def unique_records(df: pd.DataFrame, keys=None) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    if keys is None:
        keys = [c for c in ("统计日期", "日期", "机构", "车型", "能源类型", "期间") if c in df.columns]
    if not keys:
        return df.drop_duplicates().copy()
    out = df.copy()
    for column in keys:
        if column not in ("日期", "统计日期"):
            out[column] = out[column].astype("string").str.strip()
    value_cols = [c for c in out.columns if c not in ("来源Sheet", "来源行", "来源", "数据区块", "表类型")]
    out = out.drop_duplicates(subset=value_cols)
    conflicts = out.duplicated(subset=keys, keep=False)
    if conflicts.any():
        names = out.loc[conflicts, keys].head(3).astype(str).agg(" / ".join, axis=1).tolist()
        raise ValueError("同一主键存在冲突记录，请选择主表或修正数据：" + "；".join(names))
    return out.reset_index(drop=True)

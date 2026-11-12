"""独立万行基准，不进入 pytest。"""

import sys
import time
import tempfile
import platform
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.validation.schema import validate_frame
from src.parser.excel_loader import read_excel, excel_engine


def main():
    df = pd.DataFrame(
        {
            "机构": ["机构A"] * 10000,
            "车型": ["车型"] * 9999 + ["合计"],
            "预期赔付率": [70.0] * 10000,
            "成本率1": [20.0] * 10000,
            "预期成本率": [90.0] * 10000,
            "签单保费": [100.0] * 9999 + [999900.0],
            "赔款": [70.0] * 9999 + [699930.0],
            "赔付率": [70.0] * 10000,
            "保费占比": [100 / 9999] * 9999 + [100.0],
        }
    )
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "bench.xlsx"
        df.to_excel(path, index=False)
        start = time.perf_counter()
        loaded = read_excel(path)
        read_s = time.perf_counter() - start
        start = time.perf_counter()
        issues = validate_frame(loaded, "性能表")
        validate_s = time.perf_counter() - start
    text = f"Python {platform.python_version()}；pandas {pd.__version__}；{excel_engine()}；10000 行读取 {read_s:.3f}s；完整校验 {validate_s:.3f}s；问题 {len(issues)} 条。"
    print(text)
    (Path(__file__).resolve().parents[1] / "docs/validation_benchmark.md").write_text(
        "# 校验性能实测\n\n2026-11-11，开发容器单次冷启动（含 Pandera 初始化），非 CI 性能门槛。\n\n"
        + text
        + "\n\n复现：`.venv/bin/python scripts/bench_validation.py`。\n"
    )


if __name__ == "__main__":
    main()

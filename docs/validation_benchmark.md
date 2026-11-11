# 校验性能实测

2026-11-11，开发容器单次冷启动（含 Pandera 初始化），非 CI 性能门槛。

Python 3.13.5；pandas 3.1.1；calamine；10000 行读取 0.065s；完整校验 0.050s；问题 0 条。

复现：`.venv/bin/python scripts/bench_validation.py`。

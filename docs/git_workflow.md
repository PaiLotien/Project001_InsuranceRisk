# IRDS 版本控制约定

本项目从本阶段开始使用 Git 保存可回滚版本。当前仓库只保留代码、配置、测试和小型合成演示数据；真实业务表、个人配置、虚拟环境和生成的压缩包不会进入版本库。

## 日常流程

```bash
git status
git add src config tests
git commit -m "feat: describe the change"
```

提交前建议执行：

```bash
python -m pytest
ruff check .
python -m py_compile src/dashboard/streamlit_app.py
```

## 分支建议

- `main`：可演示、可交付的稳定版本。
- `codex/<topic>`：功能开发或问题修复分支。
- 一个功能完成并通过测试后，再合并回 `main`。

## 当前边界

本地仓库已经初始化，但尚未绑定远程 GitHub 地址，也不会自动上传保险业务数据。需要远程协作时，再由项目负责人创建私有仓库并配置远程地址。

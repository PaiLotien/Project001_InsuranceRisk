# IRDS 团队演示包

Python 3.10+；本次验证环境为Python 3.14。

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest
python -m streamlit run src/dashboard/streamlit_app.py
```

浏览器打开 http://localhost:8501 。默认Demo数据。
本包版本为2026-11-09测试反馈修复版，属于当前工作区快照，包含浅色中文界面及AI建议原型。
主要业务模块已调整为显式循环、分步计算和直接条件判断，公开接口及规则口径保留。
AI建议原型需另外安装并启动Ollama，在本机执行 ollama pull qwen3.5:4b。
未配置本地模型时，其他页面仍可使用；点击模型咨询会提示调用失败。
通知配置已脱敏，默认不含真实SMTP凭据。不要向演示联系人发真实邮件。
单条邮件在“预警通知中心”直接填写收件人和抄送邮箱；多个地址用逗号、分号或换行分隔。
未配置SMTP时仅模拟发送，不会实际投递。真实发送需在本地.env填写发件服务配置，勿在群里分享授权码。
批量通知需先预览收件人并确认，支持暂停、继续和停止；已经在投递中的邮件不能撤回。
上传Excel时只选一个主数据Sheet；其他表块在数据上传页单独查看，不盲目合并。
历史支持批量导入、CSV导出和确认后删除；演示趋势只在内存生成，不写入真实历史。
详细安装、Windows命令和体验步骤见 docs/team_quickstart.md。
docs/delivery_20260920.md说明真实功能边界和本轮验证结果。
该文件是9月历史记录，最新说明以docs/release_20261109.md为准。
docs/business_inputs为调研输入，不代表所有规则均已确认或实现。

## upgrade/v2 第 2 批：本地 AI 风控顾问

新增校验、通知 dry-run、处置闭环与组合规则的配置、入口及测试说明见 [第 2 批升级说明](docs/upgrade_v2_batch2.md)。

本地顾问配置为 `config/ai_advisor.yaml`，运行时只访问本机 Ollama。先在本机准备对话模型 `qwen3.5:4b`；向量模型文件由管理员下载 Hugging Face 的 `gpustack/bge-m3-GGUF/bge-m3-Q8_0.gguf` 到 `models/`，再执行：

```bash
ollama create bge-m3-local -f models/Modelfile.bge-m3
.venv/bin/python scripts/build_advisor_index.py
# 兼容名称：scripts/build_knowledge_index.py
.venv/bin/python scripts/run_advisor_eval.py --n 3
```

索引构建时自动同步 `docs/`、`config/` 到知识目录；自维护内容放 `knowledge/` 其他目录，顾问行为约束放 `skills/`。Skill 修改下次提问生效，知识内容修改后重建索引。索引与 GGUF 不提交 git。不需要 torch 或 sentence-transformers；docx 解析可选安装 python-docx，PDF 文本解析需要本机 poppler-utils。Ollama/模型/索引不可用时界面保留规则结果并显示降级提示。

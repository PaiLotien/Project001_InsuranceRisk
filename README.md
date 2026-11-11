# 车险智能风控平台

**IRDS · Insurance Risk Dynamic Surveillance** —— 车险经营监测、规则预警、定量归因、数据质量、处置闭环与本地 AI 风控顾问一体化平台。本地部署，业务数据不出本机。

## 启动

Python 3.10+（已在 Python 3.13 / 3.14 验证）。

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest
python -m streamlit run src/dashboard/streamlit_app.py
```

浏览器打开 http://localhost:8501 。

## 数据来源与口径

| 数据源 | 文件 | 说明 |
|---|---|---|
| 车险经营监测数据集（SUSEP 2011，默认） | `data/official/brvehins_2011.xlsx` | 巴西保险监管局 SUSEP AUTOSEG 公开统计，经 CASdatasets `brvehins1` 汇总为州 × 车型；27 个州及联邦区保持原地名；金额单位雷亚尔；GPL-2+ |
| 中国行业监测视图 | `data/official/china/*.csv` | 国家统计局、国家金融监督管理总局、中国精算师协会公开统计；省级为财产险全险种口径，不含计划单列市 |
| 上传机构数据 | 侧栏上传 Excel / CSV | 自动识别 9 类监控报表，只读取所选主数据 Sheet |

字段来源、许可与缺口详见 [docs/data_sources.md](docs/data_sources.md)。

## 功能

- **监测总览**：全辖预期赔付率 / 成本率、红橙黄预警、待处置、数据质量问题 KPI；风险分布、地区排行或省级地图；一键导出 Excel 报告与打印版。
- **预警中心**：配置驱动规则引擎（三级阈值黄 / 橙 / 红可在侧栏调整并本机持久化，合计行不参与判级），触发规则可追溯；统计异常分单独展示，不改变预警等级；批量通知需预览并确认收件人。
- **归因分析**：赔付率三要素对数分解（三项之和等于总变化）、结构 / 自身效应、Adtributor 异常切片，以及单条预警的规则归因与整改建议。
- **数据质量**：智能校验定位到行与字段、TOP3 问题、DQ001 / DQ002；报表接入与结构识别。
- **处置跟踪**：新建 → 已确认 → 处置中 → 已关闭，责任人、时限、超时升级留痕。
- **预警通知**：收件人匹配、邮件预览、发送记录；多渠道推送演练。未配置 SMTP 时只做模拟记录，真实发送需在本地 `.env` 配置发件服务（勿共享授权码）。
- **行业对标**：省级财产险赔付率地图、全国车险保费与单均保费走势、精协基准纯风险保费查询。
- **AI 风控顾问**：本机 Ollama 模型 + 知识库检索，引用与数字溯源校验。
- **系统设置**：版本、阈值、数据源登记、指标口径。

界面结构与本批改动见 [docs/upgrade_v2_batch3.md](docs/upgrade_v2_batch3.md)。

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

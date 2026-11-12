# upgrade/v2 第 2 批升级说明

## 交付与验证

本批在 `upgrade/v2` 基于 b8635af 实施。主要交付后端功能，仅增加必要的 Streamlit 入口。未推送、未修改 remote、未发送真实邮件或消息。

- `.venv/bin/python -m pytest -q`：177 项全部通过，原有 114 项之外新增 63 项。旧 AI 测试按集成文档改为 mock 新统一调用入口，保留规则边界回归断言。
- `.venv/bin/ruff check src tests scripts`：通过。
- 依赖 pandera、python-calamine、apprise 已加入 requirements.txt 并安装到 `.venv`。pandas 最低版本升至 2.2，以支持 calamine。
- 独立性能测试不进入 pytest：见 [万行性能实测](validation_benchmark.md)，当前环境 10000 行读取 0.065 秒，完整校验 0.050 秒，0 条问题；不是跨机器性能承诺。

## Excel 智能校验

`src/validation/schema.py` 读取 YAML，生成 Pandera 字段 Schema，并执行配置声明的公式、分组总计检查。共 23 条声明式规则，另复用 DQ002 跨表对账，DQ003 保留默认关闭、显式启用时仅提示。

| 编号 | 校验内容 |
|---|---|
| DQ101–105 | 机构、车型、预期赔付率、成本率1、预期成本率必填 |
| DQ111–116 | 六个金额/比率字段须可转为有限数值 |
| DQ121–124 | 保费、赔款非负；保费占比及费用率在 0–100 内 |
| DQ131–132 | 机构与车型为文本 |
| DQ001 | 主表车型保费占比加总约为 100%，沿用 ±1 个百分点容差 |
| DQ141 | 预期成本率≈预期赔付率+成本率1，容差 0.1 个百分点 |
| DQ142 | 赔付率≈赔款/签单保费×100，容差 0.1 个百分点；零分母报问题 |
| DQ151 | 每个机构同口径分组须有且只有一条“合计”行 |
| DQ152–153 | 签单保费、赔款的合计=明细之和，金额容差 0.01（沿用输入单位） |
| DQ002 | T2/T7 对应机构当月保费一致，复用 Decimal 对账及重复值保护 |
| DQ003 | 当日值×当月天数≈当月总值，默认关闭，启用后仍仅为 warning |

DQ001–003 来自 `docs/business_inputs/数据质量校验规则表.xlsx`，保留业务来源和原表“中止仅供参考”的说明；新增规则标注为工程完整性检查，不冒充已确认业务阈值。新增字段/范围、公式容差可以在 YAML 中调整。比率统一使用百分数数值，例如 70 表示 70%。公式仅在输入列齐全时执行，缺列由必填规则报告。

所有问题输出 `sheet / row / column / rule_id / severity / message`。原始区块保留 `来源行`，标准表从 Excel 第 2 行计数；缺失整列或跨表缺机构时行号为空，避免伪造定位。先按来源 Sheet、机构、期间、区块、表类型分组，不混加子表和不同期间。`top3()` 汇总出现最多的三类问题。上传数据 attrs 保存智能校验明细与 TOP3，页面提供查看入口；跨表页显示统一定位。

平面 Excel 读取优先 calamine，不可导入时回退 openpyxl。保留 openpyxl 的工作簿结构解析，以读取合并单元格、表头和原始行位置。损坏文件的 calamine 异常统一转成可读上传错误。

## 推送与处置

`src/notification/channels.py` 通过 Apprise 封装企业微信机器人（`wecombot://`）、钉钉、飞书和 SMTP。`notification_rules.yaml` 的 `delivery` 指定等级路由、分组键、抑制条件和 `repeat_interval`（秒，默认 14400）。地址均为占位配置。

同机构同批次合成一条通知；相同对象的红色抑制黄/橙，不抑制其他车型或其他批次。合并后按最高等级路由。重复状态持久化到 SQLite，重启后仍生效；重复间隔的等号边界允许重发。指纹包含内容、路由和 dry-run 模式，演练不会阻止将来的真实模式。发送失败不写入成功状态。事务串行化并发去重。

默认 `dry_run: true`，只写 `data/runtime/outbox.jsonl`；页面“生成多渠道 dry-run outbox”强制演练模式。测试对 Apprise 全部 mock，覆盖四种渠道、抑制、分组、重复与失败重试。本批没有调用真实发送。

`src/alerts/lifecycle.py` 在 `data/runtime/irds.db` 持久化“新建→已确认→处置中→已关闭”，禁止跳跃和回退。保存关联预警引用、责任人、UTC 时限、超时标记及带时刻的处置记录；`escalate_overdue()` 对超过时限的未关闭单标记一次升级并留痕。时刻恰好等于 deadline 不升级。通知中心提供新建、更新和检查超时入口；后台定时任务可调用同一函数，本批不额外部署调度服务。整个运行目录已忽略。

## 组合规则与阈值边界

`src/rules/expressions.py` 用 AST 白名单解析，只允许规则名称、布尔常量、AND/OR/NOT 与括号，不使用 eval；调用、属性、下标、运算、比较均拒绝。

`alert_rules.yaml` 的 C001 为 `R008 AND R007`，两个原始规则在同机构、车型、期间、维度、区块都命中时，将该对象命中记录各升一级，最高红色。只对原始命中计算组合，多个组合同时命中不会连升多级。R008 仍须满足当月满期保费和进度前置条件。

`threshold_matrix.yaml` 按机构×车型×规则精确匹配；没有配置则回退全局阈值，默认空表。完整指标规则引擎与上传数据成本率判级均复用阈值矩阵。

比较定义集中在 `COMPARATORS / SYMBOLS`：gt/change_gt 为 `>`，gte 为 `≥`，lt/change_lt 为 `<`，lte 为 `≤`，eq 为等于。默认 R005 的 90 恰好不触发，R010 的 100 恰好不触发红色，100 仍为黄色；存在独立橙色阈值时也使用严格 `>`。原有边界语义保持不变。

## 本地 AI 顾问

按 `/workspace/irds_ai_advisor/INTEGRATION.md` 与 DESIGN.md 将原型复制到仓库，运行时代码不引用仓库外路径。`src/advisor/rag.py` 保留 Markdown/文本/YAML/xlsx/docx/pdf 加载、标题切块、本地向量+BM25/RRF 检索、Skill 加载和 Ollama 流式调用。xlsx 改为每行一块，保证规则完整。

`context.py` 生成中文、带单位上下文，NaN/无穷等缺失值显示“数据不足”。假设分析 `what_if_loss_ratio()` 由 Python 计算“成本率−赔付率下降百分点”，明确费用率保持不变；匹配中文问题后将计算结果作为系统上下文交给模型。数据不足时不臆造基准值。

`service.py` 提供固定接口 `get_index / ask_advisor / stream_advisor / rebuild_index / advisor_health`。索引用 `st.cache_resource` 缓存，重建后清除。支持 JSON Schema 结构化输出、字段类型检查、引用有效性与数字溯源检查；流式生成器耗尽后补全元数据。保留最近两轮历史。数字/引用异常在页面提示人工核对，不改变规则结果。

构建脚本按块文本+模型哈希复用向量；知识库的“项目文档”“配置规则”构建时从仓库 docs/config 同步，避免重复维护。知识文件增删改、模型变化、仓库源文件变化会触发过期提示。知识、Skill、24 题评测集与示例上下文已复制；索引、GGUF、评测输出不提交。

配置及环境变量覆盖按集成表实现，旧 `IRDS_OLLAMA_URL` 去掉 `/api/chat` 后缀兼容。HTTP 显式禁用代理，默认 socket 离线守卫拒绝非回环连接，Ollama 地址预检查仅允许本机；开启后对当前 Python 进程生效。模型不可用、索引缺失或 JSON 无效均返回可展示降级信息，规则结果不受影响。

`src/llm_advisor.py` 保留 `analyze()` 和 `chat()`，旧四字段及 model/timeout 参数兼容，内部统一走新服务。页面改名“AI 风控顾问”，提供健康状态、系统依据、索引重建、四类解读、流式对话、引用片段、数字检查、计时、Skill/知识清单。启动步骤见 README。

## 实测范围与未完成项

本批代码功能无未完成项。按任务约束，真实机器人/SMTP 投递、真实 Ollama 推理、向量模型构建与 24 题实机质量评测未执行；这些外部运行条件不纳入离线 pytest 成功结论。需本机预先准备模型后运行构建和评测脚本。UI 正式美化留给下一批。

## 改动文件清单

- `.gitignore`
- `README.md`
- `config/ai_advisor.yaml`
- `config/alert_rules.yaml`
- `config/data_quality_rules.yaml`
- `config/notification_rules.yaml`
- `config/threshold_matrix.yaml`
- `docs/upgrade_v2_batch2.md`
- `docs/validation_benchmark.md`
- `eval/advisor_eval_set.jsonl`
- `examples/context_red.json`
- `knowledge/业务输入/业务规则调研问卷.xlsx`
- `knowledge/业务输入/数据质量校验规则表.xlsx`
- `knowledge/业务输入/车型风险维度映射表.xlsx`
- `knowledge/业务输入/预警规则配置表.xlsx`
- `knowledge/指标口径/大创资料摘要.md`
- `knowledge/指标口径/数据与指标口径方案.md`
- `models/Modelfile.bge-m3`
- `requirements.txt`
- `scripts/bench_validation.py`
- `scripts/build_advisor_index.py`
- `scripts/build_knowledge_index.py`
- `scripts/run_advisor_eval.py`
- `skills/irds_advisor.md`
- `src/advisor/__init__.py`
- `src/advisor/context.py`
- `src/advisor/rag.py`
- `src/advisor/service.py`
- `src/advisor/settings.py`
- `src/alerts/lifecycle.py`
- `src/alerts/thresholds.py`
- `src/core/models.py`
- `src/dashboard/streamlit_app.py`
- `src/llm_advisor.py`
- `src/notification/channels.py`
- `src/parser/excel_loader.py`
- `src/parser/upload_data.py`
- `src/rules/expressions.py`
- `src/rules/rule_engine.py`
- `src/validation/cross_report.py`
- `src/validation/schema.py`
- `tests/test_advisor_rag.py`
- `tests/test_attribution_fixes.py`
- `tests/test_batch2_notifications.py`
- `tests/test_batch2_rules.py`
- `tests/test_batch2_validation.py`
- `tests/test_rule_boundaries.py`

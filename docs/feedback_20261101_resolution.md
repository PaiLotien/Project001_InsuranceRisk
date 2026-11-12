# 11月1日反馈问题处理记录

日期：2026-11-11。分支 upgrade/v2。全量测试 198 项通过（含新增 tests/test_feedback_111.py：Demo、真实样表填数副本、无“机构”列样表三种模式下 7 个页面均无异常）。

| 编号 | 问题 | 处理 | 主要改动 |
| --- | --- | --- | --- |
| 1 | 一键导出/打印、精简布局、指标说明 | 首页提供“一键导出”（多 Sheet Excel 风控报告）与“打印版”（带打印样式 HTML）；新增“指标与阈值说明”折叠表，内容由 YAML 生成；引导内容折叠 | src/dashboard/export.py、streamlit_app.py |
| 2 | 剔除合计行 | 新增 is_total_row（合计/合 计/小计/总计/全车型合计/XX合计/XX-合计）；合计行不定级，各页面统一使用剔除合计行的明细 | utils/helpers.py、alerts/thresholds.py、notification/batch.py |
| 3 | R008 前置条件改为“或” | YAML 新增 precondition_logic: any，规则引擎按 any/all 判断，测试同步 | config/alert_rules.yaml、rules/rule_engine.py |
| 4 | 三级阈值 | 新增橙色规则 R011（默认 95%，待业务确认）；侧栏开放黄/橙/红三级修改并校验 黄＜橙＜红；本地 SQLite 持久化，刷新不丢失，可恢复默认；邮件按预警等级显示对应阈值 | src/config/threshold_store.py、email_templates.py |
| 5 | 汉化与显示 | 公式改为字母+中文解释；预警等级、归因条件、发送状态等界面文字中文化。Streamlit 表格自带菜单（Sort 等）为框架内置，无法在代码中翻译 | streamlit_app.py、attribution/* |
| 6 | DQ001 合计翻倍 | 校验前统一剔除各类合计行，并按机构+Sheet+区块+期间分组求和 | alerts/thresholds.py |
| 7 | 多表识别与单位 | 跳过合并标题行与空行；保留全部期间；读取表头单位说明，存在疑义时在侧栏提示；显示各表块行数与排除原因 | parser/table_blocks.py、upload_data.py |
| 8 | 无“机构”列样表/上传崩溃 | 修复上传后核心指标表、预警中心崩溃（DataFrame 存入 attrs 的问题）；无机构列默认“全辖” | parser/upload_data.py |
| 9 | 休眠规则 | 新增 alerts/pipeline.py::evaluate_frame，页面数据统一经完整规则引擎（含前置条件、组合规则、阈值矩阵）评估，预警表新增“触发规则”“规则类型”列 | src/alerts/pipeline.py |
| 10 | 首页地图 | 首页接入城市风险地图；剔除合计行，按保费加权；计划单列市单独打点；颜色与黄/橙/红阈值对齐 | dashboard/region_map.py |

## 仍需业务确认
- 橙色阈值 95% 为默认参考值，需渤海财险业务确认。
- 红色判定为严格大于阈值（预期成本率恰为 100% 判为橙色/黄色），是否调整需业务确认。
- 第 6、7 项基于合成填数样表验证，需用组员手中真实填写的工作簿复测。
- 邮件仍需在正式 SMTP 配置下做真实投递验收。

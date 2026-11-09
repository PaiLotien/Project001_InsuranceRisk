# IRDS 团队试用说明

本包包含最新源码、Demo、四份业务规则表、研究说明和测试。解压后进入含requirements.txt的IRDS目录。需要预先安装Python 3.10或以上，并联网安装依赖；当前开发机验证版本为Python 3.14。

## Windows

在IRDS目录打开PowerShell，执行以下命令，无需修改脚本执行策略：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run src/dashboard/streamlit_app.py
```

如果系统没有py命令，使用python替换第一行的py。

## macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run src/dashboard/streamlit_app.py
```

启动后打开 http://localhost:8501 。保持终端运行，Ctrl+C停止。如果端口已占用，在启动命令末尾加 --server.port 8502，浏览器访问对应端口。

## 建议体验顺序

1. 首页：确认361条Demo记录、25家机构、22种车型。
2. 核心指标表：输入“企业车”，查看25条结果；尝试机构与等级筛选。
3. 风险预警中心：查看按当前规则重新计算的预警等级及明细，调整黄色阈值并观察结果。以当前页面及规则配置为准。
4. 归因分析中心：选择“上海 | 企业车”，查看原始输入、逐条条件、缺失依据和成本构成图。无法判断表示缺少字段或比较基准，不代表没有风险。
5. 预警通知中心：填写“收件人”和可选“抄送”，查看邮件预览。地址格式错误或空收件人时禁止发送；没有SMTP配置时只模拟发送。
6. AI建议原型：另行安装并启动Ollama，执行 `ollama pull qwen3.5:4b` 下载模型后，选择预警进行问答或生成结构化建议。模型仅解释既有规则结果。
7. 数据上传与报表识别：查看报表类型识别和校验结果。任意原表上传不代表已经完成全部报表的业务验收。

## 邮件配置

单条通知使用页面填写的地址，不追加原配置收件人。批量通知仍走机构配置，本次团队试用请使用单条入口。

如需真实邮件，将根目录.env.example复制为.env，在本机填写SMTP_HOST、SMTP_PORT、SMTP_USE_SSL、SMTP_USER、SMTP_PASSWORD。密码为服务商要求的授权码；服务端SSL一般使用465，STARTTLS一般使用587，具体以邮箱服务商要求为准。配置修改后停止并重新启动应用。

本包不包含开发者的发件账号授权码。Mock成功不代表真实投递；SMTP接受也需收件人确认收件箱或垃圾箱。

## 测试和反馈

先安装测试依赖：python -m pip install -r requirements-dev.txt。

macOS/Linux运行：.venv/bin/python -m pytest -q

Windows运行：.\.venv\Scripts\python.exe -m pytest -q

测试不发送真实邮件。本次包在macOS上进行了测试，Windows命令已提供但未在Windows实机验证。

反馈请附：系统与Python版本、页面名称、操作步骤、预期结果、报错截图。不要附邮箱授权码。更新源码后请完整重启服务，避免旧模块仍留在内存导致方法缺失。

最新说明见release_20261109.md；delivery_20260920.md是历史交付记录，域名和候选数据源见domain_and_data_20260920.md。

## 11月测试反馈版使用变化

上传后先在侧栏选择主数据Sheet；系统不再自动合并所有Sheet，避免同一批数据重复进入统计。其他表块可在数据上传页逐块检查。

历史快照与趋势中的“历史数据维护”支持批量导入、导出、按日期删除及清空；删除需要确认。导入数据需要可识别日期，冲突时整体拒绝导入。演示历史仅在内存生成，不污染本地记录；云端账户同步尚未接入。

批量通知先预览收件人和数量，再勾选确认后启动。支持暂停、继续和停止，已进入发送中的单封邮件可能仍会完成，不自动重试。默认Mock仅模拟发送。

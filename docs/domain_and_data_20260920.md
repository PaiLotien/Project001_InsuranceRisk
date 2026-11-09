# 域名部署与数据源适配研究

核查日期：2026-09-20。仅研究，未购买域名、修改DNS或发布系统。

## 一、域名与部署

域名是访问地址，服务器运行Python应用，DNS把地址指向服务器，HTTPS保护访问连接。单独买域名不会让本机Streamlit自动上线。

建议先在演示环境使用合成数据；若只是答辩链接，可评估Streamlit Community Cloud提供的自定义streamlit.app子域名。完整自有域名建议使用支持Python常驻进程的服务器或托管平台，域名指向服务器后由反向代理处理HTTPS和WebSocket。

执行步骤：

1. 明确谁持有域名、续费责任、预算、访问人群和服务器地区。
2. 选服务器或托管平台，在隔离环境安装依赖并验证六页流程。
3. 注册域名；拥有公网IPv4时配置A记录，平台提供目标域名时按平台要求配置CNAME。DNS解析不是网址跳转，也不自动配置证书。
4. 配置HTTPS证书与反向代理，支持Streamlit WebSocket；应用内部监听回环地址。
5. 把SMTP授权码留在服务端环境变量，通知只允许指定测试收件人；对需要非公开访问的部署设置登录权限。
6. 测试新浏览器访问、上传限制、页面切换、断线重连和服务器重启恢复。

若使用中国内地服务器提供网站服务，需按接入商要求办理ICP备案；不要把域名实名、ICP备案和DNS解析混为一件事。具体主体和材料由实际接入商确认。

来源：
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- https://developers.cloudflare.com/dns/manage-dns-records/how-to/create-subdomain/
- https://help.aliyun.com/zh/dws/getting-started/the-whole-process-of-website-building

## 二、用户提供的数据导航页

已读取WorkBuddy公开页面内容：https://workbuddy.link/p/plSZHyZByMgtXu8gWS3iL9?ext2=copy_link

页面包含欺诈数据、国际汇总数据、监管统计和论文来源。这些是候选线索，页面标注免费不等于原始数据确实开放，论文存在也不代表其保险公司原始记录可下载。不能把不同论文的特征重要性当成本系统归因的正确答案；预测解释与业务异常成因是不同目标。

### 候选适配结论

| 来源 | 可用于什么 | 不能直接验证什么 | 当前核实程度 |
|---|---|---|---|
| Springer 2025生成模型车险欺诈论文 | 年保费、赔款、车辆年份等字段的接口映射；小样本演示 | 全部承保车辆的出险频度、机构成本率、业务归因真值 | 正文核实有1000条、所选13字段；数据可用性只链接Kaggle总目录，精确数据版本与许可未锁定 |
| 导航页MDPI、PeerJ论文 | 待取原始数据后再评估字段 | 不能把论文指标或页面宣称的准确率当系统验收阈值 | 本轮原文访问受限，未核实数据下载与许可 |
| 葡萄牙、捷克硕士论文 | 变量设计参考 | 未证明原始数据公开，不能当作已取得的数据 | 导航页线索，原数据待核实 |
| CASdatasets freMTPL2freq / freMTPL2sev | 保单ID关联、暴露期、赔案次数、赔款与车龄；可构造频度和案均赔款模块测试 | 缺少实收保费、费用及中国机构维度，不能直接算经营成本率；不是欺诈标签集 | 官方数据文档已核对，尚未下载数据 |

Springer原文：https://link.springer.com/article/10.1007/s44163-025-00574-5 ，第2.1节与Data availability。论文列出policy_annual_premium、total_claim_amount、injury_claim、property_claim、vehicle_claim等字段，但保险金额/赔款不能替代已赚保费或费用。

CASdatasets官方字段文档：https://dutangc.github.io/CASdatasets/reference/freMTPL.html 。frequency文件包含未出险保单与暴露期，severity文件包含赔款，适合按保单ID先聚合再连接，避免一保单多赔案重复保费或暴露期。其法国燃油分类不可直接映射中国新能源分类，BonusMalus也不等于中国自主定价系数。

### 可执行的最小试验

1. 锁定具体下载地址、许可、版本和字段字典，再抽取小样本。
2. 保留来源字段与合成字段标记。汇总频度使用赔案总数/暴露期总和，案均赔款使用赔款总额/对应赔案件数，须先核对赔案定义。
3. 缺少保费、费用、机构时，只能另外设定合成值并明确标注，不能宣称它们来自真实保单。
4. 注入单一、已知异常，例如赔款金额放大且其余保持不变，检验系统是否正确显示数值与解释；与现有Demo、真实业务验证分开报告。
5. 欺诈样本若仅包含报案记录，就缺少全部承保风险暴露分母，不能由样本内案件数推断公司出险频度。

结论：可以充当部分字段和模块测试数据，不能直接替代中国车险经营报表，更不能证明现有27条专属归因规则均已正确。优先做受控合成场景加频度/案均模块试验。

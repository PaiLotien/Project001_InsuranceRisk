# upgrade/v2 第1批：后端定量归因与统计异常

本批保持 Streamlit 页面与黄橙红规则逻辑不变。

## 定量归因 `src/attribution/decomposition.py`

- `log_decomposition(reference, current, metric="预期赔付率")`：字典或 Series 输入，字段为 `累计出险频度`（百分数）、`案均赔款`（元）、`单均保费_元`（元）及观测赔付率。返回 `contributions`、`total_change`、`warning`、`fallback`。恒等式为 `ln(LR1/LR0) = ln(f1/f0) + ln(s1/s0) - ln(p1/p0)`。贡献为自然对数，保留负贡献，不是赔付率百分点。
- 沿用 `config/metric_definitions.yaml` 和 `src/rules/metrics.py`：预期赔付率为预期赔款/签单保费，预期成本率还包含费用率，因此不能直接用三要素分解成本率。当前案均赔款为已决赔款/已决案件数，累计频度还包含 RBNR；必须先确认三要素与观测率兼容。支持传入 `频度分母`、`保费分母`、`赔款口径` 元数据；跨期口径不同、分母不同、缺失、零值、非有限值或恒等式校验失败，均显式返回 warning 与 fallback。仅元数据无法证明口径一致，调用方仍应确保数据来源一致。可用相容的实际赔付率字段作为 `metric`；默认不把已决口径强行解释为预期口径。
- `mix_rate_decomposition(reference, current, dimensions=("机构", "车型"), numerator="预期赔款", denominator="签单保费")`：聚合分子分母后，按机构×车型联合切片分解。令 `w` 为分母权重、`r` 为切片率，`mix = Σ[(w1-w0)(r0+r1)/2]`，`rate = Σ[(r1-r0)(w0+w1)/2]`，两者之和为全辖率差。返回明细 DataFrame。使用比率单位，乘100后为百分点。新增/退出切片以另一期的率补齐，将变化归入 mix；非零分子却零分母时报错。机构、车型单独分解时可传单维 dimensions，不能把多个独立分解再相加。
- `additive_drilldown(reference, current, metric, dimensions=("机构", "车型"), top_n=10)`：适用于非负可加量（赔款、保费、件数），枚举单维及交叉切片；解释力为切片绝对变化/联合叶子绝对变化之和，surprise 为切片占比及补集占比的 Bernoulli JS 散度，按两者乘积排序。JS 用自然对数，零概率按连续极限计算。返回维度值、变化量、解释力、surprise、score。切片相互重叠，TOP N 不能直接加总。

`simple_attribution.analyze` 保留旧调用方式，新增可选 `reference` 历史基准，也支持行内 `基期_指标名`。`df_peer` 仍保留兼容，不把同日同业数据当历史。新增 `quantitative_analysis` 字段；定量可用时优先使用，原 `contribution_pct` 为100倍自然对数贡献（描述明确单位）。不可用时保留阈值描述并标注 fallback，将三要素参考项贡献记为0（未知的占位，非真实零贡献），移除经验系数；成本构成展示与车型建议保留。

## 统计异常 `src/monitoring/anomaly.py`

- `score_anomalies(current, metrics, reference=None, dimensions=("机构", "车型"), random_state=42)`：每个分组一行，重复分组需调用方先按指标口径聚合。IsolationForest 使用200棵树，固定随机种子；有 reference 则只在历史基准上拟合，否则在当前横截面拟合。返回 `anomaly_score = clip(-score_samples × 100, 0, 100)`。分数越高越异常，是异常强度而非异常概率；不对应业务预警等级。
- `top_metric` 是将单一指标替换成训练中位数后，森林异常分数下降最多的指标，`top_metric_contribution` 为该下降值。此为模型敏感性解释，不是因果贡献；所有下降都为负时，表示单指标替换均不能缓解异常。缺失值以训练中位数补齐；样本不足、训练指标全缺失、当前行全缺失时报错。
- `detect_deterioration(data, metric="预期成本率", date_column="日期", policy=None, direction=1)`：按机构×车型与日历日期排序，使用严格过去窗口计算单侧滚动 z-score，输出 `sudden_change`、`deterioration_days`、`persistent_deterioration`。缺日或缺值中断连续天数，同组同日重复记录报错。默认指标越大越差；越小越差可设 direction=-1。
- `config/drift_rules.yaml` 新增 `statistical_anomaly.temporal`：14天窗口、至少7天历史、z阈值2、连续3天、标准差下限0.1。持续偏离指连续超出过去滚动基准，不等于连续环比上升。

统计层不读取或写入黄橙红等级，不自动调整任何规则阈值。本批不引入 wise-pizza/Adtributor 依赖，不访问外部数据；新增 scikit-learn 运行依赖并安装到 `.venv`。

## 验证

新增25项 pytest 参数化用例，覆盖对数贡献与结构贡献守恒（绝对容差1e-9）、口径不相容警告、机构×车型注入 TOP3 命中、旧接口与 fallback、森林异常及解释、原等级不变、时序分组隔离、缺日中断和无未来泄漏。

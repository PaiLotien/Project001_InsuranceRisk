"""项目常量定义"""

# 报表类型标识
REPORT_TYPE_T1 = "T1"
REPORT_TYPE_T2 = "T2"
REPORT_TYPE_T3 = "T3"
REPORT_TYPE_T4 = "T4"
REPORT_TYPE_T5 = "T5"
REPORT_TYPE_T6 = "T6"
REPORT_TYPE_T7 = "T7"
REPORT_TYPE_T8 = "T8"
REPORT_TYPE_T9 = "T9"

# 核心指标名称
METRIC_PREMIUM_PCT = "保费占比"
METRIC_SIGNED_PREMIUM = "签单保费"
METRIC_EXPECTED_LOSS_RATIO = "预期赔付率"
METRIC_COST_RATIO_1 = "成本率1"
METRIC_EXPECTED_COST_RATIO = "预期成本率"
METRIC_AUTONOMOUS_COEF = "自主定价系数"
METRIC_AVG_PREMIUM = "单均保费"
METRIC_CLAIM_FREQUENCY = "累计出险频度"
METRIC_COMBINED_RATIO = "两率和"
METRIC_AVG_CLAIM_AMOUNT = "案均赔款"

# T1 日报 - 13大类车型（按样表顺序）
T1_VEHICLE_TYPES = [
    "家用车",
    "出租租赁车",
    "非营业2吨以下",
    "营业10吨以上",
    "营业5-10吨",
    "营业2吨以下",
    "企业车",
    "特种车",
    "摩托车",
    "营业2-5吨",
    "公路客运",
    "机关车",
    "公交车",
]

# T1 子表 - 细化车型
T1_REFINED_VEHICLE_TYPES = [
    "非营业2吨以下_1-2吨",
    "非营业2吨以下_1吨以下",
    "家用车_单交强",
    "家用车_单商业",
    "家用车_交商共保",
    "摩托车_电动",
    "摩托车_燃油",
    "营业2吨以下_1-2吨",
    "营业2吨以下_1吨以下",
]

# T2 日月年 - 时间维度
TIME_DIM_DAILY = "当日"
TIME_DIM_MONTHLY = "当月"
TIME_DIM_YEARLY = "当年"
TIME_DIM_MOM = "月环比"
TIME_DIM_YOY = "月同比"
TIME_DIM_YOY_ANNUAL = "当年同比"

T2_TIME_DIMENSIONS = ["当日", "当月", "月同比", "月环比"]
T2_METRICS_T2 = [
    "保费占比",
    "签单保费",
    "成本率1",
    "预期赔付率",
    "预期成本率",
    "单均保费",
    "自主系数",
]

# 预警等级
ALERT_LEVEL_YELLOW = "黄色预警"
ALERT_LEVEL_ORANGE = "橙色预警"
ALERT_LEVEL_RED = "红色预警"

# 风险因子（归因分析用）
FACTOR_CLAIM_FREQ = "出险频度"
FACTOR_AVG_CLAIM = "案均赔款"
FACTOR_AVG_PREMIUM = "单均保费"
FACTOR_COST_RATIO = "费用成本"

# 取数口径
CALIBER_POLICY_ISSUE = "保批单生成日期"
CALIBER_INCEPTION = "保险起期"

# 机构信息
BRANCH_NAMES = [
    "全辖", "湖北", "江西", "江苏", "浙江", "河北", "陕西",
    "四川", "广西", "重庆", "河南", "湖南", "山东", "深圳",
    "辽宁", "山西", "安徽", "内蒙", "青岛", "北京", "天津",
    "宁波", "上海", "新疆", "云南",
]

"""按车型规则分析成本构成、风险要素并生成建议。"""
import math
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.attribution.evidence import condition_evidence
from src.attribution.decomposition import log_decomposition
from src.alerts.thresholds import configured_thresholds
from src.core.models import AttributionItem
from src.utils.helpers import safe_float

_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _load_attribution_rules() -> dict:
    """加载归因规则配置"""
    config_path = _PROJECT_ROOT / "config" / "attribution_rules.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


_RULES = None


def get_rules() -> dict:
    global _RULES
    if _RULES is None:
        _RULES = _load_attribution_rules()
    return _RULES


def _match_vehicle_type(vehicle_name: str) -> Optional[str]:
    """根据车型名称匹配到车型大类"""
    rules = get_rules()
    dims = rules.get("vehicle_dimensions", {})

    for vehicle_type, config in dims.items():
        aliases = config.get("alias", [])
        if vehicle_name in aliases or vehicle_name == vehicle_type:
            return vehicle_type

        # 模糊匹配：检查车型名是否包含大类关键词
        if vehicle_type in vehicle_name:
            return vehicle_type

    return None


@dataclass
class AttrItem(AttributionItem):
    """沿用旧类型名，字段与公共归因模型共用。"""


@dataclass
class AttrResult:
    """归因分析结果"""
    vehicle_type: str = ""
    matched_dimensions: list = field(default_factory=list)
    cost_driver: str = ""  # "赔付驱动" / "费用驱动"
    loss_ratio_pct: float = 0
    cost_ratio_pct: float = 0
    triggered_rules: list = field(default_factory=list)
    tri_factor_analysis: dict = field(default_factory=dict)
    suggestions: list = field(default_factory=list)
    quantitative_analysis: dict = field(default_factory=dict)
    summary: str = ""
    items: list = field(default_factory=list)


def analyze(row, df_peer=None, *, yellow_threshold: float | None = None,
            frequency_threshold: float | None = None, premium_threshold: float | None = None, reference=None) -> AttrResult:
    """分析单条预警；df_peer 参数保留兼容，当前不参与计算；reference 为显式历史基准。"""
    rules = get_rules()
    if yellow_threshold is None:
        yellow_threshold = configured_thresholds()["yellow"]
    yellow_threshold = float(yellow_threshold)
    if not math.isfinite(yellow_threshold):
        raise ValueError("黄色阈值必须是有限数值")
    vehicle = str(row.get("车型", ""))
    result = AttrResult(vehicle_type=vehicle)

    vehicle_class = _match_vehicle_type(vehicle)
    if vehicle_class:
        dims = rules.get("vehicle_dimensions", {})
        dim_config = dims.get(vehicle_class, {})
        for dimension in dim_config.get("dimensions", []):
            result.matched_dimensions.append({
                "name": dimension["name"],
                "detail": dimension["detail"],
                "metric": dimension.get("metric", ""),
            })

    # 成本构成只描述两项的占比，不是因果贡献度。
    loss_ratio = safe_float(row.get("预期赔付率"))
    cost_ratio = safe_float(row.get("成本率1"))
    total = 0
    components_valid = False
    # 零值可以参与构成分析；缺失、无效或无限值不能当作零补算。
    if loss_ratio is not None and cost_ratio is not None:
        if math.isfinite(loss_ratio) and math.isfinite(cost_ratio):
            total = loss_ratio + cost_ratio
            components_valid = True

    if total > 0:
        result.loss_ratio_pct = round(loss_ratio / total * 100, 1)
        result.cost_ratio_pct = round(cost_ratio / total * 100, 1)
        is_loss_driven = loss_ratio > cost_ratio
        if is_loss_driven:
            result.cost_driver = "赔付风险驱动"
        else:
            result.cost_driver = "费用成本驱动"

        result.items.append(AttrItem(
            dimension="成本构成",
            factor_name="预期赔付率",
            contribution_pct=result.loss_ratio_pct,
            description=f"预期赔付率 {loss_ratio:.1f}%，占总成本率的 {result.loss_ratio_pct}%",
            is_primary=is_loss_driven,
        ))
        result.items.append(AttrItem(
            dimension="成本构成",
            factor_name="成本率1（费用率）",
            contribution_pct=result.cost_ratio_pct,
            description=f"成本率1 {cost_ratio:.1f}%，占总成本率的 {result.cost_ratio_pct}%",
            is_primary=not is_loss_driven,
        ))

    if vehicle_class:
        attr_rules = rules.get("attribution_rules", {}).get(vehicle_class, [])
        for rule in attr_rules:
            matched = True
            matched_conditions = []
            for condition in rule.get("conditions", []):
                ok = condition_evidence(condition, row)["matched"]
                name = condition.get("metric", condition.get("dimension", ""))
                op = condition.get("operator", "")
                value = condition.get("value", condition.get("value_ref", ""))
                matched_conditions.append({
                    "description": f"{name} {op} {value}",
                    "matched": ok,
                })
                if not ok:
                    matched = False
                    break
            if matched:
                result.triggered_rules.append({
                    "rule_id": rule.get("id", ""),
                    "conclusion": rule.get("conclusion", ""),
                    "suggestions": rule.get("suggestions", []),
                    "conditions": matched_conditions,
                })

    claim_freq = safe_float(row.get("累计出险频度"))
    avg_premium = safe_float(row.get("单均保费_元"))

    reference_thresholds = rules.get("tri_factor_analysis", {}).get("reference_thresholds", {})
    if frequency_threshold is None:
        frequency_threshold = float(reference_thresholds.get("claim_frequency_percent", 25))
    if premium_threshold is None:
        premium_threshold = float(reference_thresholds.get("average_premium_yuan", 2000))
    # 以下是可配置参考阈值；缺少历史基准，不能判断同比变化。
    tri_findings = []
    freq_up = claim_freq and claim_freq > frequency_threshold
    prem_down = avg_premium and avg_premium < premium_threshold

    if freq_up:
        tri_findings.append("出险频度偏高")
    if prem_down:
        tri_findings.append("单均保费偏低")

    if len(tri_findings) >= 2:
        result.tri_factor_analysis = {
            "severity": "高",
            "summary": "多个要素出现恶化信号：" + "、".join(tri_findings),
            "detail": f"参考阈值：出险频度 > {frequency_threshold:g}%，且单均保费 < {premium_threshold:g} 元；无历史基准，不能判断同比恶化，也不能推断案均赔款上升。",
        }
    elif freq_up:
        result.tri_factor_analysis = {
            "severity": "中",
            "summary": "出险频度偏高，事故概率增加",
            "detail": "可能原因：承保车辆风险结构变化、外部环境恶化、驾驶行为变化。应对：排查核保政策是否松动。",
        }
    elif prem_down:
        result.tri_factor_analysis = {
            "severity": "中",
            "summary": "单均保费偏低，保费充足率不足",
            "detail": "可能原因：自主定价系数偏低、业务结构变化、竞争性降价。应对：复核自主定价系数。",
        }

    baseline = reference if reference is not None else {
        str(key)[3:]: value for key, value in row.items() if str(key).startswith("基期_")
    }
    quantitative = log_decomposition(baseline, row)
    result.quantitative_analysis = quantitative
    if not quantitative["fallback"]:
        result.tri_factor_analysis = {
            **quantitative, "severity": "高" if quantitative["total_change"] > 0 else "低",
            "summary": "赔付率三要素对数分解",
            "detail": "贡献以自然对数表示，三项之和等于赔付率对数变化。",
        }
        for name, contribution in quantitative["contributions"].items():
            result.items.append(AttrItem(
                dimension="三要素", factor_name=name, contribution_pct=contribution * 100,
                description=f"对数贡献 {contribution:+.6f}（非百分点）",
                is_primary=contribution == max(quantitative["contributions"].values()),
            ))
    else:
        if result.tri_factor_analysis:
            result.tri_factor_analysis.update({"fallback": True, "warning": quantitative["warning"]})
        for name, value in (("出险频度", safe_float(claim_freq)),
                            ("单均保费", safe_float(avg_premium))):
            if value is not None and math.isfinite(value):
                result.items.append(AttrItem(
                    dimension="三要素", factor_name=name, contribution_pct=0,
                    description=f"降级说明：{name} {value:g}；仅作水平参考，无法量化历史贡献",
                    is_primary=False,
                ))

    suggestions = []
    for rule in result.triggered_rules:
        suggestions.extend(rule.get("suggestions", []))

    # 专属规则没有给出建议时，按成本构成提供通用检查方向。
    if not suggestions:
        if total > 0 and loss_ratio > cost_ratio:
            suggestions.extend([
                "下钻分析出险频度和案均赔款，定位赔付恶化原因",
                "检查该车型在该机构的核保政策是否需要收紧",
                "按新续转维度拆分，排查是否存在逆选择",
            ])
        elif total > 0:
            suggestions.extend([
                "检查该车型的市场费用率是否过高",
                "评估是否需要调整渠道费用政策",
                "对比同地区同车型的市场费用率水平",
            ])
        else:
            suggestions.append("建议人工下钻分析具体风险维度（机构/车型/新续转/渠道）")

    result.suggestions = suggestions[:6]  # 最多6条

    triggered_ids = []
    for rule in result.triggered_rules:
        triggered_ids.append(rule["rule_id"])
    total_cost = total if components_valid else row.get("预期成本率", 0)
    deviation = total_cost - yellow_threshold

    parts = []
    if result.cost_driver:
        parts.append(f"【{result.cost_driver}】")
    parts.append(
        f"{row.get('机构', '')} {vehicle} 预期成本率 {total_cost:.1f}%，"
        f"偏离黄色预警阈值({yellow_threshold:g}%) {deviation:+.1f}pp"
    )
    if triggered_ids:
        parts.append(f"触发 {len(triggered_ids)} 条车型专属规则（{', '.join(triggered_ids)}）")
    result.summary = "。".join(parts) + "。"

    return result


def batch_analyze(df, alert_df=None, *, yellow_threshold: float | None = None):
    """批量归因分析"""
    results = []
    data = alert_df if alert_df is not None else df
    for _, row in data.iterrows():
        results.append(analyze(row, df, yellow_threshold=yellow_threshold))
    return results

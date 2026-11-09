"""归因报告生成器

将归因分析结果格式化为人类可读的报告文本。
"""

from src.core.models import AttributionResult


def generate_text_report(result: AttributionResult) -> str:
    """生成单条预警的归因文字报告"""
    alert = result.alert
    lines = [
        "## 归因分析报告",
        "",
        f"**预警指标**: {alert.metric_name}",
        f"**预警对象**: {alert.branch} / {alert.vehicle_type}",
        f"**当前值**: {alert.current_value:.1f}%  |  **阈值**: {alert.threshold}%",
        f"**偏离幅度**: {alert.current_value - alert.threshold:+.1f} 个百分点",
        f"**风险等级**: {alert.alert_level.value}",
        "",
        "### 归因结论",
        "",
        result.summary,
        "",
    ]

    if result.items:
        lines.append("### 归因明细")
        lines.append("")
        lines.append("| 维度 | 因子 | 贡献度 | 说明 |")
        lines.append("|------|------|--------|------|")
        for item in result.items:
            primary_marker = " ★" if item.is_primary else ""
            lines.append(
                f"| {item.dimension} | {item.factor_name}{primary_marker} "
                f"| {item.contribution_pct}% | {item.description} |"
            )
        lines.append("")

    if result.suggestions:
        lines.append("### 整改建议")
        lines.append("")
        for i, sug in enumerate(result.suggestions, 1):
            lines.append(f"{i}. {sug}")

    return "\n".join(lines)


def generate_batch_summary(results: list[AttributionResult]) -> str:
    """生成批量归因摘要"""
    if not results:
        return "无预警触发，系统运行正常。"

    lines = [
        "# 批量归因分析摘要",
        "",
        f"共触发 **{len(results)}** 条预警，以下为归因分析要点：",
        "",
    ]

    # 按主要归因因子分组
    loss_driven = []
    cost_driven = []
    premium_driven = []
    other = []

    for r in results:
        if "赔付风险驱动" in r.summary:
            loss_driven.append(r)
        elif "费用成本驱动" in r.summary:
            cost_driven.append(r)
        elif "保费充足度风险" in r.summary:
            premium_driven.append(r)
        else:
            other.append(r)

    if loss_driven:
        lines.append(f"### 🔴 赔付风险驱动型（{len(loss_driven)}条）")
        for r in loss_driven:
            lines.append(f"- {r.alert.vehicle_type}: {r.alert.metric_name} = {r.alert.current_value:.1f}%")
        lines.append("")

    if cost_driven:
        lines.append(f"### 🟡 费用成本驱动型（{len(cost_driven)}条）")
        for r in cost_driven:
            lines.append(f"- {r.alert.vehicle_type}: {r.alert.metric_name} = {r.alert.current_value:.1f}%")
        lines.append("")

    if premium_driven:
        lines.append(f"### 🟠 保费充足度风险型（{len(premium_driven)}条）")
        for r in premium_driven:
            lines.append(f"- {r.alert.vehicle_type}: 单均保费异常")
        lines.append("")

    if other:
        lines.append(f"### 其他（{len(other)}条）")
        for r in other:
            lines.append(f"- {r.alert.vehicle_type}: {r.alert.metric_name}")
        lines.append("")

    return "\n".join(lines)

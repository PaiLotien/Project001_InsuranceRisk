"""邮件模板生成器

根据预警信息和归因结果生成专业的邮件标题和正文。
"""

from datetime import datetime
from typing import Any, Optional

from src.alerts.thresholds import configured_thresholds


def _get_alert_emoji(level: str) -> str:
    """预警等级 → emoji"""
    level_lower = level.lower()
    if "红色" in level or "red" in level_lower:
        return "🔴"
    elif "橙色" in level or "orange" in level_lower:
        return "🟠"
    elif "黄色" in level or "yellow" in level_lower:
        return "🟡"
    return "⚪"


def _get_urgency_text(level: str) -> str:
    """预警等级 → 紧急程度描述"""
    level_lower = level.lower()
    if "红色" in level or "red" in level_lower:
        return "【特急】此为红色预警，请于4小时内响应并启动应急处置流程。"
    elif "橙色" in level or "orange" in level_lower:
        return "【紧急】此为橙色预警，请于24小时内响应并制定整改方案。"
    elif "黄色" in level or "yellow" in level_lower:
        return "【提示】此为黄色预警，请于48小时内关注并评估风险态势。"
    return ""


def _alert_fields(alert: Any, threshold: float | None = None) -> dict:
    """统一读取预警对象或上传明细中的公共邮件字段。"""
    is_record = hasattr(alert, "alert_level")
    if is_record:
        level_value = alert.alert_level.value if hasattr(alert.alert_level, "value") else str(alert.alert_level)
        branch = getattr(alert, "branch", "全辖") or "全辖"
        vehicle = getattr(alert, "vehicle_type", "")
        metric_name = getattr(alert, "metric_name", "")
        current_value = getattr(alert, "current_value", 0)
        threshold = getattr(alert, "threshold", 0)
    else:
        level_map = {"red": "红色预警", "orange": "橙色预警", "yellow": "黄色预警"}
        raw_level = alert.get("预警等级", "yellow")
        level_value = level_map.get(raw_level, raw_level)
        branch = alert.get("机构", "全辖") or "全辖"
        vehicle = alert.get("车型", "")
        metric_name = alert.get("metric_name", "预期成本率")
        current_value = alert.get("预期成本率", 0)
        if threshold is None:
            threshold = configured_thresholds()["yellow"]
            if float(threshold).is_integer():
                threshold = int(threshold)

    return {
        "level": level_value,
        "branch": branch,
        "vehicle": vehicle,
        "metric": metric_name,
        "value": current_value,
        "threshold": threshold,
        "is_record": is_record,
    }


def generate_subject(
    alert: Any,
    prefix: str = "[车险风控预警]",
    threshold: float | None = None,
) -> str:
    """生成含预警等级、机构、车型和指标的邮件标题。"""
    fields = _alert_fields(alert, threshold)
    emoji = _get_alert_emoji(fields["level"])

    return (
        f"{prefix}{emoji}[{fields['level']}] "
        f"{fields['branch']}-{fields['vehicle']} {fields['metric']}{fields['value']:.1f}% 触发预警"
    )


def generate_body(
    alert: Any,
    context: Any = None,  # NotificationContext
    attribution_summary: str = "",
    suggestions: Optional[list[str]] = None,
    threshold: float | None = None,
) -> str:
    """生成文本邮件正文，支持附加归因摘要和整改建议。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    fields = _alert_fields(alert, threshold)
    level_value = fields["level"]
    branch = fields["branch"]
    vehicle = fields["vehicle"]
    metric_name = fields["metric"]
    current_value = fields["value"]
    threshold = fields["threshold"]
    deviation = current_value - threshold
    if not fields["is_record"]:
        # 明细表正文沿用经营成本率口径；标题允许显示自定义指标名。
        metric_name = "预期成本率"
        loss_ratio = alert.get("预期赔付率", None)
        cost_ratio = alert.get("成本率1", None)

    emoji = _get_alert_emoji(level_value)
    urgency = _get_urgency_text(level_value)

    # 响应时效
    response_hours = 48
    if context and hasattr(context, "response_hours"):
        response_hours = context.response_hours

    # 部门
    dept = "车险部"
    if context and hasattr(context, "department"):
        dept = context.department

    # 构建正文
    lines = [
        f"═══════════════════════════════════════",
        f"  车险风险预警通知",
        f"═══════════════════════════════════════",
        f"",
        f"发送时间：{now}",
        f"预警等级：{emoji} {level_value}",
        f"优先级别：{context.priority if context else '普通'}",
        f"关注部门：{dept}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  预警摘要",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"  机构名称：{branch}",
        f"  车型分类：{vehicle}",
        f"  监控指标：{metric_name}",
        f"  当前数值：{current_value:.1f}%",
        f"  预警阈值：{threshold}%",
        f"  偏离幅度：{deviation:+.1f} 个百分点",
        f"  预警等级：{emoji} {level_value}",
        f"",
    ]

    # 赔付率/费用率详情（如可用）
    if hasattr(alert, "预期赔付率") or (not hasattr(alert, "alert_level") and alert.get("预期赔付率")):
        lr = loss_ratio if not hasattr(alert, "alert_level") else getattr(alert, "预期赔付率", None)
        cr = cost_ratio if not hasattr(alert, "alert_level") else getattr(alert, "成本率1", None)
        if lr is not None and cr is not None:
            lines.extend([
                f"",
                f"  明细指标：",
                f"    预期赔付率：{lr:.1f}%",
                f"    成本率1（费用率）：{cr:.1f}%",
                f"    预期成本率 = 预期赔付率 + 成本率1 = {lr + cr:.1f}%",
            ])

    lines.append("")

    # 归因分析
    if attribution_summary:
        lines.extend([
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  归因分析",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"",
            attribution_summary,
            f"",
        ])

    # 整改建议
    if suggestions:
        lines.extend([
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  建议处置动作",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"",
        ])
        for i, sug in enumerate(suggestions, 1):
            lines.append(f"  {i}. {sug}")
        lines.append("")

    # 行动要求
    lines.extend([
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  行动要求",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        urgency,
        f"",
        f"  响应时效：{response_hours} 小时内",
        f"  关注部门：{dept}",
        f"",
    ])

    # 收件人
    if context:
        lines.extend([
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  通知对象",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"",
        ])
        for r in context._recipients:
            lines.append(f"  {r.name} — {r.title}")
        lines.append("")

    # 页脚
    lines.extend([
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"此邮件由渤海财险车险智能风控系统（IRDS）自动生成。",
        f"如需退订或调整通知设置，请联系系统管理员。",
        f"系统管理员：信息科技部 王工（wangit@bohai-ins.com）",
    ])

    return "\n".join(lines)


def generate_html_body(
    alert: Any,
    context: Any = None,
    attribution_summary: str = "",
    suggestions: Optional[list[str]] = None,
    threshold: float | None = None,
) -> str:
    """生成 HTML 格式邮件正文（用于页面预览）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    fields = _alert_fields(alert, threshold)
    level_value = fields["level"]
    branch = fields["branch"]
    vehicle = fields["vehicle"]
    metric_name = fields["metric"]
    current_value = fields["value"]
    threshold = fields["threshold"]
    deviation = current_value - threshold
    if not fields["is_record"]:
        metric_name = "预期成本率"
        loss_ratio = alert.get("预期赔付率", None)
        cost_ratio = alert.get("成本率1", None)

    emoji = _get_alert_emoji(level_value)

    # 预警等级颜色
    level_colors = {
        "红色预警": "#dc3545",
        "橙色预警": "#fd7e14",
        "黄色预警": "#ffc107",
    }
    border_color = level_colors.get(level_value, "#6c757d")
    bg_color = {
        "红色预警": "#fff5f5",
        "橙色预警": "#fff8f0",
        "黄色预警": "#fffef5",
    }.get(level_value, "#f8f9fa")

    html = f"""
    <div style="font-family: 'Microsoft YaHei', Arial, sans-serif; max-width: 680px; margin: 0 auto;">

        <!-- 标题栏 -->
        <div style="background: {border_color}; padding: 16px 24px; border-radius: 8px 8px 0 0;">
            <h2 style="margin:0; color: white; font-size: 18px;">
                {emoji} 车险风险预警通知 — {level_value}
            </h2>
            <p style="margin: 4px 0 0; color: rgba(255,255,255,0.85); font-size: 12px;">
                发送时间: {now} | 优先级: {context.priority if context else '普通'}
            </p>
        </div>

        <!-- 正文 -->
        <div style="background: #ffffff; padding: 24px; border: 1px solid #e0e0e0; border-top: none;">

            <!-- 预警摘要 -->
            <h3 style="margin: 0 0 16px 0; color: #333; border-bottom: 2px solid {border_color}; padding-bottom: 8px;">
                预警摘要
            </h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <tr>
                    <td style="padding: 6px 12px; color: #666; width: 120px;">机构名称</td>
                    <td style="padding: 6px 12px;"><strong>{branch}</strong></td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 6px 12px; color: #666;">车型分类</td>
                    <td style="padding: 6px 12px;"><strong>{vehicle}</strong></td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; color: #666;">监控指标</td>
                    <td style="padding: 6px 12px;"><strong>{metric_name}</strong></td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 6px 12px; color: #666;">当前数值</td>
                    <td style="padding: 6px 12px;">
                        <span style="color: {border_color}; font-weight: bold; font-size: 18px;">{current_value:.1f}%</span>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; color: #666;">预警阈值</td>
                    <td style="padding: 6px 12px;">{threshold}%</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 6px 12px; color: #666;">偏离幅度</td>
                    <td style="padding: 6px 12px; color: {border_color}; font-weight: bold;">{deviation:+.1f} 个百分点</td>
                </tr>
            </table>
    """

    # 赔付率/费用率详情
    if not hasattr(alert, "alert_level"):
        lr = loss_ratio
        cr = cost_ratio
        if lr is not None and cr is not None:
            html += f"""
            <h4 style="margin: 16px 0 8px; color: #555;">明细指标</h4>
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <tr>
                    <td style="padding: 4px 12px; color: #666;">预期赔付率</td>
                    <td style="padding: 4px 12px;">{lr:.1f}%</td>
                </tr>
                <tr>
                    <td style="padding: 4px 12px; color: #666;">成本率1（费用率）</td>
                    <td style="padding: 4px 12px;">{cr:.1f}%</td>
                </tr>
                <tr>
                    <td style="padding: 4px 12px; color: #666;">预期成本率</td>
                    <td style="padding: 4px 12px;"><strong>= {lr + cr:.1f}%</strong></td>
                </tr>
            </table>
            """

    # 归因分析
    if attribution_summary:
        html += f"""
            <h3 style="margin: 20px 0 12px 0; color: #333; border-bottom: 2px solid {border_color}; padding-bottom: 8px;">
                归因分析
            </h3>
            <div style="background: {bg_color}; padding: 14px; border-radius: 6px; font-size: 14px; line-height: 1.8; border-left: 4px solid {border_color};">
                {attribution_summary}
            </div>
        """

    # 整改建议
    if suggestions:
        html += """
            <h3 style="margin: 20px 0 12px 0; color: #333; border-bottom: 2px solid #333; padding-bottom: 8px;">
                建议处置动作
            </h3>
            <ol style="font-size: 14px; line-height: 1.8; padding-left: 20px;">
        """
        for sug in suggestions:
            html += f"<li>{sug}</li>"
        html += "</ol>"

    # 行动要求
    urgency = _get_urgency_text(level_value)
    response_hours = context.response_hours if context else 48
    dept = context.department if context else "车险部"

    html += f"""
            <h3 style="margin: 20px 0 12px 0; color: #333; border-bottom: 2px solid #333; padding-bottom: 8px;">
                行动要求
            </h3>
            <div style="background: {bg_color}; padding: 14px; border-radius: 6px; font-size: 14px;">
                <p style="margin: 0 0 8px 0;"><strong>{urgency}</strong></p>
                <p style="margin: 4px 0;">响应时效：<strong>{response_hours} 小时内</strong></p>
                <p style="margin: 4px 0;">关注部门：<strong>{dept}</strong></p>
            </div>
    """

    # 收件人
    if context:
        html += """
            <h3 style="margin: 20px 0 12px 0; color: #333; border-bottom: 2px solid #333; padding-bottom: 8px;">
                通知对象
            </h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
        """
        for r in context._recipients:
            html += f"""
                <tr>
                    <td style="padding: 4px 8px; color: #666; width: 100px;">{r.role}</td>
                    <td style="padding: 4px 8px;"><strong>{r.name}</strong></td>
                    <td style="padding: 4px 8px; color: #888;">{r.title}</td>
                    <td style="padding: 4px 8px; color: #1a73e8;">{r.email}</td>
                </tr>
            """
        html += "</table>"

    # 页脚
    html += f"""
        </div>

        <!-- 页脚 -->
        <div style="background: #f5f5f5; padding: 14px 24px; border-radius: 0 0 8px 8px; font-size: 12px; color: #999; border: 1px solid #e0e0e0; border-top: none;">
            <p style="margin: 0 0 4px 0;">此邮件由<strong>渤海财险车险智能风控系统（IRDS）</strong>自动生成。</p>
            <p style="margin: 0;">如需退订或调整通知设置，请联系系统管理员：信息科技部 王工（wangit@bohai-ins.com）</p>
        </div>

    </div>
    """

    return html

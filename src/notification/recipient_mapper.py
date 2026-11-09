"""收件人映射器

根据预警记录的机构、预警等级、指标类型，自动匹配通知收件人列表。
所有映射规则从 config/notification_rules.yaml 读取。
"""

from pathlib import Path
from typing import Any, Optional

import yaml

from src.core.constants import (
    METRIC_AVG_PREMIUM,
    METRIC_CLAIM_FREQUENCY,
    METRIC_COST_RATIO_1,
    METRIC_EXPECTED_COST_RATIO,
    METRIC_EXPECTED_LOSS_RATIO,
    METRIC_PREMIUM_PCT,
    METRIC_SIGNED_PREMIUM,
)

_CONFIG_PATH = (
    Path(__file__).parent.parent.parent / "config" / "notification_rules.yaml"
)

# 标准化指标名 → yaml key 映射
_METRIC_TO_YAML_KEY = {
    METRIC_EXPECTED_LOSS_RATIO: "预期赔付率",
    METRIC_COST_RATIO_1: "成本率1",
    METRIC_EXPECTED_COST_RATIO: "预期成本率",
    METRIC_CLAIM_FREQUENCY: "累计出险频度",
    "案均赔款": "案均赔款",
    METRIC_AVG_PREMIUM: "单均保费",
    METRIC_SIGNED_PREMIUM: "签单保费",
    METRIC_PREMIUM_PCT: "保费占比",
}


def _load_config() -> dict[str, Any]:
    """读取通知规则配置。"""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"通知规则配置文件不存在: {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Recipient:
    """单个收件人"""

    def __init__(self, name: str, email: str, title: str, role: str = ""):
        self.name = name
        self.email = email
        self.title = title
        self.role = role

    def __repr__(self):
        return f"{self.name} <{self.email}> ({self.title})"

    def to_dict(self) -> dict[str, str]:
        return {
            "姓名": self.name,
            "邮箱": self.email,
            "职务": self.title,
            "角色": self.role,
        }


class NotificationContext:
    """单条预警的完整通知上下文"""

    def __init__(
        self,
        alert: Any,  # AlertRecord | dict (from DataFrame row)
        escalation: dict,
        branch_contacts: dict,
        metric_dept: Optional[dict],
        email_config: dict,
    ):
        self.alert = alert
        self.escalation = escalation
        self.branch_contacts = branch_contacts
        self.metric_dept = metric_dept
        self.email_config = email_config
        self._recipients: list[Recipient] = []
        self._build_recipient_list()

    def _build_recipient_list(self):
        """根据预警等级和机构构建收件人列表"""
        configured_roles = self.escalation.get("roles", [])

        for role_key in configured_roles:
            contact = self.branch_contacts.get(role_key)
            if contact:
                recipient = Recipient(
                    name=contact.get("name", ""),
                    email=contact.get("email", ""),
                    title=contact.get("title", ""),
                    role=role_key,
                )
                self._recipients.append(recipient)

    @property
    def to_list(self) -> list[str]:
        """To: 主要收件人邮箱列表"""
        addresses = []
        for recipient in self._recipients:
            addresses.append(recipient.email)
        return addresses

    def override_addresses(self, to_list: list[str], cc_list: list[str]) -> None:
        """仅替换本次通知对象，不修改机构配置。"""
        self._recipients = []
        for address in to_list:
            self._recipients.append(Recipient("指定收件人", address, "收件人"))
        self._edited_cc = list(cc_list)

    @property
    def cc_list(self) -> list[str]:
        """CC: 抄送人邮箱列表"""
        if hasattr(self, "_edited_cc"):
            return list(self._edited_cc)
        cc = []
        if self.metric_dept:
            extra_roles = self.metric_dept.get("cc_roles", [])
            for role_key in extra_roles:
                contact = self.branch_contacts.get(role_key)
                if contact:
                    cc.append(contact.get("email", ""))
        return cc

    @property
    def priority(self) -> str:
        return self.escalation.get("priority", "普通")

    @property
    def response_hours(self) -> int:
        return self.escalation.get("response_hours", 48)

    @property
    def escalation_description(self) -> str:
        return self.escalation.get("description", "")

    @property
    def department(self) -> str:
        if self.metric_dept:
            return self.metric_dept.get("dept", "车险部")
        return "车险部"


def get_notification_context(alert: Any) -> NotificationContext:
    """按预警的机构、等级、指标创建通知上下文，支持对象或字典。"""
    config = _load_config()

    # 确定预警等级和机构
    if hasattr(alert, "alert_level"):
        # AlertRecord 对象
        level_value = alert.alert_level.value if hasattr(alert.alert_level, "value") else str(alert.alert_level)
        branch = getattr(alert, "branch", "全辖") or "全辖"
        metric_name = getattr(alert, "metric_name", "")
    else:
        # DataFrame row (dict-like)
        level_map = {
            "red": "红色预警",
            "orange": "橙色预警",
            "yellow": "黄色预警",
        }
        raw_level = alert.get("预警等级", "yellow")
        level_value = level_map.get(raw_level, "黄色预警")
        branch = alert.get("机构", "全辖") or "全辖"
        metric_name = alert.get("metric_name", "预期成本率")

    # 查找升级规则
    escalation_map = {
        "黄色预警": "yellow",
        "橙色预警": "orange",
        "红色预警": "red",
    }
    escalation_key = escalation_map.get(level_value, "yellow")
    escalation = config.get("alert_escalation", {}).get(escalation_key, {})

    # 查找分支机构联系人
    all_contacts = config.get("branch_contacts", {})
    branch_contacts = all_contacts.get(branch, all_contacts.get("全辖", {}))

    # 查找指标对应部门
    yaml_metric_key = _METRIC_TO_YAML_KEY.get(metric_name, metric_name)
    metric_dept = config.get("metric_departments", {}).get(yaml_metric_key)

    # 邮件配置
    email_config = config.get("email_config", {})

    return NotificationContext(
        alert=alert,
        escalation=escalation,
        branch_contacts=branch_contacts,
        metric_dept=metric_dept,
        email_config=email_config,
    )


def format_recipient_list(context: NotificationContext) -> list[dict[str, str]]:
    """格式化收件人列表为展示用 dict 列表"""
    return [r.to_dict() for r in context._recipients]

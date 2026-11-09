"""前端收件地址解析，不接受显示名称或邮件头内容。"""
import re


def parse_addresses(text: str) -> list[str]:
    """允许逗号、分号或换行分隔，验证后按首次出现顺序去重。"""
    addresses = []
    seen = set()
    for value in re.split(r"[,;，；\n]+", text):
        value = value.strip()
        if not value:
            continue
        if len(value) > 254 or not re.fullmatch(
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value
        ):
            raise ValueError(f"邮箱格式不正确：{value}")
        local = value.split('@')[0]
        if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
            raise ValueError(f"邮箱格式不正确：{value}")
        if value.lower() not in seen:
            addresses.append(value)
            seen.add(value.lower())
    return addresses

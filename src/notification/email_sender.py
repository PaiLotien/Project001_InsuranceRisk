"""邮件发送器

支持两种模式（自动检测）：
- 真实 SMTP 发送：配置环境变量 SMTP_HOST 后自动启用
- Mock 模式：未配置 SMTP 时的演示回退

环境变量：
  SMTP_HOST        - SMTP 服务器地址（如 smtp.qq.com）
  SMTP_PORT        - 端口（默认 587 STARTTLS / 465 SSL）
  SMTP_USER        - 登录用户名（通常即发件邮箱）
  SMTP_PASSWORD    - 登录密码或授权码
  SMTP_USE_SSL     - 设为 1 使用 SSL（端口 465），否则 STARTTLS（端口 587）
  SMTP_FROM        - 发件人地址（可选，默认同 SMTP_USER）
  SMTP_FROM_NAME   - 发件人显示名称（可选）
"""

import os
import smtplib
import time
import ssl
from email.utils import formataddr, formatdate, make_msgid
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional


@dataclass
class SendRecord:
    """单次发送记录"""

    send_id: str
    timestamp: str
    recipients: list[str]
    cc: list[str]
    subject: str
    body_preview: str
    status: str  # "success" | "failed"
    error_message: str = ""
    mode: str = "mock"  # "smtp" | "mock"


# 全局发送记录（会话级）
_send_history: list[SendRecord] = []


def get_send_history() -> list[SendRecord]:
    return list(_send_history)


def clear_send_history():
    _send_history.clear()


def _smtp_config() -> dict:
    """读取 SMTP 配置，未配置返回空 dict"""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return {}
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "465" if os.environ.get("SMTP_USE_SSL") == "1" else "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_ssl": os.environ.get("SMTP_USE_SSL", "0") == "1",
        "from_addr": os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", ""),
        "from_name": os.environ.get("SMTP_FROM_NAME", ""),
    }


def is_smtp_available() -> bool:
    """检测 SMTP 是否已配置"""
    try:
        return bool(_smtp_config())
    except (ValueError, TypeError):
        return False


def send_email(
    subject: str,
    body: str,
    to_list: list[str],
    cc_list: Optional[list[str]] = None,
    context: Any = None,
    dry_run: bool = False,
) -> SendRecord:
    """发送 HTML 邮件并记录结果；未配置 SMTP 时使用 Mock。"""
    cc_list = cc_list or []
    send_id = f"SEND-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{len(_send_history) + 1:03d}"

    if not to_list:
        record = SendRecord(
            send_id=send_id,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            recipients=[], cc=[], subject=subject,
            body_preview=body[:200] + ("..." if len(body) > 200 else ""),
            status="failed", error_message="收件人列表为空", mode="mock",
        )
        _send_history.append(record)
        return record

    try:
        cfg = {} if dry_run else _smtp_config()
    except (ValueError, TypeError):
        record = SendRecord(send_id, datetime.now().isoformat(), list(to_list), list(cc_list),
                            subject, body[:200], "failed", "SMTP 端口配置无效", "smtp")
        _send_history.append(record)
        return record

    if not cfg:
        time.sleep(0.2)
        record = SendRecord(
            send_id=send_id,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            recipients=list(to_list), cc=list(cc_list),
            subject=subject,
            body_preview=body[:200] + ("..." if len(body) > 200 else ""),
            status="success", mode="mock",
        )
        _send_history.append(record)
        return record

    status = "failed"
    error_message = ""
    try:
        msg = MIMEMultipart("alternative")
        from_display = formataddr((cfg["from_name"], cfg["from_addr"]))
        msg["From"] = from_display
        msg["To"] = ", ".join(to_list)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        msg.attach(MIMEText(body, "html", "utf-8"))

        all_recipients = to_list + cc_list

        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
            server.starttls(context=ssl.create_default_context())

        try:
            server.login(cfg["user"], cfg["password"])
            refused = server.sendmail(cfg["from_addr"], all_recipients, msg.as_string())
        finally:
            # 邮件已被接受后，QUIT 失败不应诱导用户重复投递。
            try:
                server.quit()
            except Exception:
                server.close()

        if refused:
            error_message = "部分收件人被拒绝；请核对，勿向已接受者重复发送"
        else:
            status = "success"
    except smtplib.SMTPAuthenticationError as e:
        error_message = f"SMTP 认证失败: {e.smtp_error}"
    except smtplib.SMTPException as e:
        error_message = f"SMTP 发送失败: {e}"
    except Exception as e:
        error_message = str(e)

    record = SendRecord(
        send_id=send_id,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        recipients=list(to_list), cc=list(cc_list),
        subject=subject,
        body_preview=body[:200] + ("..." if len(body) > 200 else ""),
        status=status,
        error_message=error_message,
        mode="smtp",
    )
    _send_history.append(record)
    return record


# 向后兼容别名
mock_send_email = send_email


def get_last_send() -> Optional[SendRecord]:
    if _send_history:
        return _send_history[-1]
    return None

"""邮件结果归档与解析入口的回归检查。"""

import os
import smtplib
import unittest
from io import BytesIO
from unittest.mock import patch

from openpyxl import Workbook

from src.notification.email_sender import clear_send_history, get_send_history, send_email
from src.notification.recipient_mapper import NotificationContext
from src.parser.excel_loader import get_sheet_dimensions, load_uploaded_file, read_rows


class MailResultTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "SMTP_HOST": "smtp.example.test", "SMTP_PORT": "465", "SMTP_USE_SSL": "1",
            "SMTP_USER": "sender@example.test", "SMTP_PASSWORD": "test-only",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        clear_send_history()
        self.addCleanup(clear_send_history)

    @patch("src.notification.email_sender.smtplib.SMTP_SSL", side_effect=OSError("offline"))
    def test_connection_failure_is_recorded_once(self, factory):
        record = send_email("测试", "正文", ["to@example.test"])
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_message, "offline")
        self.assertEqual(record.mode, "smtp")
        self.assertEqual(get_send_history(), [record])
        factory.assert_called_once()

    @patch("src.notification.email_sender.smtplib.SMTP_SSL")
    def test_protocol_failure_keeps_smtp_message(self, factory):
        factory.return_value.sendmail.side_effect = smtplib.SMTPException("protocol error")
        record = send_email("测试", "正文", ["to@example.test"])
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_message, "SMTP 发送失败: protocol error")
        self.assertEqual(len(get_send_history()), 1)
        factory.return_value.sendmail.assert_called_once()
        factory.return_value.quit.assert_called_once()


class InputAndRecipientTests(unittest.TestCase):
    def test_address_override_preserves_order_and_does_not_change_config(self):
        contacts = {"manager": {"name": "演示", "email": "old@example.test", "title": "经理"}}
        context = NotificationContext({}, {"roles": ["manager"]}, contacts, None, {})
        addresses = ["b@example.test", "a@example.test"]
        cc = ["cc@example.test"]
        context.override_addresses(addresses, cc)
        addresses.clear()
        cc.clear()
        self.assertEqual(context.to_list, ["b@example.test", "a@example.test"])
        self.assertEqual(context.cc_list, ["cc@example.test"])
        self.assertEqual(contacts["manager"]["email"], "old@example.test")

    def test_uploaded_workbook_keeps_sparse_rows_and_zero_values(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["车型", None, "保费"])
        sheet.append([None, None, None])
        sheet.append(["家用车", None, 0])
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        uploaded = load_uploaded_file(stream)
        try:
            self.assertEqual(get_sheet_dimensions(uploaded.active), (2, 2))
            self.assertEqual(read_rows(uploaded.active), [
                ["车型", "", "保费"], ["", "", ""], ["家用车", "", "0"],
            ])
        finally:
            uploaded.close()

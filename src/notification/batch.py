"""冻结收件人后执行批量通知；暂停和终止只影响尚未开始的邮件。"""

import hashlib
import threading
import time

from src.attribution.simple_attribution import analyze
from src.notification.email_sender import send_email
from src.notification.email_templates import generate_html_body, generate_subject
from src.notification.recipient_mapper import get_notification_context
from src.notification.address_input import parse_addresses


def unique_alerts(df):
    view = df[df["车型"].ne("合计") & df["预警等级"].isin(["red", "orange", "yellow"])].copy()
    view["_level"] = view["预警等级"].map({"red": 0, "orange": 1, "yellow": 2})
    view = view.sort_values(["_level", "预期成本率"], ascending=[True, False], kind="stable")
    return view.drop_duplicates(["机构", "车型"], keep="first").drop(columns="_level")


def batch_signature(df, threshold):
    rows = unique_alerts(df).to_json(orient="records", force_ascii=False, date_format="iso")
    return hashlib.sha256((str(threshold) + rows).encode()).hexdigest()


def prepare_batch(df, threshold):
    items = []
    for _, row in unique_alerts(df).iterrows():
        context = get_notification_context(row)
        to_list = parse_addresses(";".join(context.to_list))
        to_keys = {address.lower() for address in to_list}
        cc_list = [address for address in parse_addresses(";".join(context.cc_list)) if address.lower() not in to_keys]
        if not to_list:
            raise ValueError(f"{row['机构']} 缺少有效收件人，请先配置联系人")
        items.append({"record": row.to_dict(), "context": context, "to": to_list,
                      "cc": cc_list, "threshold": threshold})
    return items


def recipient_preview(items):
    rows = []
    for item in items:
        record = item["record"]
        rows.append({"机构": record["机构"], "车型": record["车型"], "预警等级": record["预警等级"],
                     "收件人": "; ".join(item["to"]), "抄送": "; ".join(item["cc"])})
    return rows


def send_batch_item(item):
    row = item["record"]
    threshold = item["threshold"]
    result = analyze(row, yellow_threshold=threshold)
    subject = generate_subject(row, threshold=threshold)
    body = generate_html_body(row, item["context"], result.summary, result.suggestions, threshold)
    return send_email(subject, body, item["to"], item["cc"], item["context"])


class BatchJob:
    """一次冻结队列对应一个线程；每条记录最多尝试一次，不自动重试。"""

    def __init__(self, items, batch_size=5, delay=3.0, sender=send_batch_item):
        self.items = list(items)
        self.batch_size = max(1, int(batch_size))
        self.delay = max(0, float(delay))
        self.sender = sender
        self.condition = threading.Condition()
        self.status = "未开始"
        self.index = 0
        self.results = []
        self.cancelled = False
        self.thread = None

    def start(self):
        with self.condition:
            if self.status != "未开始":
                return False
            self.status = "运行中"
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return True

    def pause(self):
        with self.condition:
            if self.status == "运行中":
                self.status = "已暂停"
            self.condition.notify_all()

    def resume(self):
        with self.condition:
            if self.status == "已暂停":
                self.status = "运行中"
            self.condition.notify_all()

    def cancel(self):
        with self.condition:
            self.cancelled = True
            if self.status not in ("已完成", "已终止"):
                self.status = "正在终止"
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {"状态": self.status, "总数": len(self.items), "已开始": self.index,
                    "结果": [dict(row) for row in self.results]}

    def _run(self):
        next_time = 0
        while True:
            with self.condition:
                while not self.cancelled and (self.status == "已暂停" or time.monotonic() < next_time):
                    timeout = max(0, next_time - time.monotonic()) if self.status != "已暂停" else None
                    self.condition.wait(timeout)
                if self.cancelled:
                    self.status = "已终止"
                    return
                if self.index >= len(self.items):
                    self.status = "已完成"
                    return
                item = self.items[self.index]
                # 先占用队列位置，界面重跑也不会再次提交这封邮件。
                self.index += 1
            row = item["record"]
            try:
                sent = self.sender(item)
                outcome = {"机构": row["机构"], "车型": row["车型"], "状态": sent.status,
                           "模式": sent.mode, "错误": sent.error_message}
            except Exception as exc:
                outcome = {"机构": row["机构"], "车型": row["车型"], "状态": "failed", "模式": "未知", "错误": str(exc)}
            with self.condition:
                self.results.append(outcome)
                if self.index % self.batch_size == 0 and self.index < len(self.items):
                    next_time = time.monotonic() + self.delay

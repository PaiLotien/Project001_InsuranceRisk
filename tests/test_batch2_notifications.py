import json
from unittest.mock import patch
import pytest
from src.notification.channels import dispatch
from src.alerts.lifecycle import AlertLifecycle


def config(dry=True):
    return {
        "dry_run": dry,
        "repeat_interval": 60,
        "routes": {"red": ["wecom", "smtp"], "yellow": ["wecom"], "orange": ["wecom"]},
        "channels": {"wecom": {"url": "mock://wecom"}, "smtp": {"url": "mock://smtp"}},
    }


def alerts():
    return [{"branch": "湖北", "batch": "b1", "object": "家用车", "level": v} for v in ["yellow", "orange", "red"]] + [
        {"branch": "湖北", "batch": "b1", "object": "货车", "level": "yellow"}
    ]


def test_dry_run_inhibition_grouping_repeat_persistent(tmp_path):
    with patch("src.notification.channels.apprise.Apprise") as client:
        result = dispatch(alerts(), config=config(), runtime=tmp_path, now=0)
        assert len(result) == 1 and len(result[0]["alerts"]) == 2
        assert result[0]["level"] == "red"
        assert not client.called
        assert dispatch(alerts()[::-1], config=config(), runtime=tmp_path, now=59) == []
        assert len(dispatch(alerts(), config=config(), runtime=tmp_path, now=60)) == 1
    assert len((tmp_path / "outbox.jsonl").read_text().splitlines()) == 2


def test_groups_do_not_inhibit_other_batches(tmp_path):
    data = alerts() + [{"branch": "湖北", "batch": "b2", "object": "家用车", "level": "yellow"}]
    assert len(dispatch(data, config=config(), runtime=tmp_path)) == 2


@pytest.mark.parametrize("channel", ["wecom", "dingtalk", "feishu", "smtp"])
def test_apprise_mocked_channels(tmp_path, channel):
    cfg = config(False)
    cfg["routes"]["red"] = [channel]
    cfg["channels"][channel] = {"url": "mock://" + channel}
    with patch("src.notification.channels.apprise.Apprise") as factory:
        factory.return_value.notify.return_value = True
        dispatch(alerts(), config=cfg, runtime=tmp_path)
        factory.return_value.add.assert_called_once_with("mock://" + channel)
        assert factory.return_value.notify.call_count == 1


def test_send_failure_is_retryable(tmp_path):
    with patch("src.notification.channels.apprise.Apprise") as factory:
        factory.return_value.notify.return_value = False
        with pytest.raises(RuntimeError):
            dispatch(alerts(), config=config(False), runtime=tmp_path, now=0)
        factory.return_value.notify.return_value = True
        assert dispatch(alerts(), config=config(False), runtime=tmp_path, now=1)


def test_lifecycle_persistence_and_audit(tmp_path):
    store = AlertLifecycle(tmp_path / "irds.db")
    ident = store.create("张三", 100, now=0)
    assert store.get(ident)["status"] == "新建"
    for status in ["已确认", "处置中", "已关闭"]:
        store.transition(ident, status, "核查说明", now=1)
    record = AlertLifecycle(tmp_path / "irds.db").get(ident)
    assert record["owner"] == "张三" and len(record["records"]) == 4
    assert store.escalate_overdue(now=101) == 0


@pytest.mark.parametrize("status", ["新建", "处置中", "已关闭"])
def test_lifecycle_rejects_invalid_transition(tmp_path, status):
    store = AlertLifecycle(tmp_path / "irds.db")
    ident = store.create("张三", 100)
    with pytest.raises(ValueError):
        store.transition(ident, status, "记录")


def test_lifecycle_overdue_and_required_fields(tmp_path):
    store = AlertLifecycle(tmp_path / "irds.db")
    with pytest.raises(ValueError):
        store.create("", 100)
    ident = store.create("张三", 100)
    assert store.escalate_overdue(now=100) == 0
    assert store.escalate_overdue(now=101) == 1
    assert store.escalate_overdue(now=102) == 0
    assert store.get(ident)["escalated"] == 1
    with pytest.raises(ValueError):
        store.transition(ident, "已确认", "")


def test_inhibition_configuration_and_dry_run_isolation(tmp_path):
    cfg = config()
    cfg["inhibit"] = {"source": "red", "targets": [], "equal": ["branch", "batch", "object"]}
    assert len(dispatch(alerts(), config=cfg, runtime=tmp_path, now=0)[0]["alerts"]) == 4
    cfg["dry_run"] = False
    with patch("src.notification.channels.apprise.Apprise") as factory:
        factory.return_value.notify.return_value = True
        assert dispatch(alerts(), config=cfg, runtime=tmp_path, now=1)
        assert factory.return_value.notify.called


def test_lifecycle_alert_ref_and_escalation_audit(tmp_path):
    store = AlertLifecycle(tmp_path / "irds.db")
    ident = store.create("李四", 100, alert_ref="湖北/b1/家用车/R005", now=0)
    store.escalate_overdue(now=101)
    row = store.get(ident)
    assert row["alert_ref"] == "湖北/b1/家用车/R005"
    assert row["records"][-1]["note"] == "超时升级"
    with pytest.raises(ValueError):
        store.create("李四", float("nan"))

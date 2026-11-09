"""11 月 6 日反馈的解析、快照、质量明细及批量任务回归。"""

from io import BytesIO
from types import SimpleNamespace
import threading
from unittest.mock import patch

import pandas as pd
import pytest
from openpyxl import Workbook

from src.alerts.thresholds import apply_alert_rules, quality_details, validation_summary
from src.attribution.simple_attribution import analyze
from src.monitoring.history import (build_trend, delete_history, demo_history,
                                    import_history_files, load_history, rapid_changes, save_snapshot)
from src.notification.batch import BatchJob, unique_alerts, prepare_batch
from src.parser.t1_t2_parser import parse_t2
from src.parser.upload_data import UploadReadError, read_uploaded_data
from src.validation.cross_report import run_dq002


def t2_workbook():
    book = Workbook()
    ws = book.active
    ws.title = "分车型预期成本率监控表（日月年）"
    ws.append(["归属机构：全辖"])
    ws.append(["取数口径：保批单生成日期(2026-11-06)"])
    headers = {1: "序号", 2: "车型", 3: "保费占比", 7: "签单保费", 11: "成本率1",
               15: "预期赔付率", 19: "预期成本率", 23: "单均保费"}
    for col, name in headers.items():
        ws.cell(3, col, name)
    ws.merge_cells("B3:B4")
    for col in (3, 7, 11, 15, 19, 23):
        ws.merge_cells(start_row=3, start_column=col, end_row=3, end_column=col + 3)
        for j, period in enumerate(("当日", "当月", "月同比", "月环比")):
            ws.cell(4, col + j, period)
    for i, vehicle, share, premium in [(5, "家用车", .6, 100), (6, "企业车", .4, 200)]:
        for col, value in {1: i - 4, 2: vehicle, 3: share, 7: premium / 2, 8: premium,
                           11: 17.6, 15: 66, 19: 77.1, 23: 1800}.items():
            ws.cell(i, col, value)
        ws.cell(i, 3).number_format = "0.0%"
    ws.cell(7, 2, "合计")
    ws.cell(7, 8, 300)
    ws.cell(9, 1, "年度主表")
    for j, name in enumerate(["序号", "车型", "签单保费", "成本率1", "预期赔付率", "预期成本率"], 1):
        ws.cell(10, j, name)
    for j in range(3, 7):
        ws.cell(11, j, "当年")
    for j, value in enumerate([1, "家用车", 999, 20, 70, 90], 1):
        ws.cell(12, j, value)
    ws.cell(14, 1, "车型细分表")
    for j, value in enumerate(["序号", "车型", "能源类别", "保费占比", "签单保费", "成本率1", "预期赔付率", "预期成本率"], 1):
        ws.cell(15, j, value)
    for j in range(4, 9):
        ws.cell(16, j, "当月")
    for j, value in enumerate([1, "家用车", "新能源", 100, 9999, 20, 90, 110], 1):
        ws.cell(17, j, value)
    t7 = book.create_sheet("分机构车险日平台监控表")
    t7.append(["机构", "月签单保费"])
    t7.append([None, None])
    t7.append(["全辖", 300])
    return book


def as_file(book, name="test.xlsx"):
    stream = BytesIO()
    stream.name = name
    book.save(stream)
    stream.seek(0)
    return stream


def csv_file(text, name="history.csv"):
    stream = BytesIO(text.encode("utf-8-sig"))
    stream.name = name
    return stream


def test_merged_t2_all_blocks_keep_periods_and_sources():
    book = t2_workbook()
    result = parse_t2(book.active, book.active.title)
    assert {m.source_block for m in result.metrics} == {1, 2, 3}
    assert any(m.metric_name == "签单保费" and m.time_dimension == "当月" and m.value == 100 for m in result.metrics)
    assert any(m.time_dimension == "当年" and m.value == 999 for m in result.metrics)
    assert any(m.table_role == "细分表" and m.value == 9999 for m in result.metrics)
    inputs, check = run_dq002(book)
    assert inputs.ready
    assert float(inputs.t2.iloc[0]["当月累计签单保费"]) == 300
    assert check.iloc[0]["结果"] == "通过"


def test_core_source_excludes_secondary_blocks_and_recomputes_cost():
    frame = read_uploaded_data(as_file(t2_workbook()))
    assert set(frame["车型"]) == {"家用车", "企业车"}
    assert set(frame["期间"]) == {"当日"}
    assert frame["预期成本率"].tolist() == pytest.approx([83.6, 83.6])
    assert frame["保费占比"].tolist() == [60, 40]
    assert validation_summary(frame).iloc[0]["结果"] == "通过"


def test_conflicting_duplicates_are_rejected_instead_of_overwritten():
    book = Workbook()
    book.active.append(["机构", "车型", "预期成本率"])
    book.active.append(["湖北", "家用车", 90])
    book.active.append(["湖北", "家用车", 100])
    with pytest.raises(UploadReadError, match="冲突"):
        read_uploaded_data(as_file(book))


def test_identical_duplicates_do_not_double_sample_count():
    book = Workbook()
    book.active.append(["机构", "车型", "预期成本率"])
    book.active.append(["湖北", "家用车", 90])
    book.active.append(["湖北", "家用车", 90])
    assert len(read_uploaded_data(as_file(book))) == 1


def test_sheet_named_csv_is_still_an_excel_sheet():
    book = Workbook()
    book.active.title = "CSV"
    book.active.append(["机构", "车型", "预期成本率"])
    book.active.append(["湖北", "家用车", 95])
    assert read_uploaded_data(as_file(book)).iloc[0]["预期成本率"] == 95


def test_monthly_decimal_reconciliation_does_not_add_binary_roundoff():
    book = t2_workbook()
    book.active.cell(5, 8, 100.1)
    book.active.cell(6, 8, 200.2)
    book.active.cell(7, 8, None)
    book.active['H7'] = None
    book.worksheets[1].cell(3, 2, 300.3)
    _, checks = run_dq002(book)
    assert checks.iloc[0]["结果"] == "通过"


def test_history_recomputes_the_same_66_plus_17_6_and_overwrites_key(tmp_path):
    path = tmp_path / "history.csv"
    frame = pd.DataFrame({"机构": ["湖北"], "车型": ["家用车"], "预期赔付率": [66], "成本率1": [17.6],
                          "预期成本率": [77.1], "单均保费_元": [1800], "累计出险频度": [20]})
    save_snapshot(frame, snapshot_date="2026-11-06", path=path)
    save_snapshot(frame, snapshot_date="2026-11-06", path=path)
    history = load_history(path)
    assert len(history) == 1
    assert history.iloc[0]["预期成本率"] == pytest.approx(83.6)
    assert history.iloc[0]["预期赔付率"] == 66
    assert build_trend(history, branch="湖北").iloc[0]["预期成本率"] == pytest.approx(83.6)


def test_direct_attribution_does_not_repeat_the_wrong_manual_cost():
    row = pd.Series({"机构": "湖北", "车型": "家用车", "预期赔付率": 66,
                     "成本率1": 17.6, "预期成本率": 77.1})
    result = analyze(row)
    assert "预期成本率 83.6%" in result.summary


def test_history_import_export_and_deletion(tmp_path):
    path = tmp_path / "history.csv"
    file = csv_file("统计日期,机构,车型,预期赔付率,成本率1\n2026-11-01,湖北,家用车,60,20\n2026-11-02,湖北,家用车,66,20\n")
    assert import_history_files([file], path) == 2
    history = load_history(path)
    exported = csv_file(history.to_csv(index=False), "export.csv")
    second = tmp_path / "second.csv"
    assert import_history_files([exported], second) == 2
    assert load_history(second)["预期成本率"].tolist() == [80, 86]
    assert delete_history("2026-11-01", second) == 1
    assert delete_history(path=second) == 1
    assert load_history(second).empty


def test_invalid_history_import_does_not_partially_write(tmp_path):
    path = tmp_path / "history.csv"
    good = csv_file("统计日期,机构,车型,预期成本率\n2026-11-01,湖北,家用车,90\n")
    bad = csv_file("机构,车型,预期成本率\n湖北,家用车,90\n", "undated.csv")
    with pytest.raises(ValueError, match="缺少日期"):
        import_history_files([good, bad], path)
    assert not path.exists()


def test_history_package_can_contain_several_dated_excel_sheets(tmp_path):
    book = Workbook()
    for i, day in enumerate(("20261101", "20261102")):
        sheet = book.active if i == 0 else book.create_sheet()
        sheet.title = day
        sheet.append(["机构", "车型", "预期赔付率", "成本率1"])
        sheet.append(["湖北", "家用车", 60 + i, 20])
    path = tmp_path / "history.csv"
    assert import_history_files([as_file(book)], path) == 2
    assert load_history(path)["日期"].tolist() == ["2026-11-01", "2026-11-02"]


def test_other_undated_sheets_cannot_override_history_main_table(tmp_path):
    book = Workbook()
    main = book.active
    main.title = "主表"
    main.append(["统计日期", "机构", "车型", "预期成本率"])
    main.append(["2026-11-01", "湖北", "家用车", 90])
    other = book.create_sheet("辅助表")
    other.append(["统计日期", "机构", "车型", "预期成本率"])
    other.append(["2026-11-01", "湖北", "家用车", 100])
    path = tmp_path / "history.csv"
    assert import_history_files([as_file(book)], path) == 1
    assert load_history(path).iloc[0]["预期成本率"] == 90


def test_demo_history_and_rapid_signal_are_explicitly_synthetic():
    frame = pd.DataFrame({"机构": ["湖北"], "车型": ["家用车"], "预期赔付率": [60], "成本率1": [20]})
    history = demo_history(frame)
    assert history["日期"].nunique() == 7
    assert set(history["来源"]) == {"内置合成演示历史"}
    trend = pd.DataFrame({"日期": ["2026-11-01", "2026-11-02"], "预期成本率": [80, 86]})
    assert rapid_changes(trend, "预期成本率", threshold=5).iloc[0]["结果"] == "提示快速变化"


def test_quality_failure_lists_exact_contributing_rows():
    frame = pd.DataFrame({"机构": ["湖北"] * 3, "车型": ["家用车", "企业车", "家用车_细分"],
                          "保费占比": [60, 30, 50], "表类型": ["主表", "主表", "细分表"]})
    checks = validation_summary(frame)
    details = quality_details(frame, checks)
    assert checks.iloc[0]["实际值"] == 90
    assert details["车型"].tolist() == ["家用车", "企业车"]
    assert set(details["距100%差额"]) == {-10}


def test_tri_factor_reference_thresholds_are_adjustable():
    row = pd.Series({"车型": "家用车", "预期成本率": 95, "预期赔付率": 70, "成本率1": 25,
                     "累计出险频度": 30, "单均保费_元": 1800})
    assert analyze(row).tri_factor_analysis
    assert not analyze(row, frequency_threshold=40, premium_threshold=1000).tri_factor_analysis


def test_batch_queue_deduplicates_branch_and_vehicle():
    frame = pd.DataFrame({"机构": ["湖北"] * 3, "车型": ["家用车", "家用车", "企业车"],
                          "预期成本率": [95, 105, 96], "预警等级": ["yellow", "red", "yellow"]})
    queue = unique_alerts(frame)
    assert len(queue) == 2
    assert queue.iloc[0]["预期成本率"] == 105


def test_batch_addresses_are_deduplicated_across_roles_and_cc():
    context = SimpleNamespace(to_list=["one@example.test", "ONE@example.test"], cc_list=["one@example.test", "cc@example.test"])
    frame = pd.DataFrame({"机构": ["湖北"], "车型": ["家用车"], "预期成本率": [95], "预警等级": ["yellow"]})
    with patch("src.notification.batch.get_notification_context", return_value=context):
        item = prepare_batch(frame, 90)[0]
    assert item["to"] == ["one@example.test"]
    assert item["cc"] == ["cc@example.test"]


def test_threshold_config_is_loaded_once_for_a_frame():
    with patch("src.alerts.thresholds.configured_thresholds", return_value={"yellow": 90, "orange": 100, "red": 100}) as config:
        result = apply_alert_rules(pd.DataFrame({"预期成本率": [95] * 1000}))
    assert config.call_count == 1
    assert result["预警等级"].eq("yellow").all()


def test_nullable_rate_values_do_not_break_batch_classification():
    frame = pd.DataFrame({"预期成本率": pd.Series([pd.NA, 95], dtype="Float64")})
    assert apply_alert_rules(frame)["预警等级"].tolist() == ["normal", "yellow"]


def test_batch_pause_resume_and_double_start_never_duplicate():
    started = threading.Event()
    release = threading.Event()
    second = threading.Event()
    calls = []
    items = [{"record": {"机构": "湖北", "车型": name}} for name in ("家用车", "企业车")]

    def sender(item):
        calls.append(item["record"]["车型"])
        if len(calls) == 1:
            started.set()
            release.wait(2)
        else:
            second.set()
        return SimpleNamespace(status="success", mode="mock", error_message="")

    job = BatchJob(items, delay=0, sender=sender)
    try:
        assert job.start()
        assert not job.start()
        assert started.wait(1)
        job.pause()
        release.set()
        assert not second.wait(.05)
        job.resume()
        assert second.wait(1)
        job.thread.join(1)
        assert calls == ["家用车", "企业车"]
        assert job.snapshot()["状态"] == "已完成"
    finally:
        job.cancel()
        release.set()
        job.thread.join(2)


def test_batch_cancel_does_not_start_next_email():
    started = threading.Event()
    release = threading.Event()
    calls = []

    def sender(item):
        calls.append(item)
        started.set()
        release.wait(2)
        return SimpleNamespace(status="success", mode="mock", error_message="")

    items = [{"record": {"机构": "湖北", "车型": str(i)}} for i in range(3)]
    job = BatchJob(items, sender=sender)
    try:
        job.start()
        assert started.wait(1)
        job.cancel()
        release.set()
        job.thread.join(1)
        assert len(calls) == 1
        assert job.snapshot()["状态"] == "已终止"
    finally:
        release.set()
        job.cancel()
        job.thread.join(2)

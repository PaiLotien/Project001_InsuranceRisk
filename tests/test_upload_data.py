"""上传文件读取、跨 Sheet 合并和错误诊断检查。"""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from streamlit.runtime.uploaded_file_manager import UploadedFile, UploadedFileRec
from streamlit.proto.Common_pb2 import FileURLs

from src.parser.upload_data import read_uploaded_data


def excel_file(tables):
    stream = BytesIO()
    stream.name = "test.xlsx"
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        for sheet, frame in tables:
            frame.to_excel(writer, sheet_name=sheet, index=False)
    stream.seek(0)
    return stream


def test_only_selected_main_sheet_is_used():
    stream = excel_file([
        ("说明", pd.DataFrame({"备注": ["演示"]})),
        ("一", pd.DataFrame({"车型": ["家用车"], "预期成本率": [95]})),
        ("二", pd.DataFrame({"车型": ["企业车"], "预期成本率": [100]})),
    ])
    result = read_uploaded_data(stream)
    assert result["车型"].tolist() == ["家用车"]
    assert result["来源Sheet"].tolist() == ["一"]
    assert result["数据校验状态"].tolist() == ["通过"]
    selected = read_uploaded_data(stream, "二")
    assert selected["车型"].tolist() == ["企业车"]


def test_non_detail_sheet_keeps_validation_problem():
    result = read_uploaded_data(excel_file([("说明", pd.DataFrame({"备注": ["演示"]}))]))
    assert result.iloc[0]["数据校验状态"] == "待核查"
    assert "缺少必需字段：车型" in result.iloc[0]["数据校验信息"]


def test_csv_upload_uses_the_same_validation():
    stream = BytesIO("车型,预期成本率\n家用车,95\n".encode("utf-8-sig"))
    stream.name = "sample.csv"
    result = read_uploaded_data(stream)
    assert result.iloc[0]["数据校验状态"] == "通过"
    assert result.iloc[0]["来源Sheet"] == "sample"


def test_broken_excel_has_readable_error_and_original_cause():
    stream = BytesIO(b"not an excel file")
    stream.name = "broken.xlsx"
    with pytest.raises(ValueError, match="无法读取文件") as caught:
        read_uploaded_data(stream)
    assert caught.value.__cause__ is not None


def test_internal_validation_error_is_not_swallowed():
    stream = excel_file([("一", pd.DataFrame({"车型": ["家用车"], "预期成本率": [95]}))])
    with patch("src.parser.upload_data.validate_core_frame", side_effect=RuntimeError("validation bug")):
        with pytest.raises(RuntimeError, match="validation bug"):
            read_uploaded_data(stream)


def test_upload_error_is_visible_in_sidebar():
    path = Path(__file__).resolve().parents[1] / "src/dashboard/streamlit_app.py"
    app = AppTest.from_file(path, default_timeout=30).run()
    record = UploadedFileRec("broken-ui-r3", "broken_ui.xlsx", "application/octet-stream", b"broken file for ui test")
    stream = UploadedFile(record, FileURLs())
    with patch("streamlit.delta_generator.DeltaGenerator.file_uploader", return_value=stream):
        app.sidebar.radio[1].set_value("上传机构数据").run()
    assert not app.exception
    assert any("无法读取文件" in error.value for error in app.sidebar.error)


def test_internal_upload_error_is_logged_and_visible(caplog):
    path = Path(__file__).resolve().parents[1] / "src/dashboard/streamlit_app.py"
    app = AppTest.from_file(path, default_timeout=30).run()
    data = excel_file([("一", pd.DataFrame({"车型": ["家用车"], "预期成本率": [95]}))]).getvalue()
    record = UploadedFileRec("internal-ui-r3", "internal_ui.xlsx", "application/octet-stream", data)
    uploaded = UploadedFile(record, FileURLs())
    with patch("streamlit.delta_generator.DeltaGenerator.file_uploader", return_value=uploaded):
        with patch("src.parser.upload_data.validate_core_frame", side_effect=ValueError("internal validation bug")):
            app.sidebar.radio[1].set_value("上传机构数据").run()
    assert not app.exception
    assert any("数据处理异常" in error.value for error in app.sidebar.error)
    assert any(record.exc_info and "内部异常" in record.message for record in caplog.records)

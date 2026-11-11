"""一个文件只选一个主数据源，不把辅助 Sheet 叠加到核心数据中。"""

from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import openpyxl
import pandas as pd
from src.parser.excel_loader import excel_engine, read_excel
from src.validation.schema import validate_frame, top3
try:
    from python_calamine import CalamineError
except ImportError:
    class CalamineError(ValueError):
        pass
from openpyxl.utils.exceptions import InvalidFileException

from src.alerts.thresholds import apply_alert_rules
from src.parser.data_source import unique_records
from src.parser.table_blocks import read_table_blocks
from src.validation.data_contract import annotate_validation, validate_core_frame


class UploadReadError(ValueError):
    """输入文件无法读取或含有需要用户处理的数据冲突。"""


def _input(uploaded_file):
    if hasattr(uploaded_file, "getvalue"):
        return BytesIO(uploaded_file.getvalue())
    return uploaded_file


def list_upload_sheets(uploaded_file) -> list[str]:
    if Path(str(getattr(uploaded_file, "name", ""))).suffix.lower() == ".csv":
        return ["CSV"]
    try:
        with pd.ExcelFile(_input(uploaded_file), engine=excel_engine()) as workbook:
            names = list(workbook.sheet_names)
    except (OSError, ValueError, BadZipFile, InvalidFileException, ParseError, CalamineError) as exc:
        raise UploadReadError(f"无法读取文件，请检查格式和内容：{exc}") from exc
    # 明确命名的主表优先；仍允许用户在侧栏切换，而不是静默覆盖。
    def priority(name):
        if name in ("完整数据集", "主表", "主数据", "main"):
            return 0
        if "分车型" in name and "日" in name:
            return 1
        if any(word in name for word in ("细分", "子表", "说明", "detail")):
            return 3
        return 2
    return sorted(names, key=priority)


def read_uploaded_data(uploaded_file, source_sheet: str | None = None) -> pd.DataFrame:
    names = list_upload_sheets(uploaded_file)
    if not names:
        raise UploadReadError("文件中没有可读取的表格")
    source_sheet = source_sheet or names[0]
    if source_sheet not in names:
        raise UploadReadError("选定的主数据源不在当前文件中")
    csv = Path(str(getattr(uploaded_file, "name", ""))).suffix.lower() == ".csv"
    blocks = []
    try:
        if csv:
            frame = pd.read_csv(_input(uploaded_file), encoding="utf-8-sig")
        else:
            workbook = openpyxl.load_workbook(_input(uploaded_file), data_only=True)
            try:
                blocks = read_table_blocks(workbook[source_sheet])
            finally:
                workbook.close()
            if not blocks:
                frame = read_excel(_input(uploaded_file), sheet_name=source_sheet)
    except (OSError, ValueError, BadZipFile, InvalidFileException, ParseError, CalamineError) as exc:
        raise UploadReadError(f"无法读取文件，请检查格式和内容：{exc}") from exc

    ignored_blocks = 0
    if blocks:
        selected = []
        available = []
        for block in blocks:
            if block.empty:
                continue
            if block.iloc[0]["表类型"] != "主表":
                ignored_blocks += 1
                continue
            selected.append(block)
            available.extend(block["期间"].unique())
        if not selected:
            raise UploadReadError("选定 Sheet 中没有可用于核心分析的主表")
        period = next((p for p in ("当日", "当月", "当年") if p in available), None)
        if period is None:
            raise UploadReadError("主表缺少当日、当月或当年绝对值，不能用同比值计算成本率")
        frame = pd.concat(selected, ignore_index=True)
        all_periods = frame.to_dict("records")
        frame = frame[frame["期间"].eq(period)].copy()

    if "来源Sheet" not in frame.columns:
        frame["来源Sheet"] = source_sheet if not csv else Path(str(getattr(uploaded_file, "name", "CSV"))).stem
    try:
        frame = unique_records(frame)
    except ValueError as exc:
        raise UploadReadError(str(exc)) from exc
    smart_issues = validate_frame(frame, source_sheet)
    frame = apply_alert_rules(frame)
    frame = annotate_validation(frame, validate_core_frame(frame))
    frame.attrs["smart_validation"] = smart_issues.to_dict("records")
    frame.attrs["quality_top3"] = top3(smart_issues)
    frame.attrs["all_periods"] = all_periods if blocks else frame.to_dict("records")
    frame.attrs["unit_warnings"] = list(dict.fromkeys(w for b in blocks for w in b.attrs.get("unit_warnings", [])))
    frame.attrs["blocks"] = [{"区块": i+1, "行数": len(b[[c for c in ("来源行",) if c in b]].drop_duplicates()), "状态": "参与分析" if not b.empty and b.iloc[0]["表类型"] == "主表" else "排除：细分口径，避免重复计数"} for i,b in enumerate(blocks)]
    frame.attrs["source_info"] = {"主数据源": source_sheet, "忽略其他Sheet数": len(names) - 1,
                                  "识别区块数": len(blocks), "排除细分区块数": ignored_blocks}
    return frame

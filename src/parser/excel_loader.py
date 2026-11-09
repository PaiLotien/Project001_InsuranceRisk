"""Excel文件加载器"""

from pathlib import Path

import openpyxl


def load_workbook(file_path: str | Path) -> openpyxl.Workbook:
    """读取 xlsx/xlsm，文件不存在或格式不支持时抛出异常。"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = path.suffix.lower()
    if ext not in (".xlsx", ".xlsm"):
        raise ValueError(f"不支持的文件格式: {ext}，请使用 .xlsx 文件")

    return openpyxl.load_workbook(path, data_only=True, read_only=False)


def load_uploaded_file(uploaded_file) -> openpyxl.Workbook:
    """读取上传组件提供的文件流。"""
    return openpyxl.load_workbook(uploaded_file, data_only=True, read_only=False)


def get_sheet_names(wb: openpyxl.Workbook) -> list[str]:
    """获取工作簿中所有Sheet名称"""
    return wb.sheetnames


def get_sheet_dimensions(ws) -> tuple[int, int]:
    """返回至少含一个非空单元格的行数、列数。"""
    if ws.max_row == 0 or ws.max_column == 0:
        return 0, 0

    # 模板格式可能一直延伸到 XFD，只检查已存在且有值的单元格。
    rows = set()
    columns = set()
    for cell in list(ws._cells.values()):
        if cell.value is not None:
            rows.add(cell.row)
            columns.add(cell.column)
    return len(rows), len(columns)


def read_rows(ws, max_rows: int = 100) -> list[list]:
    """读取前 max_rows 行，空单元格转成空字符串。"""
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            break
        values = []
        for cell in row:
            if cell is None:
                values.append("")
            else:
                values.append(str(cell))
        rows.append(values)
    return rows

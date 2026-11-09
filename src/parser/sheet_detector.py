"""Sheet 类型自动识别器

通过关键字匹配，将上传Excel中的每个Sheet映射到对应的报表类型（T1-T9）。
"""

from pathlib import Path
from typing import Optional

import yaml

from src.core.models import ReportType, SheetMeta


# 缓存加载的映射配置
_mapping_cache: Optional[list[dict]] = None


def _load_mappings() -> list[dict]:
    """加载 sheet_mapping.yaml 配置"""
    global _mapping_cache
    if _mapping_cache is not None:
        return _mapping_cache

    config_path = Path(__file__).parent.parent.parent / "config" / "sheet_mapping.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            _mapping_cache = data.get("mappings", [])
    else:
        _mapping_cache = []

    return _mapping_cache


def detect_report_type(sheet_name: str) -> tuple[ReportType, str]:
    """按 Sheet 名称返回报表类型和匹配说明。"""
    # 先尝试精确匹配
    exact_matches = {
        "分车型预期成本率监控表（日）": ReportType.T1,
        "分车型预期成本率监控表(日)": ReportType.T1,
        "分车型预期成本率监控表（日月年）": ReportType.T2,
        "分车型预期成本率监控表(日月年)": ReportType.T2,
        "全辖满期出险频度进展监测表": ReportType.T3,
    }

    for key, rt in exact_matches.items():
        if key in sheet_name:
            return rt, f"精确匹配 → {rt.value}"

    # 关键字模糊匹配
    mappings = _load_mappings()
    candidates = []  # (report_type, priority, description)

    for m in mappings:
        for kw in m.get("keywords", []):
            if kw in sheet_name:
                candidates.append((
                    m["report_type"],
                    m.get("priority", 5),
                    m.get("description", ""),
                ))

    if candidates:
        # 按优先级排序（priority越小越优先）
        candidates.sort(key=lambda x: x[1])
        rt_str = candidates[0][0]
        desc = candidates[0][2]
        try:
            rt = ReportType(rt_str)
            return rt, f"关键字匹配 → {rt.value} ({desc})"
        except ValueError:
            return ReportType.UNKNOWN, f"未识别的报表类型: {rt_str}"

    return ReportType.UNKNOWN, "未能匹配到已知报表类型"


def analyze_sheet(ws, sheet_name: str) -> SheetMeta:
    """读取实际行列数、报表类型和前十行的口径说明。"""
    from src.parser.excel_loader import get_sheet_dimensions

    actual_rows, actual_cols = get_sheet_dimensions(ws)

    report_type, parse_note = detect_report_type(sheet_name)

    # 提取元数据（取数口径、单位等）
    metadata: dict[str, str] = {}
    first_rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= 10:
            break
        vals = [str(c).strip() for c in row if c is not None]
        first_rows.append(" ".join(vals)[:120])

    for row_text in first_rows:
        if "取数口径" in row_text:
            metadata["取数口径"] = row_text
        if "保批单生成日期" in row_text:
            metadata["取数口径"] = "保批单生成日期"
        if "保险起期" in row_text:
            metadata["取数口径"] = "保险起期"
        if "签单保费单位" in row_text or "保费单位" in row_text:
            metadata["保费单位"] = row_text
        if "单均保费单位" in row_text:
            metadata["单均保费单位"] = row_text
        if "归属机构" in row_text:
            metadata["归属机构"] = row_text
        if "更新频度" in row_text:
            metadata["更新频度"] = row_text
        if "统计条件" in row_text:
            metadata["统计条件"] = row_text
        if "剔除短期单" in row_text:
            metadata["统计条件"] = "剔除短期单"
        if "注：统计数据均为累进值" in row_text:
            metadata["数据特性"] = "累进值"
        if "比去年同期高的红色突出显示" in row_text:
            metadata["预警规则"] = "2025年数值 > 2024年同期 → 红色"

    is_parseable = report_type in (ReportType.T1, ReportType.T2)

    return SheetMeta(
        sheet_name=sheet_name,
        report_type=report_type,
        row_count=actual_rows,
        col_count=actual_cols,
        is_parseable=is_parseable,
        parse_note=parse_note if report_type != ReportType.UNKNOWN
        else "暂不支持深度解析，仅展示结构信息",
        metadata=metadata,
    )


def analyze_all_sheets(wb) -> list[SheetMeta]:
    """按工作簿顺序分析各 Sheet。"""
    results = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        meta = analyze_sheet(ws, sheet_name)
        results.append(meta)
    return results

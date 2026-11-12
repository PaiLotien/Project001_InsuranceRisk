"""按重复表头拆分监控表，保留主表、细分表和时间口径。"""

import re

import pandas as pd

from src.utils.helpers import safe_float, safe_str


METRICS = {
    "保费占比": "保费占比", "签单保费": "签单保费_万元", "成本率1": "成本率1",
    "预期赔付率": "预期赔付率", "预期成本率": "预期成本率", "单均保费": "单均保费_元",
    "累计出险频度": "累计出险频度", "出险频度": "累计出险频度",
    "自主定价系数": "自主定价系数", "自主系数": "自主定价系数", "案均赔款": "案均赔款", "满期保费": "满期保费", "满期进度": "满期进度",
}
PERIODS = ("当日", "当月", "当年", "月同比", "月环比", "同期", "同比", "同比率")


def read_table_blocks(ws) -> list[pd.DataFrame]:
    rows = list(ws.iter_rows(values_only=True))
    headers = []
    for i, row in enumerate(rows):
        names = [safe_str(value).replace("\n", "") for value in row]
        if "车型" in names and any(any(key in name for key in METRICS) for name in names):
            headers.append(i)

    merged = {}
    for area in ws.merged_cells.ranges:
        value = ws.cell(area.min_row, area.min_col).value
        if value is None:
            continue
        for r in range(area.min_row, area.max_row + 1):
            for c in range(area.min_col, area.max_col + 1):
                merged[r, c] = value

    blocks = []
    for block_no, header in enumerate(headers):
        end = headers[block_no + 1] if block_no + 1 < len(headers) else len(rows)
        names = [safe_str(v).replace("\n", "") for v in rows[header]]
        vehicle_col = names.index("车型")
        branch_col = names.index("机构") if "机构" in names else None
        energy_col = next((j for j, name in enumerate(names) if "能源" in name), None)
        context = " ".join(safe_str(v) for row in rows[:header] for v in row[:3] if v is not None)
        nearby = " ".join(safe_str(v) for row in rows[max(0, header - 3):header] for v in row[:3])
        branch_match = re.findall(r"归属机构[：:]\s*([^\s()（）]+)", context)
        branch = branch_match[-1] if branch_match else "全辖"
        date_match = re.findall(r"(20\d{2})[.\-/年](\d{1,2})[.\-/月](\d{1,2})", context)
        day = ""
        if date_match:
            y, m, d = date_match[-1]
            day = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        role = "主表"
        if (energy_col is not None and len(headers) > 1) or "细化" in nearby or "细分" in nearby or "子表" in nearby:
            role = "细分表"

        second = rows[header + 1] if header + 1 < len(rows) else []
        double_header = any(safe_str(v) in PERIODS for v in second)
        specs = []
        current_metric = None
        for j in range(len(names)):
            # 只向合并区域内取表头，不将前一指标无限填充到下一块。
            top = safe_str(merged.get((header + 1, j + 1), rows[header][j])).replace("\n", "")
            metric = next((value for key, value in METRICS.items() if key in top), None)
            if top.startswith("原表"):
                metric = None
            if metric is not None:
                current_metric = metric
            elif top:
                current_metric = None
            elif double_header:
                metric = current_metric
            if not metric:
                continue
            period = "当日"
            if double_header:
                period = safe_str(merged.get((header + 2, j + 1), second[j] if j < len(second) else ""))
                if period not in PERIODS:
                    continue
            specs.append((j, metric, period))

        records = []
        start = header + 2 if double_header else header + 1
        for i in range(start, end):
            raw = rows[i]
            if any(a.min_row == i + 1 and a.max_col - a.min_col >= 2 for a in ws.merged_cells.ranges):
                continue
            vehicle = safe_str(merged.get((i + 1, vehicle_col + 1), raw[vehicle_col]))
            if not vehicle or vehicle == "车型":
                continue
            values = {}
            for j, metric, period in specs:
                value = safe_float(raw[j]) if j < len(raw) else None
                if value is None:
                    continue
                if "%" in ws.cell(i + 1, j + 1).number_format and metric not in ("签单保费_万元", "单均保费_元", "案均赔款"):
                    value *= 100
                unit_context = safe_str(merged.get((header + 1, j + 1), rows[header][j]))
                unit_context += " " + context
                if period not in ("月同比", "月环比", "同比", "同比率"):
                    if metric in ("签单保费_万元", "满期保费") and re.search(r"(?:签单保费|满期保费)(?:单位)?[：:（( ]*元", unit_context): value /= 10000
                    if metric in ("单均保费_元", "案均赔款") and re.search(r"(?:单均保费|案均赔款)(?:单位)?[：:（( ]*万元", unit_context): value *= 10000
                values.setdefault(period, {})[metric] = value
            serial = raw[names.index("序号")] if "序号" in names else None
            if not values and safe_float(serial) is None:
                continue
            # 空模板仍保留车型行；有多种时间列时分别保留，不混成一条记录。
            if not values:
                period = next((p for p in ("当日", "当月", "当年") if any(s[2] == p for s in specs)), "当日")
                values[period] = {}
            for period, metrics in values.items():
                record = {"机构": safe_str(raw[branch_col]) if branch_col is not None else branch,
                          "车型": vehicle, "期间": period, "表类型": role,
                          "数据区块": block_no + 1, "来源行": i + 1, "来源Sheet": ws.title}
                if day:
                    record["统计日期"] = day
                for label in ("统计日期", "日期"):
                    if label in names:
                        record["统计日期"] = raw[names.index(label)]
                if energy_col is not None:
                    record["能源类型"] = safe_str(merged.get((i + 1, energy_col + 1), raw[energy_col]))
                record.update(metrics)
                records.append(record)
        frame = pd.DataFrame(records)
        frame.attrs["header_row"] = header + 1
        warnings = []
        for j, metric, period in specs:
            if metric not in ('预期赔付率','成本率1','预期成本率','保费占比','累计出险频度'): continue
            numbers = [safe_float(rows[r][j]) for r in range(start,end) if j < len(rows[r])]
            numbers = [v for v in numbers if v is not None]
            if numbers and max(abs(v) for v in numbers) <= 1.5 and not any('%' in ws.cell(r+1,j+1).number_format for r in range(start,end)):
                warnings.append(f"{metric}（{period}）为无百分号小数，请确认是否需要乘100；当前保留原值，避免误判单位。")
        frame.attrs['unit_warnings'] = warnings
        blocks.append(frame)
    return blocks

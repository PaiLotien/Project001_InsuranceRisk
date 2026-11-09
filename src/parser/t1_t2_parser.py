"""读取 T1/T2 的所有区块；指标保留来源区块、行号和时间维度。"""

import pandas as pd

from src.core.models import MetricCell, ParsedReport, ReportType
from src.parser.table_blocks import METRICS, read_table_blocks


def _parse(ws, sheet_name, report_type):
    report = ParsedReport(report_type=report_type, sheet_name=sheet_name)
    vehicles = []
    for frame in read_table_blocks(ws):
        for _, row in frame.iterrows():
            vehicle = row["车型"]
            if vehicle not in vehicles:
                vehicles.append(vehicle)
            report.branch = row["机构"]
            for metric in dict.fromkeys(METRICS.values()):
                if metric not in row or pd.isna(row[metric]):
                    continue
                name = {"签单保费_万元": "签单保费", "单均保费_元": "单均保费"}.get(metric, metric)
                report.metrics.append(MetricCell(
                    vehicle_type=vehicle, metric_name=name, value=float(row[metric]),
                    time_dimension=row["期间"], branch=row["机构"],
                    dimension_1=row.get("能源类型", ""), source_block=int(row["数据区块"]),
                    source_row=int(row["来源行"]), table_role=row["表类型"],
                ))
    report.vehicle_types = vehicles
    report.row_count = len(vehicles)
    return report


def parse_t1(ws, sheet_name: str) -> ParsedReport:
    return _parse(ws, sheet_name, ReportType.T1)


def parse_t2(ws, sheet_name: str) -> ParsedReport:
    return _parse(ws, sheet_name, ReportType.T2)

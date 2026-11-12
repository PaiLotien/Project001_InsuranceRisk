"""可离线审阅的 Excel 与打印报告。"""
from io import BytesIO
from pathlib import Path
from html import escape
import pandas as pd
import yaml
from src.alerts.thresholds import load_alert_config, configured_thresholds

LABELS = {'normal':'正常','yellow':'黄色','orange':'橙色','red':'红色'}
MISSING_DISPLAY = "—"
_MISSING_TOKENS = {"none", "nan", "nat", "<na>", "null"}


def _is_missing_token(value):
    if value is None or value is pd.NA:
        return True
    try:
        if pd.isna(value):
            return True
    except (ValueError, TypeError):
        return False
    return isinstance(value, str) and value.strip().lower() in _MISSING_TOKENS


def mask_missing(frame):
    """把 None / NaN / 字面 'None' 'nan' 等缺失标记统一成空值，供表格展示。"""
    if frame is None or not isinstance(frame, pd.DataFrame):
        return frame
    out = frame.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(lambda v: pd.NA if _is_missing_token(v) else v)
    return out


def display_frame(frame):
    out = frame.copy()
    for col in ('预警等级', '等级'):
        if col in out: out[col] = out[col].replace(LABELS)
    if "severity" in out: out["severity"] = out["severity"].replace({"error":"错误","warning":"提示"})
    return mask_missing(out.rename(columns={"sheet":"工作表","row":"来源行","column":"字段","rule_id":"规则编号","severity":"严重程度","message":"说明"}))


def present_frame(frame):
    """屏幕表格用副本：缺失值写成 —，无缺失的数值列保持原类型。"""
    view = mask_missing(display_frame(frame))
    out = view.copy()
    for col in out.columns:
        if not view[col].isna().any():
            continue
        out[col] = [MISSING_DISPLAY if pd.isna(v) else v for v in view[col].tolist()]
    return out

def metric_explanations():
    root = Path(__file__).resolve().parents[2]
    metrics = yaml.safe_load((root/'config/metric_definitions.yaml').read_text())['metrics']
    rules = load_alert_config()['rules']; thresholds = configured_thresholds()
    return pd.DataFrame([{'指标':m['name'],'公式或口径':m.get('formula', m.get('description','—')),'单位':m.get('unit') or '—',
        '阈值及作用':'；'.join(f"{r['id']}：{LABELS.get(r['level'],r['level'])}，严格大于{thresholds.get(r['level'],r.get('threshold'))}%" if r['metric']=='预期成本率' else f"{r['id']}：{r.get('description','')}" for r in rules if r.get('enabled',True) and r['metric']==m['name']) or '用于经营分析及规则前置判断',
        '来源':'指标定义配置、预警规则配置'} for m in metrics])

def report_sheets(df, alerts=None, checks=None):
    return {'概览':pd.DataFrame([{'明细行数':len(df),'预警行数':int(df['预警等级'].isin(['yellow','orange','red']).sum()),'说明':'红色严格大于阈值；橙色默认95%，待业务确认'}]),
        '预警明细':display_frame(df[df['预警等级'].ne('normal')] if alerts is None else alerts),
        '触发规则':display_frame(pd.DataFrame(df.attrs.get('rule_evidence',[]))),
        '数据质量':pd.DataFrame() if checks is None else checks,'阈值说明':metric_explanations()}

def build_report_xlsx(df, alerts=None, checks=None):
    stream = BytesIO()
    with pd.ExcelWriter(stream, engine='openpyxl') as writer:
        for name, frame in report_sheets(df, alerts, checks).items():
            frame.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]; ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
            for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=24
    return stream.getvalue()

def build_report_html(df, alerts=None, checks=None):
    sections=''.join(f'<h2>{escape(name)}</h2>{frame.to_html(index=False,escape=True,na_rep=MISSING_DISPLAY)}' for name,frame in report_sheets(df,alerts,checks).items())
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>车险风控报告</title><style>body{font-family:sans-serif}table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #aaa;padding:6px}@media print{button{display:none}thead{display:table-header-group}tr{break-inside:avoid}h2{break-after:avoid}@page{size:A4 landscape;margin:12mm}</style><button onclick="window.print()">打印报告</button><h1>车险风控报告</h1>'+sections+'</html>'

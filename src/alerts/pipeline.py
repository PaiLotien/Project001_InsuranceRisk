"""页面统一预警入口：保留期间与区块，合并业务规则及阈值结果。"""
import pandas as pd
from src.alerts.thresholds import apply_alert_rules, configured_thresholds
from src.core.models import MetricCell, AlertLevel
from src.rules.rule_engine import run_all_rules, load_alert_rules
from src.utils.helpers import detail_frame

LEVELS = {AlertLevel.YELLOW: 'yellow', AlertLevel.ORANGE: 'orange', AlertLevel.RED: 'red'}
ORDER = {'normal': 0, 'yellow': 1, 'orange': 2, 'red': 3}

def evaluate_frame(frame, yellow_threshold=None):
    out = detail_frame(apply_alert_rules(frame, yellow_threshold))
    source = detail_frame(pd.DataFrame(frame.attrs.get('all_periods', frame.to_dict('records'))))
    cells = []
    aliases = {'签单保费_万元': '签单保费', '单均保费_元': '单均保费', '累计出险频度': '出险频度'}
    metrics = {'预期成本率','预期赔付率','成本率1','单均保费','出险频度','案均赔款','满期保费','满期进度','签单保费'}
    for _, row in source.iterrows():
        if row.get('表类型', '主表') != '主表': continue
        for col, value in row.items():
            period = str(row.get('期间', '当日'))
            name = aliases.get(col, col)
            for suffix in ('月同比', '月环比'):
                if col.endswith('_'+suffix):
                    name = aliases.get(col[:-len(suffix)-1], col[:-len(suffix)-1]); period = suffix
            if name not in metrics: continue
            value = pd.to_numeric(value, errors='coerce')
            if pd.isna(value): continue
            cells.append(MetricCell(str(row.get('车型','')), name, float(value), period,
                str(row.get('机构','全辖')), str(row.get('能源类型','')), str(row.get('产品组合','')),
                int(row.get('数据区块',0)), table_role='主表'))
    rules = load_alert_rules()
    thresholds = configured_thresholds()
    if yellow_threshold is not None: thresholds['yellow'] = yellow_threshold
    for rule in rules:
        if rule['id'] in ('R005','R010','R011'): rule['threshold'] = thresholds[rule['level']]
    alerts = run_all_rules(cells, rules)
    out['触发规则'] = ''
    out['规则类型'] = ''
    evidence = []
    for alert in alerts:
        mask = out['机构'].eq(alert.branch) & out['车型'].eq(alert.vehicle_type)
        if '数据区块' in out: mask &= out['数据区块'].eq(alert.source_block)
        if '能源类型' in out: mask &= out['能源类型'].fillna('').eq(alert.dimension_1)
        for idx in out.index[mask]:
            old = out.at[idx,'预警等级']; level = LEVELS[alert.alert_level]
            if ORDER[level] > ORDER.get(old,0): out.at[idx,'预警等级'] = level
            ids = set(filter(None, out.at[idx,'触发规则'].split('、'))); ids.add(alert.rule_id)
            if '组合条件成立' in alert.trigger_reason: ids.add('C001')
            out.at[idx,'触发规则'] = '、'.join(sorted(ids))
            out.at[idx,'规则类型'] = '、'.join(sorted({'环比' if x=='R006' else '同比' if x in ('R007','R008') else '组合' if x=='C001' else '阈值' for x in ids}))
        evidence.append({'机构':alert.branch,'车型':alert.vehicle_type,'规则':alert.rule_id,'期间':alert.time_dimension,'数据区块':alert.source_block,'说明':alert.trigger_reason,'等级':LEVELS[alert.alert_level]})
    out.attrs['rule_evidence'] = evidence
    return out

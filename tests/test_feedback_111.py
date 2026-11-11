"""11.1 反馈回归；全部样表为空模板或合成填数，通知不发送。"""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import openpyxl
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from streamlit.runtime.uploaded_file_manager import UploadedFile, UploadedFileRec
from streamlit.proto.Common_pb2 import FileURLs
from src.utils.helpers import is_total_row
from src.alerts.thresholds import apply_alert_rules, validation_summary
from src.alerts.pipeline import evaluate_frame
from src.parser.upload_data import read_uploaded_data
from src.parser.table_blocks import read_table_blocks
from src.rules.rule_engine import evaluate_rule, load_alert_rules
from src.core.models import MetricCell
from src.config.threshold_store import save_user_thresholds, load_user_thresholds, reset_thresholds
from src.dashboard.export import build_report_xlsx, build_report_html
from src.dashboard.region_map import _risk_by_branch

FIX = Path(__file__).parent/'fixtures'
APP = Path(__file__).resolve().parents[1]/'src/dashboard/streamlit_app.py'
PAGES = ['项目首页','核心指标表','风险预警中心','归因分析中心','AI 风控顾问','预警通知中心','数据上传与Sheet识别']

@pytest.mark.parametrize('total',['合计','合 计','小计','总计','全车型合计','湖北-合计','北京合计'])
def test_total_variants(total):
    df = pd.DataFrame({'机构':['湖北']*3,'车型':['家用车','企业车',total],'保费占比':[60,40,100],'预期成本率':[80,80,120]})
    assert is_total_row(df.iloc[2])
    assert apply_alert_rules(df).iloc[2]['预警等级']=='normal'
    assert len(evaluate_frame(df))==2
    assert validation_summary(df).iloc[0]['结果']=='通过'

@pytest.mark.parametrize('premium,progress,expected',[(150,1,True),(50,3,True),(150,3,True),(50,1,False)])
def test_r008_or(premium,progress,expected):
    rule=next(r for r in load_alert_rules() if r['id']=='R008')
    cells=[MetricCell('家用车','出险频度',2,'月同比','湖北'),MetricCell('家用车','满期保费',premium,'当月','湖北'),MetricCell('家用车','满期进度',progress,'当月','湖北')]
    assert bool(evaluate_rule(rule,cells))==expected

def test_threshold_persistence_and_boundaries(tmp_path):
    path=tmp_path/'settings.db'
    save_user_thresholds(dict(yellow=88,orange=94,red=101),path)
    assert load_user_thresholds(path)==dict(yellow=88,orange=94,red=101)
    with pytest.raises(ValueError): save_user_thresholds(dict(yellow=95,orange=95,red=100),path)
    reset_thresholds(path); assert load_user_thresholds(path)=={}
    from src.alerts.thresholds import _level
    thresholds=dict(yellow=90,orange=95,red=100)
    assert [_level(x,thresholds) for x in (90,95,96,100,101)]==['normal','yellow','orange','orange','red']

def test_real_t2_business_rows():
    book=openpyxl.load_workbook(FIX/'real.xlsx',data_only=True)
    sheet=book['分车型预期成本率监控表（日月年）']
    blocks=read_table_blocks(sheet)
    assert sum(len(b[['来源行']].drop_duplicates()) for b in blocks)==42
    assert all(b['车型'].str.strip().ne('').all() for b in blocks)
    assert not any(b['车型'].str.contains('监控表').any() for b in blocks)
    book.close()

def test_pipeline_combination_and_exports():
    df=pd.DataFrame([dict(机构='湖北',车型='家用车',预期成本率=85,单均保费_元_月环比=-11,案均赔款_月同比=25,累计出险频度_月同比=2)])
    source=df.to_dict('records')+[dict(机构='湖北',车型='家用车',期间='当月',满期保费=150)]
    df.attrs['all_periods']=source
    out=evaluate_frame(df)
    assert {'R006','R007','R008','C001'} <= set(out.iloc[0]['触发规则'].split('、'))
    assert out.iloc[0]['预警等级']=='orange'
    book=openpyxl.load_workbook(BytesIO(build_report_xlsx(out)))
    assert book.sheetnames==['概览','预警明细','触发规则','数据质量','阈值说明']
    assert '@media print' in build_report_html(out)

def test_weighted_map():
    df=pd.DataFrame({'机构':['湖北']*3,'车型':['甲','乙','合计'],'签单保费_万元':[1,3,100],'预期成本率':[80,100,200]})
    assert _risk_by_branch(df).iloc[0]['风险值']==95

@pytest.mark.parametrize('mode',['demo','real_filled.xlsx','flat_no_branch.xlsx'])
def test_all_seven_pages(mode,tmp_path):
    with patch('src.config.threshold_store.DB_PATH',tmp_path/'irds.db'):
        app=AppTest.from_file(APP,default_timeout=120).run()
        assert not app.exception
        if mode=='demo':
            for page in PAGES:
                app.sidebar.radio[0].set_value(page).run()
                assert not app.exception, (page,[e.message for e in app.exception])
        else:
            uploaded=UploadedFile(UploadedFileRec('fixture',mode,'application/octet-stream',(FIX/mode).read_bytes()),FileURLs())
            with patch('streamlit.delta_generator.DeltaGenerator.file_uploader',return_value=uploaded):
                app.sidebar.radio[1].set_value('上传Excel文件').run()
                for page in PAGES:
                    app.sidebar.radio[0].set_value(page).run()
                    assert not app.exception, (page,[e.message for e in app.exception])
                    assert not any('数据处理异常' in e.value for e in app.error)


def test_units_and_merged_titles():
    book=openpyxl.Workbook(); ws=book.active
    ws.append(['签单保费单位：元；单均保费单位：万元'])
    ws.append(['序号','车型','签单保费','单均保费','预期赔付率'])
    ws.append([1,'家用车',100000,0.3,0.66])
    ws.merge_cells('A4:E4'); ws['A4']='下一张监控表标题'
    frame=read_table_blocks(ws)[0]
    assert len(frame)==1
    assert frame.iloc[0]['签单保费_万元']==10
    assert frame.iloc[0]['单均保费_元']==3000


def test_upload_metadata_is_comparable():
    frame=read_uploaded_data(FIX/'real_filled.xlsx')
    assert isinstance(frame.attrs['smart_validation'],list)
    # 触发 pandas Styler 中的属性比较路径。
    frame.style.to_html()
    pd.concat([frame.iloc[:1],frame.iloc[1:]])


def test_simulated_email_never_opens_smtp():
    from src.notification.email_sender import send_email
    with patch('src.notification.email_sender._smtp_config',side_effect=AssertionError('禁止读取实发配置')):
        assert send_email('测试','测试',['test@example.test'],dry_run=True).mode=='mock'

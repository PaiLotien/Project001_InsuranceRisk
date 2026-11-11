from io import BytesIO
from unittest.mock import patch
import pandas as pd
import pytest
import yaml
from src.validation.schema import validate_frame, top3, ROOT
from src.validation.cross_report import cross_report_issues
from src.parser.excel_loader import excel_engine, read_excel


def valid():
    return pd.DataFrame(
        {
            "机构": ["湖北", "湖北"],
            "车型": ["家用车", "合计"],
            "预期赔付率": [70.0, 70.0],
            "成本率1": [20.0, 20.0],
            "预期成本率": [90.0, 90.0],
            "签单保费": [100.0, 100.0],
            "赔款": [70.0, 70.0],
            "赔付率": [70.0, 70.0],
            "保费占比": [100.0, 100.0],
        }
    )


def test_valid_schema_and_rule_count():
    assert len(yaml.safe_load((ROOT / "config/data_quality_rules.yaml").read_text())["schema_rules"]) >= 20
    assert validate_frame(valid()).empty


@pytest.mark.parametrize(
    "column,value,rule",
    [
        ("机构", "", "DQ101"),
        ("车型", None, "DQ102"),
        ("预期赔付率", "bad", "DQ111"),
        ("成本率1", float("inf"), "DQ112"),
        ("保费占比", 101, "DQ123"),
        ("签单保费", -1, "DQ121"),
        ("预期成本率", 99, "DQ141"),
        ("赔付率", 60, "DQ142"),
    ],
)
def test_schema_failure_location(column, value, rule):
    frame = valid().astype(object)
    frame.loc[0, column] = value
    frame["来源行"] = [7, 8]
    result = validate_frame(frame, "T2主表")
    found = result[result.rule_id.eq(rule)]
    assert not found.empty
    assert found.iloc[0]["row"] == 7
    assert found.iloc[0]["sheet"] == "T2主表"
    assert found.iloc[0]["column"] == column
    assert found.iloc[0]["severity"] == "error"


def test_total_share_and_top3():
    df = valid()
    df.loc[0, "保费占比"] = 50
    df.loc[1, "签单保费"] = 200
    issues = validate_frame(df)
    assert {"DQ001", "DQ152"}.issubset(set(issues.rule_id))
    assert len(top3(issues)) <= 3
    assert "DQ151" in set(validate_frame(df.iloc[:1]).rule_id)


def test_missing_column_and_zero_denominator():
    assert "DQ101" in set(validate_frame(valid().drop(columns="机构")).rule_id)
    df = valid()
    df.loc[0, "签单保费"] = 0
    assert "DQ142" in set(validate_frame(df).rule_id)


def test_duplicate_index_uses_physical_position():
    df = valid()
    df.index = [9, 9]
    df.loc[:, "预期成本率"] = 91
    issues = validate_frame(df)
    assert issues[issues.rule_id.eq("DQ141")]["row"].tolist() == [2, 3]


def test_cross_report_uniform_issues():
    t2 = pd.DataFrame({"机构": ["湖北"], "当月累计签单保费": [100], "来源行号": [8]})
    t7 = pd.DataFrame({"机构": ["湖北"], "月签单保费": [90]})
    result = cross_report_issues(t2, t7)
    assert result.rule_id.tolist() == ["DQ002", "DQ002"]
    assert result.iloc[0]["row"] == 8
    assert cross_report_issues(t2, t7.assign(月签单保费=100)).empty


def test_calamine_and_fallback():
    data = BytesIO()
    valid().to_excel(data, index=False)
    data.seek(0)
    assert excel_engine() == "calamine"
    assert len(read_excel(data)) == 2
    with patch.dict("sys.modules", {"python_calamine": None}):
        assert excel_engine() == "openpyxl"


def test_quality_business_toggle_and_daily_warning():
    original = yaml.safe_load((ROOT / "config/data_quality_rules.yaml").read_text())
    df = valid()
    df["当日值"] = 1
    df["当月天数"] = 30
    df["当月总值"] = 100
    assert "DQ003" not in set(validate_frame(df).rule_id)
    original["rules"][2]["enabled"] = True
    original["rules"][0]["enabled"] = False
    df["保费占比"] = 50
    with patch("src.validation.schema.yaml.safe_load", return_value=original):
        issues = validate_frame(df)
    assert "DQ001" not in set(issues.rule_id)
    assert set(issues[issues.rule_id.eq("DQ003")].severity) == {"warning"}

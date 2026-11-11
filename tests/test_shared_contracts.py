"""重构前后需要保持的类型及页面契约。"""

import inspect
import pickle
import unittest
from dataclasses import asdict, fields
from pathlib import Path

from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest

from src.attribution.simple_attribution import AttrItem
from src.core.models import AttributionItem


class SharedContractTests(unittest.TestCase):
    def test_attribution_item_public_names_and_serialization(self):
        values = {"dimension": "成本构成", "factor_name": "赔付率", "contribution_pct": 75.0,
                  "description": "演示", "is_primary": True}
        for cls in (AttrItem, AttributionItem):
            with self.subTest(name=cls.__name__):
                item = cls(**values)
                self.assertEqual(asdict(item), values)
                self.assertEqual(asdict(pickle.loads(pickle.dumps(item))), values)
                self.assertEqual(type(pickle.loads(pickle.dumps(item))), cls)
                self.assertTrue(repr(item).startswith(cls.__name__ + "("))
                self.assertEqual(list(inspect.signature(cls).parameters), list(values))
                self.assertEqual([f.name for f in fields(cls)], list(values))
        self.assertEqual(AttrItem.__module__, "src.attribution.simple_attribution")
        self.assertEqual(AttributionItem.__module__, "src.core.models")

    def test_upload_modes_show_the_same_nine_report_types(self):
        path = Path(__file__).resolve().parents[1] / "src/dashboard/streamlit_app.py"
        app = AppTest.from_file(path, default_timeout=30).run()
        app.sidebar.radio[0].set_value("数据质量").run()
        self.assertFalse(app.exception)
        report_table = lambda: next(d.value for d in app.dataframe if "编号" in d.value.columns)
        demo_table = report_table().copy()
        self.assertEqual(demo_table["编号"].tolist(), [f"T{i}" for i in range(1, 10)])
        app.sidebar.radio[1].set_value("上传机构数据").run()
        self.assertFalse(app.exception)
        assert_frame_equal(demo_table, report_table())


def test_default_attr_model_fields_stay_equal():
    left = [(f.name, f.type, f.default) for f in fields(AttrItem)]
    right = [(f.name, f.type, f.default) for f in fields(AttributionItem)]
    assert left == right

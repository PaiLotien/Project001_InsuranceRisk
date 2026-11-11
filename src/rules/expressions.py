"""仅允许布尔常量、规则名及 AND/OR/NOT，不执行 Python 代码。"""

import ast
import re
import operator
from pathlib import Path
import yaml
from functools import lru_cache

COMPARATORS = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "change_gt": operator.gt,
    "change_lt": operator.lt,
}
SYMBOLS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "change_gt": ">", "change_lt": "<"}


def safe_condition(expression, values):
    expression = re.sub(r"\b(AND|OR|NOT)\b", lambda m: m[0].lower(), expression)
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name):
            return bool(values.get(node.id, False))
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            results = [visit(v) for v in node.values]
            return all(results) if isinstance(node.op, ast.And) else any(results)
        raise ValueError("不允许的组合表达式")

    return visit(tree)


@lru_cache(maxsize=8)
def _matrix_config(path, mtime):
    return yaml.safe_load(Path(path).read_text()) or {}


def matrix_threshold(branch, vehicle, rule_id, default):
    path = Path(__file__).resolve().parents[2] / "config/threshold_matrix.yaml"
    config = _matrix_config(str(path), path.stat().st_mtime_ns)
    matches = [
        r
        for r in config.get("thresholds", [])
        if r["branch"] == branch and r["vehicle_type"] == vehicle and r["rule_id"] == rule_id
    ]
    if len(matches) > 1:
        raise ValueError("差异阈值配置重复")
    return float(matches[0]["threshold"]) if matches else default

from __future__ import annotations

import ast
import operator
import re


ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10:
            raise ValueError("exponent is too large")
        return ALLOWED_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_OPERATORS:
        return ALLOWED_OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ValueError("unsupported expression")


def extract_expression(query: str) -> str:
    match = re.search(r"[-+*/%().\d\s]{3,}", query)
    if not match:
        raise ValueError("no arithmetic expression found")
    return match.group(0).strip()


def calculate(query: str) -> str:
    expression = extract_expression(query)
    tree = ast.parse(expression, mode="eval")
    value = _evaluate(tree)
    rendered = str(int(value)) if value.is_integer() else f"{value:.8g}"
    return f"{expression} = {rendered}"


from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser  # noqa: E402

from codegen.codegen import CodeGen  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402


def _to_c(src: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    return CTranspiler().transpile(ast, "<inline>")


def _to_ir(src: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    return CodeGen().generate(ast, "<inline>")


def test_int64_max_plus_one_keeps_checked_add() -> None:
    src = """
def main(): int
    i = 9223372036854775807
    x = i + 1
    return x
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_int64_min_minus_one_keeps_checked_sub() -> None:
    src = """
def main(): int
    i = -9223372036854775808
    x = i - 1
    return x
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_sub(" in c_code
    ir_text = _to_ir(src)
    assert "sub nsw i64" not in ir_text


def test_int64_mul_overflow_keeps_checked_mul() -> None:
    src = """
def main(): int
    i = 3037000500
    x = i * i
    return x
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_mul(" in c_code
    ir_text = _to_ir(src)
    assert "mul nsw i64" not in ir_text

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


def test_future_assignment_must_not_retroactively_prove_overflow_safe() -> None:
    src = """
def main(): int
    i = 9223372036854775807
    x = i + 1
    i = 0
    return x
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_unbounded_loop_increment_keeps_checked_add() -> None:
    src = """
def main(): int
    i = 0
    while true then
        i = i + 1
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_unknown_upper_bound_loop_keeps_checked_add() -> None:
    src = """
def main(n: int): int
    i = 0
    while i <= n then
        i = i + 1
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_unknown_side_effect_call_before_add_keeps_checked_add() -> None:
    src = """
extern fn touch(): int

def main(): int
    i := 0..100 = 0
    touch()
    i = i + 1
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_unknown_side_effect_call_in_loop_keeps_checked_add() -> None:
    src = """
extern fn touch(): int

def main(): int
    i := 0..100 = 0
    while i < 10 then
        touch()
        i = i + 1
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_unknown_call_inside_dict_write_keeps_checked_add() -> None:
    src = """
extern fn touch(): int

def main(): int
    d = {"a": 1}
    i := 0..100 = 0
    while i < 10 then
        d["a"] = touch()
        i = i + 1
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_branch_heavy_loop_keeps_checked_add() -> None:
    src = """
def main(): int
    i = 0
    while i < 100 then
        if i < 50 then
            i = i + 1
        else
            i = i + 2
        end
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code
    ir_text = _to_ir(src)
    assert "add nsw i64" not in ir_text


def test_while_true_break_guard_can_prove_bounded_increment() -> None:
    src = """
def main(): int
    i := 0..100 = 0
    while true then
        if i >= 100 then
            break
        end
        i = i + 1
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" not in c_code
    ir_text = _to_ir(src)
    assert "add nsw" in ir_text
    assert "llvm.sadd.with.overflow" not in ir_text

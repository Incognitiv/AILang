from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.codegen import CodeGen  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402


def _to_ir(src: str) -> str:
    ast = Parser(tokenize(src)).parse_program()
    return CodeGen().generate(ast, "<inline>")


def test_local_storage_accounts_for_values_before_the_final_assignment() -> None:
    ir_text = _to_ir(
        """
def passthrough(x):
    return x
end

def main():
    uint i = 5000000000
    uint observed = passthrough(i)
    i = 1
    return observed
end
"""
    )

    main_ir = ir_text[ir_text.index('define i32 @"main"') :]
    assert '%"i" = alloca i64' in main_ir
    assert 'call i64 @"passthrough"(i64 5000000000)' in main_ir
    assert "sext i32 5000000000 to i64" not in main_ir


def test_default_int_storage_is_not_narrowed_before_wide_arithmetic() -> None:
    ir_text = _to_ir(
        """
def main(): int
    int x = 2000000000
    return x + x
end
"""
    )

    main_ir = ir_text[ir_text.index('define i32 @"main"') :]
    assert '%"x" = alloca i64' in main_ir
    assert "add nsw i64 2000000000, 2000000000" in main_ir
    assert "add nsw i32" not in main_ir


def test_addressable_default_int_keeps_an_eight_byte_slot() -> None:
    ir_text = _to_ir(
        """
@effect(memory)
def main(): int
    int x = 7
    p = addressof(x)
    poke64(p, 0, 5000000000)
    return x
end
"""
    )

    main_ir = ir_text[ir_text.index('define i32 @"main"') :]
    assert '%"x" = alloca i64' in main_ir
    assert "store i64 5000000000" in main_ir


def test_range_value_is_checked_before_any_storage_narrowing() -> None:
    ir_text = _to_ir(
        """
def main(): int
    i := 0..100 = 4294967297
    return i
end
"""
    )

    main_ir = ir_text[ir_text.index('define i32 @"main"') :]
    assert '%"i" = alloca i64' in main_ir
    assert "store i64 4294967297" in main_ir
    assert "icmp slt i64" in main_ir
    assert "icmp sgt i64" in main_ir

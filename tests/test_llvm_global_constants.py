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


def test_negative_integer_global_constant_keeps_its_sign() -> None:
    ir_text = _to_ir(
        """
const int NEGATIVE = -100

def main(): int
    return NEGATIVE
end
"""
    )

    assert '@"NEGATIVE" = internal constant i64 -100' in ir_text


def test_positive_integer_global_constant_is_unchanged() -> None:
    ir_text = _to_ir(
        """
const int POSITIVE = 42

def main(): int
    return POSITIVE
end
"""
    )

    assert '@"POSITIVE" = internal constant i64 42' in ir_text

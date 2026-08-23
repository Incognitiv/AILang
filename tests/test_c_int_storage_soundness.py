from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402


def _to_c(src: str) -> str:
    ast = Parser(tokenize(src)).parse_program()
    return CTranspiler().transpile(ast, "<inline>")


def test_default_int_local_keeps_language_level_width() -> None:
    c_code = _to_c(
        """
def main(): int
    int x = 2000000000
    return x + x
end
"""
    )

    assert "int64_t x;" in c_code
    assert "int32_t x;" not in c_code


def test_earlier_wide_value_is_not_narrowed_by_a_later_assignment() -> None:
    c_code = _to_c(
        """
def main(): int
    int x = 5000000000
    int observed = x
    x = 1
    return observed
end
"""
    )

    assert "int64_t x;" in c_code
    assert "int32_t x;" not in c_code


def test_addressable_default_int_keeps_an_eight_byte_object() -> None:
    c_code = _to_c(
        """
@effect(memory)
def main(): int
    int x = 7
    address = addressof(x)
    poke64(address, 0, 5000000000)
    return x
end
"""
    )

    assert "int64_t x;" in c_code
    assert "int32_t x;" not in c_code
    assert "&(x)" in c_code

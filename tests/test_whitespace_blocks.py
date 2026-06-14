from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser

from lexer.scan import tokenize


def _parse_program(src: str) -> list[Any]:
    return Parser(tokenize(src)).parse_program()


def _shape(value: Any) -> Any:
    if isinstance(value, list):
        return [_shape(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_shape(item) for item in value)
    if hasattr(value, "__dict__"):
        return (
            type(value).__name__,
            {
                key: _shape(item)
                for key, item in sorted(vars(value).items())
                if not key.startswith("_")
            },
        )
    return value


def test_indentation_is_cosmetic_for_normal_ailang_blocks() -> None:
    tidy = """
def main(): int
    if 1 then
        return 10
    else
        return 20
    end
end
"""

    awkward = """
def main(): int
        if 1 then
return 10
             else
                    return 20
        end
end
"""

    assert _shape(_parse_program(awkward)) == _shape(_parse_program(tidy))

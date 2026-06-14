from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize
from parser.parser import Parser


def _parse_type(type_expr: str):
    tokens = tokenize(type_expr)
    parser = Parser(tokens)
    parsed = parser.parse_type()
    assert parser.pos == len(tokens), f"unconsumed tokens for {type_expr!r}"
    return parsed


def test_builtin_unsigned_aliases_canonicalize_without_double_u_prefix() -> None:
    cases = {
        "tiny": "i8",
        "byte": "u8",
        "small": "i16",
        "usmall": "u16",
        "short": "i32",
        "ushort": "u32",
        "int": "i64",
        "uint": "u64",
        "long": "i128",
        "ulong": "u128",
    }
    for src, expected in cases.items():
        assert _parse_type(src) == expected


def test_explicit_unsigned_does_not_reprefix_unsigned_aliases() -> None:
    cases = {
        "unsigned tiny": "u8",
        "unsigned byte": "u8",
        "unsigned small": "u16",
        "unsigned usmall": "u16",
        "unsigned short": "u32",
        "unsigned ushort": "u32",
        "unsigned int": "u64",
        "unsigned uint": "u64",
        "unsigned long": "u128",
        "unsigned ulong": "u128",
    }
    for src, expected in cases.items():
        assert _parse_type(src) == expected

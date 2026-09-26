"""AST values must obey the same source-literal contract as lexical helpers."""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from lexer.scan import unescape_string
from parser.ast_expr_nodes import StringLit


@pytest.mark.parametrize("literal,expected", [
    (r'"\x41"', "A"),
    (r'"\u017c"', "ż"),
    (r'"\x5cn"', "\\n"),
    (r'"\u005cn"', "\\n"),
    (r'"\\x41"', "\\x41"),
    (r'"\\u017c"', "\\u017c"),
    (r'"\0BACKSLASH\0"', "\0BACKSLASH\0"),
    (r'"\0BACKSLASH\0n"', "\0BACKSLASH\0n"),
    ('"\0BACKSLASH\0"', "\0BACKSLASH\0"),
    ('"\\\'"', "'"),
    (r'"\q\xG0"', r"\q\xG0"),
])
def test_ast_matches_independent_literal_value(literal: str, expected: str) -> None:
    assert StringLit(literal).value == expected
    assert unescape_string(literal) == expected


def test_short_payload_roundtrip_through_actual_ast_constructor() -> None:
    alphabet = ("a", "\\", "n", "x", "0", '"', "\n", "\r", "\t", "ż", "\0")
    spellings = {"\\": r"\\", '"': r'\"', "\n": r"\n", "\r": r"\r", "\t": r"\t", "\0": r"\0"}
    for length in range(5):
        for characters in itertools.product(alphabet, repeat=length):
            expected = "".join(characters)
            encoded = '"' + "".join(spellings.get(c, c) for c in characters) + '"'
            assert StringLit(encoded).value == expected, repr(encoded)

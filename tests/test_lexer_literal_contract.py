"""Independent source-value contracts, not merely agreement between backends."""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from lexer.scan import char_literal_to_int, tokenize, unescape_string


@pytest.mark.parametrize("literal,expected", [
    (r'"\\n"', "\\n"),
    (r'"\\t"', "\\t"),
    (r'"\\r"', "\\r"),
    (r'"\\0"', "\\0"),
    (r'"\\x41"', "\\x41"),
    (r'"\\u0041"', "\\u0041"),
    (r'"\x5cn"', "\\n"),
    (r'"\u005cn"', "\\n"),
    (r'"\x5cu0041"', "\\u0041"),
    (r'"C:\\work\\new\\test"', "C:\\work\\new\\test"),
    (r'"\n\t\r\x41\u017c"', "\n\t\rAż"),
    (r'"\q\xG0\uZZZZ"', r"\q\xG0\uZZZZ"),
])
def test_literal_value_is_decoded_once(literal: str, expected: str) -> None:
    assert unescape_string(literal) == expected


def test_exhaustive_small_payload_roundtrips() -> None:
    """16,105 payloads include all boundaries up to four characters long."""
    alphabet = ("a", "\\", "n", "x", "0", '"', "\n", "\r", "\t", "ż", "\0")
    spellings = {"\\": r"\\", '"': r'\"', "\n": r"\n", "\r": r"\r", "\t": r"\t", "\0": r"\0"}
    for length in range(5):
        for characters in itertools.product(alphabet, repeat=length):
            expected = "".join(characters)
            encoded = '"' + "".join(spellings.get(c, c) for c in characters) + '"'
            assert unescape_string(encoded) == expected, repr(encoded)


def test_generated_backslashes_are_never_reparsed() -> None:
    for slash in (r"\\", r"\x5c", r"\u005c"):
        for suffix in ("n", "t", "r", "0", "x41", "u0041"):
            assert unescape_string('"' + slash + suffix + '"') == "\\" + suffix


@pytest.mark.parametrize("literal", ['"""one\ntwo"""', '"one\ntwo"', '"one\n#{2}two"'])
def test_positions_after_multiline_literal(literal: str) -> None:
    code = "  " + literal + " after\nnext"
    found = {text: (line, col) for _, text, line, col in tokenize(code)}
    assert found[literal] == (1, 3)
    offset = code.index("after")
    assert found["after"] == (2, offset - code.rfind("\n", 0, offset))
    assert found["next"] == (3, 1)


def test_error_location_after_multiline_literal() -> None:
    with pytest.raises(SyntaxError, match=r"Line 3, Col 1:"):
        tokenize('"""a\nb"""\n$')


def test_hexadecimal_and_octal_character_escapes_reach_the_decoder() -> None:
    for value in range(256):
        for literal in (f"'\\x{value:02x}'", f"'\\{value:03o}'"):
            tokens = tokenize(literal)
            assert tokens == [("CHARLIT", literal, 1, 1)]
            assert char_literal_to_int(literal) == value


@pytest.mark.parametrize("literal", [r"'\xG0'", r"'\x4'", r"'\1234'", "'ab'"])
def test_malformed_character_literals_are_rejected(literal: str) -> None:
    with pytest.raises(SyntaxError):
        tokenize(literal)


@pytest.mark.parametrize("literal,value", [("''", 0), ("'a'", 97), (r"'\n'", 10), (r"'\\'", 92)])
def test_existing_character_semantics_are_preserved(literal: str, value: int) -> None:
    assert tokenize(literal) == [("CHARLIT", literal, 1, 1)]
    assert char_literal_to_int(literal) == value


def test_unicode_source_security_check_is_still_active() -> None:
    with pytest.raises(SyntaxError, match="Dangerous Unicode"):
        tokenize("x = 1\u202e")

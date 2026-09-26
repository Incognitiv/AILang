"""Decode string-literal escapes without reinterpreting replacement text."""

from __future__ import annotations

import re

_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
    "0": "\0",
}
_STRING_ESCAPE = re.compile(r'\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|[ntr\\"0])')


def _decode_escape(match: re.Match[str]) -> str:
    escape = match.group(1)
    if escape in _SIMPLE_ESCAPES:
        return _SIMPLE_ESCAPES[escape]
    return chr(int(escape[1:], 16))


def decode_string_literal(text: str) -> str:
    """Decode only escapes present in the input, preserving unknown sequences.

    Numeric escapes retain the existing code-point semantics. In particular,
    an escaped backslash is data, never the start of a second decoding pass.
    """
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return _STRING_ESCAPE.sub(_decode_escape, text)

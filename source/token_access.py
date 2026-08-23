"""Named accessors for lexer token tuples."""

from __future__ import annotations

from typing import Any, Sequence

TokenLike = Sequence[Any]


def token_type_at(tokens: Sequence[TokenLike], index: int) -> str:
    """Return the token type at `index`."""
    token_type, *_rest = tokens[index]
    return str(token_type)


def token_text_at(tokens: Sequence[TokenLike], index: int) -> str:
    """Return the token text/value at `index`."""
    _token_type, token_text, *_rest = tokens[index]
    return str(token_text)


def token_line_at(tokens: Sequence[TokenLike], index: int) -> int:
    """Return the source line for the token at `index`."""
    _token_type, _token_text, token_line, *_rest = tokens[index]
    return int(token_line)


def token_col_at(tokens: Sequence[TokenLike], index: int) -> int:
    """Return the source column for the token at `index`."""
    _token_type, _token_text, _token_line, token_col, *_rest = tokens[index]
    return int(token_col)

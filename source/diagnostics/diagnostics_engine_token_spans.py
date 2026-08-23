"""Token-span helpers for diagnostics that should ignore declarative DSL blocks."""

from __future__ import annotations

from typing import List, Tuple

from token_access import token_text_at, token_type_at

_UI_BLOCK_TAGS: set[str] = {
    "window",
    "background",
    "font",
    "scrollable",
    "panel",
    "dock",
}


def ignored_declarative_token_indices(tokens: List[Tuple]) -> set[int]:
    """Return token indices that are checked by parsers, not name diagnostics."""
    return (
        _ui_dsl_token_indices(tokens)
        | _cabi_header_token_indices(tokens)
        | _decorator_argument_token_indices(tokens)
    )


def _looks_like_ui_block(tokens: List[Tuple], index: int) -> bool:
    """Return true for canonical UI DSL blocks such as `window name:`."""
    token_type, token_text, _token_line, _token_col = tokens[index]
    if token_type != "IDENT" or str(token_text).lower() not in _UI_BLOCK_TAGS:
        return False
    for offset in range(1, 4):
        ahead = index + offset
        if ahead >= len(tokens):
            return False
        ahead_type = token_type_at(tokens, ahead)
        if ahead_type == "COLON":
            return True
        if ahead_type not in ("IDENT", "STRING", "STRLIT"):
            return False
    return False


def _looks_like_ui_include(tokens: List[Tuple], index: int) -> bool:
    """Return true for canonical UI DSL include lines: `include "file.ail"`."""
    return (
        token_type_at(tokens, index) in ("IDENT", "UI_INCLUDE")
        and str(token_text_at(tokens, index)).lower() == "include"
        and index + 1 < len(tokens)
        and token_type_at(tokens, index + 1) in ("STRING", "STRLIT")
    )


def _ui_dsl_token_indices(tokens: List[Tuple]) -> set[int]:
    """Find tokens belonging to top-level UI DSL syntax."""
    ignored: set[int] = set()
    i = 0
    while i < len(tokens):
        if _looks_like_ui_include(tokens, i):
            ignored.add(i)
            ignored.add(i + 1)
            i += 2
            continue

        if not _looks_like_ui_block(tokens, i):
            i += 1
            continue

        depth = 0
        while i < len(tokens):
            ignored.add(i)
            token_type, token_text, _token_line, _token_col = tokens[i]
            if token_type == "COLON":
                depth += 1
            elif token_type == "END" or (
                token_type == "IDENT" and str(token_text).lower() == "end"
            ):
                depth -= 1
                i += 1
                if depth <= 0:
                    break
                continue
            i += 1

    return ignored


def _cabi_header_token_indices(tokens: List[Tuple]) -> set[int]:
    """Find tokens belonging to ``abi header`` declarations."""
    ignored: set[int] = set()
    i = 0
    while i < len(tokens):
        token_type, token_text, _token_line, _token_col = tokens[i]
        if (
            token_type != "IDENT"
            or token_text not in {"abi", "cabi"}
            or i + 1 >= len(tokens)
            or token_type_at(tokens, i + 1) != "IDENT"
            or token_text_at(tokens, i + 1) != "header"
        ):
            i += 1
            continue

        depth = 1
        while i < len(tokens):
            ignored.add(i)
            current_type, current_text, _line, _col = tokens[i]
            if current_text in {
                "struct",
                "macro",
                "inline",
                "if",
                "ifdef",
                "ifndef",
            }:
                depth += 1
            elif current_type == "END":
                depth -= 1
                if depth <= 0:
                    i += 1
                    break
            i += 1

    return ignored


def _decorator_argument_token_indices(tokens: List[Tuple]) -> set[int]:
    """Ignore declarative decorator names/arguments in identifier diagnostics."""
    ignored: set[int] = set()
    i = 0
    while i < len(tokens):
        if token_type_at(tokens, i) != "AT" or i + 1 >= len(tokens):
            i += 1
            continue

        ignored.add(i)
        ignored.add(i + 1)
        i += 2
        if i >= len(tokens) or token_type_at(tokens, i) != "LPAREN":
            continue

        depth = 0
        while i < len(tokens):
            ignored.add(i)
            current_type = token_type_at(tokens, i)
            if current_type == "LPAREN":
                depth += 1
            elif current_type == "RPAREN":
                depth -= 1
                i += 1
                if depth <= 0:
                    break
                continue
            i += 1

    return ignored

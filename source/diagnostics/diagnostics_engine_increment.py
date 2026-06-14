"""Increment/decrement diagnostics for AILang."""

from __future__ import annotations

from typing import List, Tuple

from token_access import token_type_at

from .diagnostics_models import Diagnostic

_INCREMENT_TOKENS = frozenset({"PLUSPLUS", "MINUSMINUS"})
_STATEMENT_START_TOKENS = frozenset(
    {"THEN", "DO", "ELSE", "ELSIF", "CASE", "DEFAULT", "COLON"}
)
_STATEMENT_END_TOKENS = frozenset({"END", "ELSE", "ELSIF", "CASE", "DEFAULT"})


def _starts_statement(tokens: List[Tuple], token_index: int) -> bool:
    """Return true when token_index appears at the start of a statement."""
    if token_index <= 0:
        return True
    previous = tokens[token_index - 1]
    current = tokens[token_index]
    if previous[2] < current[2]:
        return True
    return previous[0] in _STATEMENT_START_TOKENS


def _ends_statement(tokens: List[Tuple], token_index: int) -> bool:
    """Return true when token_index appears at the end of a statement."""
    if token_index + 1 >= len(tokens):
        return True
    current = tokens[token_index]
    following = tokens[token_index + 1]
    if following[2] > current[2]:
        return True
    return following[0] in _STATEMENT_END_TOKENS


def _emit_increment_context_diagnostic(engine, token: Tuple) -> None:
    token_type, token_text, token_line, token_col = token
    op = token_text if token_text in ("++", "--") else token_type
    engine.diagnostics.append(
        Diagnostic(
            line=token_line,
            column=token_col,
            message=(
                f"'{op}' is statement-only in AILang; it cannot be used "
                "inside an expression"
            ),
            suggestion=(
                "Move the mutation to its own line, then read the variable "
                "in a separate expression"
            ),
            severity="error",
        )
    )


def check_increment_decrement_statement_only(engine, tokens: List[Tuple]) -> None:
    """Reject C-style expression-valued ++/-- forms.

    AILang keeps prefix and postfix increment/decrement as equivalent
    standalone mutation statements. It intentionally does not assign a value
    to `i++`, `++i`, `i--`, or `--i` inside larger expressions.
    """
    for index, token in enumerate(tokens):
        token_type, *_token_fields = token
        if token_type not in _INCREMENT_TOKENS:
            continue

        previous_is_ident = index > 0 and token_type_at(tokens, index - 1) == "IDENT"
        next_is_ident = (
            index + 1 < len(tokens) and token_type_at(tokens, index + 1) == "IDENT"
        )

        if previous_is_ident:
            statement_start = _starts_statement(tokens, index - 1)
            statement_end = _ends_statement(tokens, index)
            if statement_start and statement_end:
                continue

        if next_is_ident:
            statement_start = _starts_statement(tokens, index)
            statement_end = _ends_statement(tokens, index + 1)
            if statement_start and statement_end:
                continue

        _emit_increment_context_diagnostic(engine, token)

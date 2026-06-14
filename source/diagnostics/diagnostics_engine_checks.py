"""Validation check helpers for DiagnosticEngine."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from token_access import token_col_at, token_line_at, token_text_at, token_type_at

from .diagnostics_catalog import (
    CALLABLE_BUILTINS,
    LANGUAGE_SURFACE,
    PATTERN_FIXES,
    TOKEN_HINTS,
    TYPE_NAMES,
    TYPE_PREFIX_TOKENS,
    VECTOR_TYPE_NAMES,
)
from .diagnostics_engine_increment import check_increment_decrement_statement_only
from .diagnostics_engine_token_spans import ignored_declarative_token_indices
from .diagnostics_models import Diagnostic, Fix
from .diagnostics_utils import find_closest

CONTINUATION_OPERATORS = frozenset(
    {
        "PLUS",
        "MINUS",
        "STAR",
        "SLASH",
        "MOD",
        "EQ",
        "NEQ",
        "LT",
        "GT",
        "LTEQ",
        "GTEQ",
        "LSHIFT",
        "RSHIFT",
        "AMPERSAND",
        "PIPE",
        "CARET",
        "AND",
        "OR",
        "ASSIGN",
        "COLON_ASSIGN",
        "COMMA",
        "COLON",
        "DOT",
        "SAFE_DOT",
        "QUESTION",
        "LPAREN",
        "LBRACKET",
        "LBRACE",
    }
)
RETURN_CONTINUATION_TOKENS = CONTINUATION_OPERATORS - {
    "LPAREN",
    "LBRACKET",
    "LBRACE",
} | {"RPAREN", "RBRACKET", "RBRACE"}


def _check_unknown_identifiers(self, tokens: List[Tuple]) -> None:
    """Check for unknown identifiers and suggest corrections."""
    ignored_tokens = ignored_declarative_token_indices(tokens)

    for j in range(len(tokens) - 1):
        if (
            token_type_at(tokens, j) in ("EXCEPT", "CATCH")
            and token_type_at(tokens, j + 1) == "IDENT"
        ):
            self.user_symbols.add(token_text_at(tokens, j + 1))

    extern_signature_identifiers: set[int] = set()
    for j, token in enumerate(tokens):
        token_type, _token_text, token_line, _token_col = token
        if token_type != "EXTERN":
            continue
        k = j + 1
        while k < len(tokens):
            current_type, _current_text, current_line, _current_col = tokens[k]
            if current_line != token_line:
                break
            if current_type == "IDENT":
                extern_signature_identifiers.add(k)
            k += 1

    all_known = LANGUAGE_SURFACE | TYPE_NAMES | VECTOR_TYPE_NAMES | self.user_symbols

    for i, (ttype, tval, tline, tcol) in enumerate(tokens):
        if i in ignored_tokens:
            continue
        if ttype != "IDENT":
            continue
        if i in extern_signature_identifiers:
            continue

        if i + 1 < len(tokens):
            next_type = token_type_at(tokens, i + 1)
            if next_type == "ASSIGN":
                continue
            if next_type == "COLON":
                continue
            if next_type == "LBRACKET" and tval in {"slice", "view"}:
                continue
            if next_type == "COLON_ASSIGN":
                continue
            if i > 0 and token_type_at(tokens, i - 1) == "DEF":
                continue
            if i > 0 and token_type_at(tokens, i - 1) == "CATCH":
                continue
            if i > 0 and token_type_at(tokens, i - 1) == "EXCEPT":
                continue
            if i > 0 and token_type_at(tokens, i - 1) in (
                "CONST",
                "STATIC",
                "TYPE",
                "TYPEDEF",
                "EXTERN",
                "IMPORT",
                "FROM",
                "AS",
                "RECORD",
                "CLASS",
                "ENUM",
            ):
                continue
            if i > 0 and token_type_at(tokens, i - 1) in ("DOT", "SAFE_DOT"):
                continue
            if i > 0 and token_type_at(tokens, i - 1) in TYPE_PREFIX_TOKENS:
                continue
            if (
                i > 0
                and token_type_at(tokens, i - 1) == "IDENT"
                and token_text_at(tokens, i - 1) in (TYPE_NAMES | VECTOR_TYPE_NAMES)
                and next_type == "LPAREN"
            ):
                continue
            if i > 0 and token_type_at(tokens, i - 1) == "COLON":
                continue

        if tval in all_known or re.match(r"^[iuf]\d+$", tval):
            continue

        closest = find_closest(tval, all_known)
        if closest:
            self.diagnostics.append(
                Diagnostic(
                    line=tline,
                    column=tcol,
                    message=f"Unknown identifier '{tval}'",
                    suggestion=f"Did you mean '{closest}'?",
                    severity="error",
                )
            )
        else:
            self.diagnostics.append(
                Diagnostic(
                    line=tline,
                    column=tcol,
                    message=f"Unknown identifier '{tval}'",
                    suggestion="Check spelling or define it first",
                    severity="error",
                )
            )


def _check_patterns(self, tokens: List[Tuple]) -> None:
    """Check for known problematic patterns."""
    type_seq = [t[0] for t in tokens]

    for pattern, (message, fix) in PATTERN_FIXES.items():
        if message is None:
            continue  # Marker pattern, not an error

        plen = len(pattern)
        for i in range(len(type_seq) - plen + 1):
            if tuple(type_seq[i : i + plen]) == pattern:
                tline, tcol = token_line_at(tokens, i), token_col_at(tokens, i)
                self.diagnostics.append(
                    Diagnostic(
                        line=tline,
                        column=tcol,
                        message=message,
                        suggestion=fix,
                        severity="error",
                    )
                )

    check_increment_decrement_statement_only(self, tokens)


def _check_token_hints(self, tokens: List[Tuple]) -> None:
    """Check individual tokens for common mistakes."""
    for i, (ttype, tval, tline, tcol) in enumerate(tokens):
        if tval not in TOKEN_HINTS:
            continue
        if ttype == "IDENT":
            if i + 1 < len(tokens) and token_type_at(tokens, i + 1) == "ASSIGN":
                continue
            if i > 0 and token_type_at(tokens, i - 1) in (
                *TYPE_PREFIX_TOKENS,
                "DEF",
                "COMMA",
                "LPAREN",
            ):
                continue
            if i > 0 and token_type_at(tokens, i - 1) not in ("NEWLINE",):
                continue
        message, fix_text = TOKEN_HINTS[tval]
        if fix_text is not None:  # None means it's correct, just informational
            auto_fix = None
            if fix_text and fix_text != tval:
                auto_fix = Fix(line=tline, old_text=tval, new_text=fix_text)
            self.diagnostics.append(
                Diagnostic(
                    line=tline,
                    column=tcol,
                    message=message,
                    suggestion=fix_text,
                    severity=("warning" if "correct" in message.lower() else "error"),
                    fix=auto_fix,
                )
            )


def _rhs_is_direct_string_literal(tokens: List[Tuple], assign_index: int) -> bool:
    """Return true for `x = "literal"` without concatenation or calls."""
    rhs_index = assign_index + 1
    while rhs_index < len(tokens) and token_type_at(tokens, rhs_index) in ("NEWLINE",):
        rhs_index += 1
    if rhs_index >= len(tokens):
        return False
    rhs_line = token_line_at(tokens, rhs_index)
    if token_type_at(tokens, rhs_index) not in ("STRING", "STRLIT"):
        return False

    j = rhs_index + 1
    while j < len(tokens) and token_line_at(tokens, j) == rhs_line:
        token_type = token_type_at(tokens, j)
        if token_type in ("NEWLINE", "COMMENT", "HASH_COMMENT"):
            break
        if token_type == "PLUS":
            return False
        return False
    return True


def _check_dealloc_borrowed_strings(self, tokens: List[Tuple]) -> None:
    """Warn on obvious `dealloc` calls against borrowed/static strings.

    This intentionally stays conservative. It catches direct literal frees and
    locals whose current obvious assignment is exactly `x = "literal"`. It does
    not try to prove every borrowed string path; deeper ownership analysis lives
    in the C backend.
    """
    borrowed_literal_vars: Dict[str, Tuple[int, int]] = {}

    for i, (ttype, tval, tline, tcol) in enumerate(tokens):
        if ttype == "DEF":
            borrowed_literal_vars.clear()
            continue
        if ttype == "END":
            borrowed_literal_vars.clear()
            continue

        if ttype == "ASSIGN" and i > 0 and token_type_at(tokens, i - 1) == "IDENT":
            var_name = str(token_text_at(tokens, i - 1))
            if _rhs_is_direct_string_literal(tokens, i):
                borrowed_literal_vars[var_name] = (tline, tcol)
            else:
                borrowed_literal_vars.pop(var_name, None)
            continue

        if ttype != "DEALLOC":
            continue
        if i + 2 >= len(tokens) or token_type_at(tokens, i + 1) != "LPAREN":
            continue
        depth = 1
        expect_target = True
        j = i + 2
        while j < len(tokens) and depth > 0:
            target_type, target_value, target_line, target_col = tokens[j]
            if target_type == "LPAREN":
                depth += 1
                expect_target = False
                j += 1
                continue
            if target_type == "RPAREN":
                depth -= 1
                j += 1
                continue
            if depth == 1 and target_type == "COMMA":
                expect_target = True
                j += 1
                continue
            if depth != 1 or not expect_target:
                j += 1
                continue

            expect_target = False
            if target_type in ("STRING", "STRLIT"):
                self.diagnostics.append(
                    Diagnostic(
                        line=target_line,
                        column=target_col,
                        message="dealloc() called on a string literal",
                        suggestion=(
                            "String literals are borrowed/static. Remove dealloc(), "
                            'or create an owned string first with "" + literal.'
                        ),
                        severity="warning",
                    )
                )
                j += 1
                continue
            if target_type != "IDENT":
                j += 1
                continue
            var_name = str(target_value)
            assignment = borrowed_literal_vars.get(var_name)
            if assignment is None:
                j += 1
                continue
            assign_line, _assign_col = assignment
            self.diagnostics.append(
                Diagnostic(
                    line=tline,
                    column=tcol,
                    message=(
                        f"dealloc({var_name}) may free a borrowed string literal "
                        f"assigned on line {assign_line}"
                    ),
                    suggestion=(
                        "Do not dealloc borrowed literals. If ownership is intended, "
                        f'assign {var_name} = "" + {var_name} or build it through '
                        "concatenation before deallocating."
                    ),
                    severity="warning",
                )
            )
            j += 1


def _check_division_by_zero(self, tokens: List[Tuple]) -> None:
    """Check for division by literal zero."""
    for i, (ttype, _tval, tline, tcol) in enumerate(tokens):
        if ttype in ("DIV", "MOD", "SLASH", "PERCENT") and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            if next_tok[0] == "NUMBER" and next_tok[1] == "0":
                op_name = "Division" if ttype in ("DIV", "SLASH") else "Modulo"
                left_val = "?"
                if i > 0:
                    left_val = str(token_text_at(tokens, i - 1))
                op_symbol = "/" if ttype in ("DIV", "SLASH") else "%"
                self.diagnostics.append(
                    Diagnostic(
                        line=tline,
                        column=tcol,
                        message=f"{op_name} by zero: {left_val} {op_symbol} 0",
                        suggestion="This will crash at runtime - check divisor",
                        severity="error",
                    )
                )


def _check_unused_variables(self, tokens: List[Tuple]) -> None:
    """Check for variables that are assigned but never used."""
    assigned: Dict[str, Tuple[int, int]] = {}  # name -> (line, col)
    used: set = set()

    func_depth = 0

    for i, (ttype, tval, tline, tcol) in enumerate(tokens):
        is_internal_fn = (
            ttype == "IDENT"
            and tval == "internal"
            and i + 3 < len(tokens)
            and token_type_at(tokens, i + 2) == "IDENT"
            and token_type_at(tokens, i + 3) == "LPAREN"
        )
        if ttype == "DEF" or is_internal_fn:
            func_depth += 1
        elif ttype == "END" and func_depth > 0:
            func_depth -= 1

        if ttype != "IDENT":
            continue

        if i > 0 and token_type_at(tokens, i - 1) in ("DOT", "SAFE_DOT"):
            continue

        if i + 1 < len(tokens) and token_type_at(tokens, i + 1) == "ASSIGN":
            if func_depth > 0 and tval not in used:
                assigned[tval] = (tline, tcol)
        else:
            used.add(tval)

    for name, (line, col) in assigned.items():
        if name not in used and name not in CALLABLE_BUILTINS:
            self.diagnostics.append(
                Diagnostic(
                    line=line,
                    column=col,
                    message=f"Variable '{name}' is assigned but never used",
                    suggestion="Remove the variable or use it",
                    severity="hint",
                )
            )


def _check_unused_globals(self, tokens: List[Tuple]) -> None:
    """Check for global constants that are declared but never referenced."""
    if any(ttype == "LIBRARY" for ttype, *_rest in tokens):
        return

    global_consts: Dict[str, Tuple[int, int]] = {}  # name -> (line, col)
    used_names: set = set()
    ignored_tokens = ignored_declarative_token_indices(tokens)
    in_function = False
    depth = 0

    for i, (ttype, tval, _tline, _tcol) in enumerate(tokens):
        if i in ignored_tokens:
            if ttype == "IDENT":
                used_names.add(tval)
            continue
        is_internal_fn = (
            ttype == "IDENT"
            and tval == "internal"
            and i + 3 < len(tokens)
            and token_type_at(tokens, i + 2) == "IDENT"
            and token_type_at(tokens, i + 3) == "LPAREN"
        )
        if ttype == "DEF" or is_internal_fn:
            in_function = True
            depth = 0
        elif ttype == "END":
            if depth > 0:
                depth -= 1
            else:
                in_function = False
        elif ttype in ("IF", "WHILE", "FOR", "FOREACH", "LOOP") and in_function:
            depth += 1

        if not in_function and ttype == "CONST":
            j = i + 1
            if j < len(tokens) and token_type_at(tokens, j) in TYPE_PREFIX_TOKENS:
                j += 1
            if j < len(tokens) and token_type_at(tokens, j) == "IDENT":
                global_consts[token_text_at(tokens, j)] = (
                    token_line_at(tokens, j),
                    token_col_at(tokens, j),
                )

        if not in_function and ttype == "STATIC":
            j = i + 1
            if j < len(tokens) and token_type_at(tokens, j) in TYPE_PREFIX_TOKENS:
                j += 1
            if j < len(tokens) and token_type_at(tokens, j) == "IDENT":
                global_consts[token_text_at(tokens, j)] = (
                    token_line_at(tokens, j),
                    token_col_at(tokens, j),
                )

        if in_function and ttype == "IDENT":
            used_names.add(tval)

    for name, (line, col) in global_consts.items():
        if name not in used_names:
            self.diagnostics.append(
                Diagnostic(
                    line=line,
                    column=col,
                    message=(f"Global constant '{name}' is declared" " but never used"),
                    suggestion="Use it or remove it",
                    severity="hint",
                )
            )


def _check_dead_code(self, tokens: List[Tuple]) -> None:
    """Check for code after unconditional return."""
    continuation_via_prev: set[int] = set()
    prev_sig_line: int = -1
    prev_sig_ttype: Optional[str] = None
    for ttype, _tval, tline, _tcol in tokens:
        if ttype in ("NEWLINE", "COMMENT", "HASH_COMMENT", "SKIP"):
            continue
        if (
            prev_sig_line != -1
            and tline > prev_sig_line
            and prev_sig_ttype in self._CONTINUATION_OPERATORS
        ):
            continuation_via_prev.add(tline)
        prev_sig_line = tline
        prev_sig_ttype = ttype

    in_function = False
    seen_return = False
    return_line = 0
    depth = 0  # Track nesting depth (block-level: if/while/for/etc.)
    continuation_line = -1
    return_bracket_depth = 0

    for _i, (ttype, _tval, tline, tcol) in enumerate(tokens):
        if ttype == "DEF":
            in_function = True
            seen_return = False
            depth = 0
        elif ttype == "END":
            if depth > 0:
                depth -= 1
                seen_return = False
            else:
                in_function = False
                seen_return = False
        elif ttype in (
            "IF",
            "WHILE",
            "FOR",
            "FOREACH",
            "LOOP",
            "MATCH",
            "TRY",
        ):
            depth += 1
            seen_return = False  # Reset when entering a block
        elif ttype == "CASE":
            seen_return = False
        elif ttype == "RETURN" and in_function and depth == 0:
            seen_return = True
            return_line = tline
            continuation_line = -1  # No active continuation
            return_bracket_depth = 0  # Reset for new return expr
        elif seen_return and in_function and depth == 0:
            if ttype in ("LPAREN", "LBRACKET", "LBRACE"):
                return_bracket_depth += 1
            elif ttype in ("RPAREN", "RBRACKET", "RBRACE") and return_bracket_depth > 0:
                return_bracket_depth -= 1

            if tline > return_line and ttype not in (
                "END",
                "NEWLINE",
                "COMMENT",
            ):
                if return_bracket_depth > 0:
                    continuation_line = tline
                elif ttype in RETURN_CONTINUATION_TOKENS:
                    continuation_line = tline
                elif tline in continuation_via_prev:
                    continuation_line = tline
                elif tline == continuation_line:
                    pass
                else:
                    self.diagnostics.append(
                        Diagnostic(
                            line=tline,
                            column=tcol,
                            message="Unreachable code after return",
                            suggestion=f"Code after return on line "
                            f"{return_line} will never execute",
                            severity="warning",
                        )
                    )
                    seen_return = False  # Only report once per return


def _check_infinite_loops(self, tokens: List[Tuple]) -> None:
    """Check for obvious infinite loops."""
    loop_tokens = ("WHILE", "FOR", "FOREACH", "LOOP", "REPEAT")
    block_tokens = ("IF", "MATCH", "TRY")

    for i, (ttype, _tval, tline, tcol) in enumerate(tokens):
        if ttype == "WHILE" and (
            i + 1 < len(tokens) and token_text_at(tokens, i + 1) == "true"
        ):
            has_exit = False
            loop_depth = 1
            block_depth = 0
            j = i + 2
            while j < len(tokens) and loop_depth > 0:
                t = token_type_at(tokens, j)
                if t in loop_tokens:
                    loop_depth += 1
                elif t in block_tokens:
                    block_depth += 1
                elif t == "END":
                    if block_depth > 0:
                        block_depth -= 1
                    else:
                        loop_depth -= 1
                elif t in ("BREAK", "RETURN") and loop_depth == 1:
                    has_exit = True
                    break
                j += 1

            if not has_exit:
                self.diagnostics.append(
                    Diagnostic(
                        line=tline,
                        column=tcol,
                        message="Potential infinite loop: 'while true' without break",
                        suggestion="Add a 'break' or 'return' to exit the loop",
                        severity="warning",
                    )
                )

        if ttype == "LOOP":
            has_exit = False
            loop_depth = 1
            block_depth = 0
            j = i + 1
            while j < len(tokens) and loop_depth > 0:
                t = token_type_at(tokens, j)
                if t in loop_tokens:
                    loop_depth += 1
                elif t in block_tokens:
                    block_depth += 1
                elif t == "END":
                    if block_depth > 0:
                        block_depth -= 1
                    else:
                        loop_depth -= 1
                elif t in ("BREAK", "RETURN") and loop_depth == 1:
                    has_exit = True
                    break
                j += 1

            if not has_exit:
                self.diagnostics.append(
                    Diagnostic(
                        line=tline,
                        column=tcol,
                        message="Potential infinite loop: 'loop' without break",
                        suggestion="Add a 'break' or 'return' to exit the loop",
                        severity="warning",
                    )
                )


def _check_concurrency_hints(self, tokens: List[Tuple]) -> None:
    """Detect concurrency patterns and suggest --analyze for race detection."""
    spawn_line = 0
    spawn_col = 0
    has_spawn = False
    has_shared_writes = False

    assigned_vars: set = set()
    in_function = False
    for i, (ttype, _tval, tline, tcol) in enumerate(tokens):
        if ttype == "SPAWN":
            has_spawn = True
            if spawn_line == 0:
                spawn_line = tline
                spawn_col = tcol
        if ttype == "DEF":
            in_function = True
        if ttype == "END":
            in_function = False
        if ttype == "ASSIGN" and i > 0 and token_type_at(tokens, i - 1) == "IDENT":
            var_name = token_text_at(tokens, i - 1)
            if in_function and var_name in assigned_vars:
                has_shared_writes = True
            if not in_function:
                assigned_vars.add(var_name)

    if has_spawn and has_shared_writes:
        self.diagnostics.append(
            Diagnostic(
                line=spawn_line,
                column=spawn_col,
                message=(
                    "Concurrent code with shared state detected — "
                    "consider running with --analyze for race detection"
                ),
                suggestion="Run: python ailang.py <file> --analyze",
                severity="hint",
            )
        )

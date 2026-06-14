"""FFI/ABI semantic checks for DiagnosticEngine."""

from __future__ import annotations

from typing import List, Set, Tuple

from token_access import token_text_at, token_type_at

from .diagnostics_models import Diagnostic


def _check_opaque_record_by_value_use(
    self, source: str, tokens: List[Tuple[str, str, int, int]]
) -> None:
    """Reject layoutless C records inside owned records/unions.

    `opaque record T` and `extern record T` without imported layout metadata are
    pointer-only FFI handles. They may be passed around as AILang values because
    backends lower them as pointers. Embedding them in an owned record/union
    would require a by-value C layout the compiler does not know.
    """
    layoutless = _collect_layoutless_c_record_names(
        source, getattr(self, "filepath", "")
    )
    if not layoutless:
        return

    reported: Set[Tuple[int, int, str]] = set()
    i = 0
    while i < len(tokens):
        token_type = token_type_at(tokens, i)
        if token_type not in ("RECORD", "UNION"):
            i += 1
            continue

        if i > 0 and token_type_at(tokens, i - 1) in ("OPAQUE", "EXTERN"):
            i += 1
            continue

        owner_kind = "record" if token_type == "RECORD" else "union"
        owner_name = (
            token_text_at(tokens, i + 1) if i + 1 < len(tokens) else "<anonymous>"
        )
        j = i + 2
        while j < len(tokens):
            field_token_type, field_token_text, line, col = tokens[j]
            if field_token_type == "END":
                break
            if field_token_type == "IDENT" and field_token_text in layoutless:
                key = (line, col, field_token_text)
                if key not in reported:
                    reported.add(key)
                    self.diagnostics.append(
                        Diagnostic(
                            line=line,
                            column=col,
                            message=(
                                f"Opaque C record '{field_token_text}' has no known "
                                f"by-value layout and cannot be embedded in "
                                f"{owner_kind} '{owner_name}'"
                            ),
                            suggestion=(
                                "Store it as ptr, or import a concrete extern record "
                                "layout before embedding it."
                            ),
                            severity="error",
                        )
                    )
            j += 1

        i = j + 1


def _collect_layoutless_c_record_names(source: str, filepath: str) -> Set[str]:
    """Return opaque/extern record names that cannot be used by value."""
    try:
        from parser import ast as A
        from parser.parser import Parser

        from lexer.scan import tokenize as lexer_tokenize

        parsed = Parser(lexer_tokenize(source)).parse_program()
        if filepath:
            from transpiler.import_resolver import ImportResolver

            parsed = ImportResolver().run(parsed, filepath)
    except (OSError, RuntimeError, SyntaxError, ValueError, TypeError):
        return set()

    names: Set[str] = set()
    for node in parsed:
        if not isinstance(node, A.ExternRecordDef):
            continue
        if getattr(node, "is_opaque", False):
            names.add(str(getattr(node, "name", "")))
        elif getattr(node, "layout_size", None) is None:
            names.add(str(getattr(node, "name", "")))
    return {name for name in names if name}

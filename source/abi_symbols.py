"""Helpers for AILang C/LLVM ABI symbol names."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

_C_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_C_ABI_FORBIDDEN_RE = re.compile(r"[;{}#\r\n]")


def _decorator_texts(decorators: Iterable[object] | None) -> list[str]:
    if decorators is None:
        return []
    return [str(item).lstrip("@").strip() for item in decorators]


def is_export_decorator(decorator: object) -> bool:
    text = str(decorator).lstrip("@").strip().lower()
    return text == "export" or text.startswith("export(")


def has_export_decorator(decorators: Iterable[object] | None) -> bool:
    return any(is_export_decorator(item) for item in _decorator_texts(decorators))


def explicit_export_symbol(decorators: Iterable[object] | None) -> str | None:
    """Return the explicit C symbol from @export("name"), if present."""

    for raw in _decorator_texts(decorators):
        lower = raw.lower()
        if not lower.startswith("export(") or not raw.endswith(")"):
            continue
        symbol = raw[raw.find("(") + 1 : -1].strip()
        if (symbol.startswith('"') and symbol.endswith('"')) or (
            symbol.startswith("'") and symbol.endswith("'")
        ):
            symbol = symbol[1:-1]
        if not symbol:
            raise ValueError("@export(...) requires a non-empty C symbol name")
        if not _C_IDENTIFIER_RE.match(symbol):
            raise ValueError(f"invalid @export C symbol: {symbol!r}")
        return symbol
    return None


def c_symbol_for_function(
    name: str,
    decorators: Iterable[object] | None,
    default_mangle: Callable[[str], str],
) -> str:
    return explicit_export_symbol(decorators) or default_mangle(name)


def explicit_c_abi_parts(
    decorators: Iterable[object] | None,
) -> tuple[str, list[str]] | None:
    """Return explicit C ABI declaration parts from @abi(...), if present.

    Syntax:
        @abi("size_t", "char * dst", "const char * src", "size_t dstsize")

    The function symbol still comes from @export/default mangling.  This keeps
    C spelling at the ABI boundary instead of turning C names into normal
    AILang types.
    """

    for raw in _decorator_texts(decorators):
        lower = raw.lower()
        if not (
            (
                lower.startswith("abi(")
                or lower.startswith("cabi(")
                or lower.startswith("c_abi(")
            )
            and raw.endswith(")")
        ):
            continue
        body = raw[raw.find("(") + 1 : -1].strip()
        if not body:
            raise ValueError("@abi(...) requires at least a return type")
        parts = [part.strip() for part in body.split(",")]
        if not parts or not parts[0]:
            raise ValueError("@abi(...) requires a non-empty return type")
        for part in parts:
            if not part or _C_ABI_FORBIDDEN_RE.search(part):
                raise ValueError(f"invalid @abi declaration part: {part!r}")
        return_type = parts[0]
        params = parts[1:] if len(parts) > 1 else ["void"]
        return return_type, params
    return None

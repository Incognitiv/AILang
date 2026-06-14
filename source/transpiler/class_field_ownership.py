"""Helpers for ownership-aware class field cleanup in the C backend."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from .drop_plan import (
    drop_kind_for_type,
)
from .drop_plan import (
    expr_preserves_field_storage as _drop_expr_preserves_field_storage,
)
from .drop_plan import expr_produces_owned_value as _drop_expr_produces_owned_value
from .drop_plan import field_type_text as _drop_field_type_text
from .drop_plan import normalized_type_text as _drop_normalized_type_text


def owned_field_flag_name(field_name: str) -> str:
    """Return the hidden C field storing ownership for a class field."""
    return f"__ailang_{field_name}_owned"


def owned_param_flag_name(param_name: str) -> str:
    """Return the hidden C parameter/local flag for transferred ownership."""
    return f"__ailang_{param_name}_owned"


def string_len_field_name(field_name: str) -> str:
    """Return the hidden C field storing the cached byte length for a string field."""
    return f"__ailang_{field_name}_len"


def string_len_param_name(param_name: str) -> str:
    """Return the hidden C parameter storing the byte length for a string argument."""
    return f"__ailang_{param_name}_len"


def is_string_type(field_type: Any) -> bool:
    """True for AILang string type spellings."""
    return normalized_type_text(field_type) in {"string", "str"}


def field_type_text(field_type: Any) -> str:
    """Return a normalized-but-readable AILang type string."""
    return _drop_field_type_text(field_type)


def normalized_type_text(field_type: Any) -> str:
    """Return lowercase whitespace-free type text for classification."""
    return _drop_normalized_type_text(field_type)


def auto_owned_field_kind(
    field_type: Any,
    classes: dict[str, Any] | None = None,
) -> str | None:
    """Return the compiler-owned cleanup kind for a class field type.

    Only types with deterministic C-backend ownership semantics are included.
    Raw pointers and OS resources intentionally stay manual.
    """
    kind = drop_kind_for_type(field_type, classes)
    return kind.value if kind is not None else None


def is_auto_owned_field_type(
    field_type: Any,
    classes: dict[str, Any] | None = None,
) -> bool:
    """True when a field can use hidden ownership tracking."""
    return auto_owned_field_kind(field_type, classes) is not None


def is_auto_owned_string_field_type(field_type: Any) -> bool:
    """Compatibility wrapper for string-only call sites/tests."""
    return auto_owned_field_kind(field_type) == "string"


def is_auto_owned_param(
    param: Any,
    classes: dict[str, Any] | None = None,
) -> bool:
    """True when a method parameter needs a hidden ownership flag."""
    return (
        isinstance(param, tuple)
        and len(param) >= 2
        and is_auto_owned_field_type(param[1], classes)
    )


def is_auto_owned_string_param(param: Any) -> bool:
    """Compatibility wrapper for string-only call sites/tests."""
    return is_auto_owned_param(param) and auto_owned_field_kind(param[1]) == "string"


def param_name(param: Any) -> str:
    """Return a stable parameter name from parsed parameter shapes."""
    if isinstance(param, tuple) and param:
        return str(param[0])
    return str(param)


def auto_owned_fields(
    class_info: Any,
    classes: dict[str, Any] | None = None,
) -> list[tuple[str, Any, str]]:
    """Return `(field_name, field_type, kind)` entries eligible for cleanup."""
    if not class_info:
        return []
    fields = class_info[0] if isinstance(class_info, tuple) else []
    result: list[tuple[str, Any, str]] = []
    for field in fields:
        if not isinstance(field, tuple) or len(field) < 3:
            continue
        _visibility, field_name, field_type = field[:3]
        kind = auto_owned_field_kind(field_type, classes)
        if kind is not None:
            result.append((str(field_name), field_type, kind))
    return result


def auto_owned_field_names(
    class_info: Any,
    classes: dict[str, Any] | None = None,
) -> set[str]:
    """Return field names eligible for hidden ownership tracking."""
    return {name for name, _field_type, _kind in auto_owned_fields(class_info, classes)}


def auto_owned_string_fields(class_info: Any) -> list[tuple[str, Any]]:
    """Compatibility wrapper for string-only field enumeration."""
    return [
        (name, field_type)
        for name, field_type, kind in auto_owned_fields(class_info)
        if kind == "string"
    ]


def auto_owned_string_field_names(class_info: Any) -> set[str]:
    """Compatibility wrapper for string-only field names."""
    return {name for name, _field_type in auto_owned_string_fields(class_info)}


def _call_name(expr: A.ASTNode) -> str | None:
    return expr.name if isinstance(expr, A.Call) else None


def _same_field_access(expr: A.ASTNode, owner_expr: A.ASTNode, field_name: str) -> bool:
    if not isinstance(expr, A.FieldAccess):
        return False
    if expr.field_name != field_name:
        return False
    if isinstance(expr.object_expr, A.ThisExpr) and isinstance(owner_expr, A.ThisExpr):
        return True
    if isinstance(expr.object_expr, A.Variable) and isinstance(owner_expr, A.Variable):
        return expr.object_expr.name == owner_expr.name
    return False


def expr_produces_owned_value(
    expr: A.ASTNode,
    kind: str,
    field_type: Any,
    classes: dict[str, Any] | None,
    is_owned_string_alloc: Any,
) -> bool:
    """Return whether `expr` creates/returns ownership for an ownable kind."""
    return _drop_expr_produces_owned_value(
        expr, kind, field_type, classes, is_owned_string_alloc
    )


def expr_preserves_field_storage(
    expr: A.ASTNode,
    owner_expr: A.ASTNode,
    field_name: str,
    kind: str,
) -> bool:
    """True for self-mutating field updates that must not pre-free old storage."""
    return _drop_expr_preserves_field_storage(expr, owner_expr, field_name, kind)

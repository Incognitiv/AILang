"""Backend-neutral resource drop metadata for AILang lowering.

This module is intentionally small: it classifies owned fields and derives
constructor-time cleanup plans without knowing how a backend emits the cleanup.
Backends should consume the resulting DropPlan instead of duplicating ownership
heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any, Callable, Iterable

from ast_access import arg_at


class DropKind(str, Enum):
    TRIVIAL = "trivial"
    OWNED_STRING = "string"
    DYNAMIC_ARRAY = "array"
    STR_ARRAY = "str_array"
    DICT = "dict"
    CLASS_VALUE = "class"


@dataclass(frozen=True)
class DropFieldPlan:
    name: str
    type_spec: Any
    kind: DropKind


@dataclass(frozen=True)
class DropPlan:
    type_name: str
    fields: tuple[DropFieldPlan, ...] = ()
    user_destructor: str | None = None
    free_storage: bool = False

    def field_names(self, kind: DropKind | str | None = None) -> set[str]:
        if kind is None:
            return {field.name for field in self.fields}
        wanted = _coerce_kind(kind)
        return {field.name for field in self.fields if field.kind == wanted}


def field_type_text(field_type: Any) -> str:
    """Return a normalized-but-readable AILang type string."""
    try:
        return parsed_type_to_str(field_type)
    except (TypeError, ValueError):
        return str(field_type)


def normalized_type_text(field_type: Any) -> str:
    """Return lowercase whitespace-free type text for classification."""
    return field_type_text(field_type).strip().lower().replace(" ", "")


def _coerce_kind(kind: DropKind | str | None) -> DropKind | None:
    if kind is None:
        return None
    if isinstance(kind, DropKind):
        return kind
    try:
        return DropKind(kind)
    except ValueError:
        return None


def drop_kind_for_type(
    field_type: Any,
    classes: dict[str, Any] | None = None,
) -> DropKind | None:
    """Classify deterministic compiler-owned cleanup for a field type."""
    text = normalized_type_text(field_type)
    if text in {"string", "str"}:
        return DropKind.OWNED_STRING
    if text == "array":
        return DropKind.DYNAMIC_ARRAY
    if text == "str_array":
        return DropKind.STR_ARRAY
    if text == "dict":
        return DropKind.DICT
    class_names = set(classes or {})
    raw = field_type_text(field_type).strip()
    if raw in class_names:
        return DropKind.CLASS_VALUE
    return None


def auto_owned_fields(
    class_info: Any,
    classes: dict[str, Any] | None = None,
) -> list[DropFieldPlan]:
    """Return fields that can use compiler-owned cleanup metadata."""
    if not class_info:
        return []
    fields = class_info[0] if isinstance(class_info, tuple) else []
    result: list[DropFieldPlan] = []
    for field in fields:
        if not isinstance(field, tuple) or len(field) < 3:
            continue
        _visibility, field_name, field_type = field[:3]
        kind = drop_kind_for_type(field_type, classes)
        if kind is not None:
            result.append(DropFieldPlan(str(field_name), field_type, kind))
    return result


def declared_field_plans(
    fields: Iterable[tuple[str, Any]],
    classes: dict[str, Any] | None = None,
) -> list[DropFieldPlan]:
    """Classify `(name, type)` field declarations in declaration order."""
    plans: list[DropFieldPlan] = []
    for field_name, field_type in fields:
        kind = drop_kind_for_type(field_type, classes)
        if kind is not None:
            plans.append(DropFieldPlan(str(field_name), field_type, kind))
    return plans


def _default_owned_string_alloc(expr: A.ASTNode) -> bool:
    if isinstance(expr, A.BinaryOp) and expr.op.lower() in {"+", "plus"}:
        return True
    if isinstance(expr, A.InterpolatedString):
        return True
    if isinstance(expr, A.Call) and expr.name in {
        "str",
        "chr",
        "substr",
        "concat",
        "str_replace",
    }:
        return True
    return False


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
    kind: DropKind | str,
    field_type: Any,
    classes: dict[str, Any] | None = None,
    is_owned_string_alloc: Callable[[A.ASTNode], bool] | None = None,
) -> bool:
    """Return whether `expr` creates/returns ownership for an ownable kind."""
    coerced = _coerce_kind(kind)
    if coerced is None:
        return False
    if coerced == DropKind.OWNED_STRING:
        checker = is_owned_string_alloc or _default_owned_string_alloc
        return bool(checker(expr))
    if coerced == DropKind.CLASS_VALUE:
        return (
            isinstance(expr, A.NewExpr)
            and expr.type_name == field_type_text(field_type).strip()
        )
    if coerced == DropKind.DYNAMIC_ARRAY:
        if isinstance(expr, A.Call) and expr.name == "array_new":
            return True
        if isinstance(expr, A.Call) and expr.name in {"array_push", "array_set"}:
            return bool(expr.args) and expr_produces_owned_value(
                arg_at(expr, 0), coerced, field_type, classes, is_owned_string_alloc
            )
        return False
    if coerced == DropKind.STR_ARRAY:
        if isinstance(expr, A.Call) and expr.name == "str_array_new":
            return True
        if isinstance(expr, A.Call) and expr.name == "str_array_push":
            return bool(expr.args) and expr_produces_owned_value(
                arg_at(expr, 0), coerced, field_type, classes, is_owned_string_alloc
            )
        return False
    if coerced == DropKind.DICT:
        return isinstance(expr, A.DictLit) or _call_name(expr) == "dict_new"
    return False


def expr_preserves_field_storage(
    expr: A.ASTNode,
    owner_expr: A.ASTNode,
    field_name: str,
    kind: DropKind | str,
) -> bool:
    """True for self-mutating field updates that must not pre-free old storage."""
    coerced = _coerce_kind(kind)
    if not isinstance(expr, A.Call) or not expr.args:
        return False
    if coerced == DropKind.DYNAMIC_ARRAY and expr.name not in {
        "array_push",
        "array_set",
    }:
        return False
    if coerced == DropKind.STR_ARRAY and expr.name != "str_array_push":
        return False
    if coerced not in {DropKind.DYNAMIC_ARRAY, DropKind.STR_ARRAY}:
        return False
    return _same_field_access(arg_at(expr, 0), owner_expr, field_name)


def constructor_field_drop_plan(
    class_name: str,
    new_expr: A.NewExpr,
    class_methods: dict[str, list[Any]],
    record_fields: dict[str, list[tuple[str, Any]]],
    classes: dict[str, Any] | None = None,
    is_owned_string_alloc: Callable[[A.ASTNode], bool] | None = None,
    is_materialized_constructor_arg: (
        Callable[[str, str, int, A.ASTNode, DropKind, Any], bool] | None
    ) = None,
) -> DropPlan:
    """Derive which stack-lowered class fields own resources after construction.

    The plan is conservative. It only marks fields whose ownership is clear from
    constructor arguments or simple `this.field = ...` init assignments.
    """
    fields = record_fields.get(class_name, [])
    field_types = {name: field_type for name, field_type in fields}
    owned_names: set[str] = set()

    init_method = None
    for method in class_methods.get(class_name, []):
        if getattr(method, "name", None) == "init":
            init_method = method
            break

    if init_method is None:
        for index, ((field_name, field_type), arg) in enumerate(
            zip(fields, new_expr.args, strict=False)
        ):
            kind = drop_kind_for_type(field_type, classes)
            if kind is None:
                continue
            if is_materialized_constructor_arg is not None:
                owns_arg = is_materialized_constructor_arg(
                    class_name, "", index, arg, kind, field_type
                )
            else:
                owns_arg = expr_produces_owned_value(
                    arg, kind, field_type, classes, is_owned_string_alloc
                )
            if owns_arg:
                owned_names.add(field_name)
        return _plan_from_owned_names(class_name, fields, owned_names, classes)

    owned_params: dict[str, DropKind] = {}
    for index, (param, arg) in enumerate(
        zip(init_method.params, new_expr.args, strict=False)
    ):
        if len(param) < 2:
            continue
        pname, ptype = str(param[0]), param[1]
        kind = drop_kind_for_type(ptype, classes)
        if kind is None:
            continue
        if is_materialized_constructor_arg is not None:
            owns_arg = is_materialized_constructor_arg(
                class_name, "init", index, arg, kind, ptype
            )
        else:
            owns_arg = expr_produces_owned_value(
                arg, kind, ptype, classes, is_owned_string_alloc
            )
        if owns_arg:
            owned_params[pname] = kind

    for stmt in getattr(init_method, "body", []):
        if not isinstance(stmt, A.FieldAssign):
            continue
        if not isinstance(stmt.object_expr, A.ThisExpr):
            continue
        field_name = stmt.field_name
        field_type = field_types.get(field_name)
        if field_type is None:
            continue
        kind = drop_kind_for_type(field_type, classes)
        if kind is None:
            continue
        value = stmt.value
        if isinstance(value, A.Variable) and owned_params.get(value.name) == kind:
            owned_names.add(field_name)
            continue
        if expr_produces_owned_value(
            value, kind, field_type, classes, is_owned_string_alloc
        ):
            owned_names.add(field_name)
            continue
        if field_name in owned_names and expr_preserves_field_storage(
            value, A.ThisExpr(), field_name, kind
        ):
            owned_names.add(field_name)

    return _plan_from_owned_names(class_name, fields, owned_names, classes)


def _plan_from_owned_names(
    class_name: str,
    fields: list[tuple[str, Any]],
    owned_names: set[str],
    classes: dict[str, Any] | None,
) -> DropPlan:
    field_plans: list[DropFieldPlan] = []
    for field_name, field_type in fields:
        if field_name not in owned_names:
            continue
        kind = drop_kind_for_type(field_type, classes)
        if kind is not None:
            field_plans.append(DropFieldPlan(field_name, field_type, kind))
    return DropPlan(class_name, tuple(field_plans))

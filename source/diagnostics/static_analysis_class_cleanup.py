"""Class cleanup hardening helpers for static analysis."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from diagnostics.static_analysis_models import AnalysisWarning
from transpiler.class_field_ownership import auto_owned_field_kind

# Field types that typically own heap-backed/runtime-managed resources and
# therefore need explicit destructor cleanup when embedded in class instances.
_OWNED_TYPE_EXACT: set[str] = {
    "array",
    "dict",
    "file",
    "handle",
    "map",
    "set",
    "socket",
    "sqlite",
    "sqlite3",
    "stmt",
}
_OWNED_TYPE_MARKERS: tuple[str, ...] = (
    "array",
    "dict",
    "file",
    "socket",
    "sqlite",
    "channel",
    "thread",
    "mutex",
    "rwlock",
    "cond",
    "buffer",
    "arena",
)
_NON_OWNING_PREFIXES: tuple[str, ...] = ("slice[", "view[")


def _check_class_cleanup_contract(self, ast_nodes: list[A.ASTNode]) -> None:
    """Warn when likely-owned class fields are not addressed by `~ClassName`.

    AILang's default class cleanup frees the object itself at scope exit, but
    it does not recursively clean fields. For classes containing likely-owned
    resources (string/dict/dynamic array/file/socket/etc.), require explicit
    destructor intent and require that the destructor touches those fields.
    """
    for node in ast_nodes:
        if not isinstance(node, A.ClassDef):
            continue
        class_names = {
            class_node.name
            for class_node in ast_nodes
            if isinstance(class_node, A.ClassDef)
        }

        risky_fields: list[tuple[str, str]] = []
        for field in node.fields:
            field_name, field_type = self._extract_class_field_spec(field)
            if not field_name:
                continue
            if auto_owned_field_kind(field_type, {name: None for name in class_names}):
                continue
            if self._looks_owned_resource_type(field_type):
                risky_fields.append((field_name, field_type))

        if not risky_fields:
            continue

        destructor = next(
            (
                method
                for method in node.methods
                if isinstance(method, A.Function) and method.name.startswith("~")
            ),
            None,
        )
        if destructor is None:
            preview = _format_class_cleanup_field_preview(self, risky_fields)
            self.warnings.append(
                AnalysisWarning(
                    line=max(1, int(getattr(node, "line", 1))),
                    column=max(1, int(getattr(node, "col", 1))),
                    category="class-cleanup",
                    message=(
                        f"Class '{node.name}' has no destructor but holds likely-owned "
                        f"resource field(s): {preview}"
                    ),
                    suggestion=(
                        f"Add `~{node.name}()` and release owned fields explicitly. "
                        "Default class cleanup is shallow (no per-field teardown)."
                    ),
                    severity="warning",
                )
            )
            continue

        touched_fields = _destructor_touched_fields(self, destructor)
        untouched = [
            (name, field_type)
            for name, field_type in risky_fields
            if name not in touched_fields
        ]
        if not untouched:
            continue

        preview = _format_class_cleanup_field_preview(self, untouched)
        self.warnings.append(
            AnalysisWarning(
                line=max(1, int(getattr(node, "line", 1))),
                column=max(1, int(getattr(node, "col", 1))),
                category="class-cleanup",
                message=(
                    f"Class '{node.name}' destructor does not reference likely-owned "
                    f"resource field(s): {preview}"
                ),
                suggestion=(
                    f"Update `~{node.name}()` to release or intentionally neutralize "
                    "those fields. A cosmetic empty destructor is not sufficient."
                ),
                severity="warning",
            )
        )


def _format_class_cleanup_field_preview(self, fields: list[tuple[str, str]]) -> str:
    """Return compact `name:type` preview for cleanup diagnostics."""
    preview = ", ".join(f"{name}:{ftype}" for name, ftype in fields[:3])
    if len(fields) > 3:
        preview = f"{preview}, +{len(fields) - 3} more"
    return preview


def _destructor_touched_fields(self, destructor: A.Function) -> set[str]:
    """Return `this.field` names referenced by a destructor body.

    This is intentionally conservative. A destructor that references the field
    is treated as explicit user intent; a destructor that never mentions a
    likely-owned field remains suspicious.
    """
    touched: set[str] = set()

    def walk(value: Any) -> None:
        if value is None or isinstance(value, (str, int, bool, float)):
            return
        if isinstance(value, tuple):
            for item in value:
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, (A.FieldAccess, A.SafeFieldAccess, A.FieldAssign)):
            object_expr = getattr(value, "object_expr", None)
            if isinstance(object_expr, A.ThisExpr):
                touched.add(str(getattr(value, "field_name", "")))
            walk(object_expr)
            walk(getattr(value, "value", None))
            return
        for attr in (
            "value",
            "left",
            "right",
            "cond",
            "condition",
            "true_expr",
            "false_expr",
            "object_expr",
            "array",
            "index",
            "expr",
            "iterable",
            "init",
            "step",
            "init_value",
            "body",
            "then_body",
            "else_body",
            "try_body",
            "finally_body",
            "args",
            "elements",
            "parts",
        ):
            walk(getattr(value, attr, None))
        if isinstance(value, A.TryExcept):
            for _error_type, _var_name, catch_body in value.catch_blocks:
                walk(catch_body)
            if value.except_block:
                _error_var, except_body = value.except_block
                walk(except_body)
        elsif = getattr(value, "elsif_branches", None)
        if elsif:
            for elsif_cond, branch in elsif:
                walk(elsif_cond)
                walk(branch)

    walk(destructor.body)
    return {name for name in touched if name}


def _extract_class_field_spec(self, field: Any) -> tuple[str, str]:
    """Return `(field_name, canonical_field_type)` for class-field tuples."""
    if isinstance(field, tuple):
        # Canonical parsed shape for class fields:
        # (visibility, field_name, field_type, init_value)
        if len(field) >= 3:
            field_name = str(field[1])
            raw_type = field[2]
            return field_name, self._field_type_to_text(raw_type)
        if len(field) >= 2:
            field_name = str(field[0])
            raw_type = field[1]
            return field_name, self._field_type_to_text(raw_type)
    return "", ""


def _field_type_to_text(self, raw_type: Any) -> str:
    """Convert parsed field type to stable textual form."""
    if isinstance(raw_type, str):
        return raw_type
    try:
        return A.parsed_type_to_str(raw_type)
    except (TypeError, ValueError):
        return str(raw_type)


def _looks_owned_resource_type(self, field_type: str) -> bool:
    """Heuristic: classify field types likely requiring destructor cleanup."""
    if not field_type:
        return False

    norm = field_type.strip().lower().replace(" ", "")
    if not norm:
        return False

    if norm.startswith(_NON_OWNING_PREFIXES):
        return False

    # Parsed dynamic array notation: [T]
    if norm.startswith("[") and norm.endswith("]") and ";" not in norm:
        return True

    if norm.endswith("*") or norm.startswith("ptr[") or norm.startswith("ptr<"):
        return True

    if norm in _OWNED_TYPE_EXACT:
        return True

    return any(marker in norm for marker in _OWNED_TYPE_MARKERS)

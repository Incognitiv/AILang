"""Ownership-aware local reassignment lowering."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from ast_access import arg_at
from transpiler.fixed_int_types import info_for_c_fixed
from transpiler.strlen_assign_cache import emit_length_only_string_reassign

from .stmt_stack_class_construct import _emit_stack_class_construct


def _emit_dyn_array_push_in_place(
    self: Any,
    target: str,
    value_code: str,
    value_kind: str = "int",
    value_node: A.ASTNode | None = None,
) -> None:
    """Append directly into an ``ailang_dyn_array`` lvalue."""
    if value_kind == "class_ptr":
        value = f"(int64_t)(uintptr_t)({value_code})"
    else:
        value = value_code
        if value_node is not None:
            fixed = info_for_c_fixed(self._infer_type(value_node))
            if fixed is not None and fixed.bits > 64:
                value = f"ailang_narrow_i64_{fixed.canonical}({value})"
    self.emit("{")
    self.emit(f"  if ({target}.length >= {target}.capacity) {{")
    self.emit(f"    {target}.capacity = {target}.capacity ? {target}.capacity * 2 : 4;")
    self.emit(
        f"    {target}.data = (int64_t*)ailang_safe_realloc({target}.data, "
        f"(size_t){target}.capacity * sizeof(int64_t));"
    )
    self.emit("  }")
    self.emit(f"  {target}.data[{target}.length++] = {value};")
    self.emit("}")


def _try_emit_string_reassign(
    self: Any, var_name: str, value: A.ASTNode, val: str, var: str
) -> bool | None:
    if var_name in self._tracked_owned_string_locals:
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_safe_free((void *)(uintptr_t)({var}));")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name not in self._mixed_ownership_string_locals:
        return None
    rhs_owned = 1 if self._is_owned_string_alloc(value) else 0
    flag = self._mixed_owned_flag(var_name)
    self.emit(f"{{ typeof({var}) __pre_assign = {val};")
    self.emit(f"  if ({flag}) ailang_safe_free((void *)(uintptr_t)({var}));")
    self.emit(f"  {var} = __pre_assign;")
    self.emit(f"  {flag} = {rhs_owned}; }}")
    return True


def _try_emit_class_reassign(
    self: Any, var_name: str, value: A.ASTNode, val: str, var: str
) -> bool | None:
    stack_classes = getattr(self, "_stack_owned_class_locals", None) or {}
    if var_name in stack_classes and isinstance(value, A.NewExpr):
        if _emit_stack_class_construct(self, var_name, stack_classes[var_name], value):
            return True
    if var_name not in self._tracked_owned_class_locals:
        return None
    cls = self._tracked_owned_class_locals[var_name]
    self.emit(f"{{ typeof({var}) __pre_assign = {val};")
    self.emit(
        f"  if ({var}) {{ {cls}_destructor({var}); " f"ailang_safe_free({var}); }}"
    )
    self.emit(f"  {var} = __pre_assign; }}")
    return True


def _try_emit_simple_array_reassign(
    self: Any, var_name: str, val: str, var: str
) -> bool | None:
    if var_name in (getattr(self, "_str_array_locals_for_cleanup", None) or []):
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_str_array_free(&{var});")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name not in (getattr(self, "_int_array_locals_for_cleanup", None) or []):
        return None
    self.emit(f"{{ typeof({var}) __pre_assign = {val};")
    self.emit(f"  ailang_int_array_free(&{var});")
    self.emit(f"  {var} = __pre_assign; }}")
    return True


def _is_self_array_call(value: A.ASTNode, var_name: str, call_name: str) -> bool:
    if not isinstance(value, A.Call) or value.name != call_name or not value.args:
        return False
    first_arg = arg_at(value, 0)
    return isinstance(first_arg, A.Variable) and first_arg.name == var_name


def _try_emit_dyn_array_reassign(
    self: Any, var_name: str, value: A.ASTNode, val: str, var: str
) -> bool | None:
    tracked = getattr(self, "_dyn_array_locals_for_cleanup", None) or []
    if var_name not in tracked:
        return None
    if isinstance(value, A.Call) and _is_self_array_call(value, var_name, "array_push"):
        value_kind = (
            "class_ptr"
            if len(value.args) >= 2
            and self._class_ptr_type(arg_at(value, 1)) is not None
            else "int"
        )
        push_node = arg_at(value, 1)
        _emit_dyn_array_push_in_place(
            self, var, self.expr(push_node), value_kind, push_node
        )
        return True
    if _is_self_array_call(value, var_name, "array_set"):
        return False
    self.emit(f"{{ typeof({var}) __pre_assign = {val};")
    self.emit(f"  ailang_dyn_array_free(&{var});")
    self.emit(f"  {var} = __pre_assign; }}")
    return True


def _try_emit_light_str_array_reassign(
    self: Any, var_name: str, value: A.ASTNode, val: str, var: str
) -> bool | None:
    tracked = getattr(self, "_lc_str_array_locals_for_cleanup", None) or []
    if var_name not in tracked:
        return None
    if _is_self_array_call(value, var_name, "str_array_push"):
        return False
    self.emit(f"{{ typeof({var}) __pre_assign = {val};")
    self.emit(f"  ailang_str_array_free_v2(&{var});")
    self.emit(f"  {var} = __pre_assign; }}")
    return True


def _emit_tracked_local_reassign(
    self: Any, var_name: str, value: A.ASTNode, val: str
) -> bool:
    """Emit ownership-aware local reassignment when ``var_name`` is tracked."""
    var = self._mangle_var(var_name)
    if emit_length_only_string_reassign(self, var_name, value):
        return True

    result = _try_emit_string_reassign(self, var_name, value, val, var)
    if result is not None:
        return result
    result = _try_emit_class_reassign(self, var_name, value, val, var)
    if result is not None:
        return result
    result = _try_emit_simple_array_reassign(self, var_name, val, var)
    if result is not None:
        return result
    result = _try_emit_dyn_array_reassign(self, var_name, value, val, var)
    if result is not None:
        return result
    result = _try_emit_light_str_array_reassign(self, var_name, value, val, var)
    return result if result is not None else False

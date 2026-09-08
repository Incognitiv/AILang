from __future__ import annotations

from parser import ast as A

from ast_access import arg_at
from transpiler.class_field_ownership import (
    expr_preserves_field_storage,
    is_string_type,
    string_len_field_name,
)
from transpiler.codegen_int_ranges import remember_fixed_dict_range
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr

from .stmt_visit_reassign import _emit_dyn_array_push_in_place


def visit_FieldAssign(self, node: A.FieldAssign) -> None:
    """Generate field assignment."""
    obj = self.expr(node.object_expr)
    val = self.expr(node.value)
    owner_class = None
    if isinstance(node.object_expr, A.ThisExpr):
        owner_class = self._current_class
    elif self._class_ptr_type(node.object_expr) is not None:
        owner_class = self._class_ptr_type(node.object_expr)

    owner_type = owner_class
    if owner_type is None:
        try:
            owner_type = self._infer_ailang_type(node.object_expr)
        except Exception:
            owner_type = None
    declared_field_type = (
        self._field_ailang_type(owner_type, node.field_name) if owner_type else None
    )
    if declared_field_type is not None:
        checked_val = checked_fixed_int_conversion_expr(
            self, node.value, val, declared_field_type
        )
        if checked_val is not None:
            val = checked_val

    if owner_class is not None and node.field_name in self._auto_owned_field_names(
        owner_class
    ):
        field_type = self._field_ailang_type(owner_class, node.field_name)
        kind = self._auto_owned_field_kind(field_type)
        owner = "self" if isinstance(node.object_expr, A.ThisExpr) else obj
        if (
            kind == "array"
            and isinstance(node.value, A.Call)
            and node.value.name == "array_push"
            and len(node.value.args) >= 2
            and expr_preserves_field_storage(
                node.value, node.object_expr, node.field_name, kind
            )
        ):
            value_kind = (
                "class_ptr"
                if self._class_ptr_type(arg_at(node.value, 1)) is not None
                else "int"
            )
            push_val = self.expr(arg_at(node.value, 1))
            self.emit(f"{{ typeof({owner}) __field_owner = {owner};")
            _emit_dyn_array_push_in_place(
                self,
                f"__field_owner->{node.field_name}",
                push_val,
                value_kind,
                arg_at(node.value, 1),
            )
            self.emit("}")
            return
        rhs_owned: str | int = (
            1
            if kind is not None
            and self._expr_produces_owned_value(node.value, kind, field_type)
            else 0
        )
        rhs_param_flag = None
        if isinstance(node.value, A.Variable):
            rhs_param_entry = (getattr(self, "_owned_param_flags", None) or {}).get(
                node.value.name
            )
            rhs_param_flag = rhs_param_entry[0] if rhs_param_entry is not None else None
            rhs_local_entry = (
                getattr(self, "_owned_value_local_kinds", None) or {}
            ).get(node.value.name)
            if (
                rhs_param_entry is None
                and rhs_local_entry is not None
                and rhs_local_entry[0] == kind
                and (
                    kind != "class"
                    or str(rhs_local_entry[1]) == str(field_type).strip()
                )
            ):
                rhs_owned = 1
        if rhs_param_flag is not None:
            rhs_owned = rhs_param_flag
        flag = self._class_field_owned_flag(node.field_name)
        preserves_storage = kind is not None and expr_preserves_field_storage(
            node.value, node.object_expr, node.field_name, kind
        )
        self.emit(f"{{ typeof({owner}) __field_owner = {owner};")
        val_text = str(val).strip() if isinstance(val, str) else str(val)
        if kind in {"array", "str_array", "dict"} and val_text in {"0", "0LL"}:
            self.emit(
                f"  typeof(__field_owner->{node.field_name}) __field_new = "
                f"(typeof(__field_owner->{node.field_name})) {{0}};"
            )
        else:
            self.emit(
                f"  typeof(__field_owner->{node.field_name}) __field_new = {val};"
            )
        if preserves_storage:
            rhs_owned = f"__field_owner->{flag}"
        elif kind is not None:
            cleanup_lines = self._owned_value_cleanup_lines(
                kind, field_type, f"__field_owner->{node.field_name}"
            )
            self.emit(f"  if (__field_owner->{flag}) {{")
            for line in cleanup_lines:
                self.emit(f"    {line}")
            self.emit("  }")
        self.emit(f"  __field_owner->{node.field_name} = __field_new;")
        if kind == "string":
            self.emit(
                f"  __field_owner->{string_len_field_name(node.field_name)} = "
                f"{self._emit_known_strlen(node.value, val)};"
            )
        self.emit(f"  __field_owner->{flag} = {rhs_owned}; }}")
        if rhs_param_flag is not None:
            self.emit(f"{rhs_param_flag} = 0;")
        return

    # Handle this.field = val -> self->field = val
    if isinstance(node.object_expr, A.ThisExpr):
        self.emit(f"self->{node.field_name} = {val};")
        if owner_class is not None and is_string_type(
            self._field_ailang_type(owner_class, node.field_name)
        ):
            self.emit(
                f"self->{string_len_field_name(node.field_name)} = "
                f"{self._emit_known_strlen(node.value, val)};"
            )
    elif self._class_ptr_type(node.object_expr) is not None:
        self.emit(f"{obj}->{node.field_name} = {val};")
        if owner_class is not None and is_string_type(
            self._field_ailang_type(owner_class, node.field_name)
        ):
            self.emit(
                f"{obj}->{string_len_field_name(node.field_name)} = "
                f"{self._emit_known_strlen(node.value, val)};"
            )
    else:
        self.emit(f"{obj}.{node.field_name} = {val};")


def visit_DictAssign(self, node: A.DictAssign) -> None:
    """Generate dictionary/array assignment: dict[key] = value or arr[idx] = value."""
    dict_expr = self.expr(node.dict_expr)
    key = self.expr(node.key_expr)
    val = self.expr(node.value_expr)

    # Handle chained field access: obj.field[idx] = value
    if isinstance(node.dict_expr, A.FieldAccess):
        owner_class = None
        field_expr = node.dict_expr
        if isinstance(field_expr.object_expr, A.ThisExpr):
            owner_class = self._current_class
        elif self._class_ptr_type(field_expr.object_expr) is not None:
            owner_class = self._class_ptr_type(field_expr.object_expr)
        if (
            owner_class is not None
            and self._field_ailang_type(owner_class, field_expr.field_name) == "dict"
        ):
            self.emit(f"dict_set({dict_expr}, {key}, {val});")
            return
        # Direct array subscript on field: nums.data[i] = value
        self.emit(f"{dict_expr}[{key}] = {val};")
        return

    # Fixed-size array aliases are native C arrays, not ailang_array structs.
    if isinstance(node.dict_expr, A.Variable) and hasattr(self, "_var_types"):
        vtype = self._var_types.get(node.dict_expr.name, "")
        parsed = None
        if isinstance(vtype, str) and hasattr(self, "_parse_fixed_array_type_spec"):
            resolved_vtype = self._resolve_type_alias_spec(vtype)
            parsed = self._parse_fixed_array_type_spec(resolved_vtype)
        if parsed is not None:
            elem_type, fixed_len = parsed
            checked_val = checked_fixed_int_conversion_expr(
                self, node.value_expr, val, elem_type
            )
            if checked_val is not None:
                val = checked_val
            checked_key = checked_fixed_int_conversion_expr(
                self, node.key_expr, key, "i64"
            )
            if checked_key is not None:
                key = checked_key
            self.emit("{")
            self.emit(f"  int64_t __ailang_fixed_idx = (int64_t)({key});")
            self.emit(
                f"  if (__ailang_fixed_idx < 0 || __ailang_fixed_idx >= {int(fixed_len)}) "
                f'__ailang_safety_trap("array index out of bounds");'
            )
            self.emit(f"  {dict_expr}[__ailang_fixed_idx] = {val};")
            self.emit("}")
            return

    # Check if this is a dict variable
    if (
        isinstance(node.dict_expr, A.Variable)
        and hasattr(self, "_dict_vars")
        and node.dict_expr.name in self._dict_vars
    ):
        scalar_values = getattr(self, "_fixed_dict_scalar_values", {})
        if (
            isinstance(node.key_expr, A.StringLit)
            and node.dict_expr.name in scalar_values
            and node.key_expr.value in scalar_values[node.dict_expr.name]
        ):
            self.emit(
                f"{scalar_values[node.dict_expr.name][node.key_expr.value]} = {val};"
            )
            remember_fixed_dict_range(
                self, node.dict_expr.name, node.key_expr.value, node.value_expr
            )
            return
        slots = getattr(self, "_fixed_dict_literal_slots", {})
        if (
            isinstance(node.key_expr, A.StringLit)
            and node.dict_expr.name in slots
            and node.key_expr.value in slots[node.dict_expr.name]
        ):
            slot = slots[node.dict_expr.name][node.key_expr.value]
            self.emit(f"{dict_expr}->entries[{slot}].value = {val};")
            return
        self.emit(f"dict_set({dict_expr}, {key}, {val});")
    elif (
        isinstance(node.dict_expr, A.Variable)
        and hasattr(self, "_dyn_array_vars")
        and node.dict_expr.name in self._dyn_array_vars
    ):
        # Dynamic array (from array_new) - auto-extend if needed
        self.emit(f"if ({key} >= {dict_expr}.length) {{")
        self.indent += 1
        self.emit(f"while ({dict_expr}.length <= {key}) {{")
        self.indent += 1
        self.emit(f"if ({dict_expr}.length >= {dict_expr}.capacity) {{")
        self.indent += 1
        self.emit(f"{dict_expr}.capacity *= 2;")
        self.emit(
            f"{dict_expr}.data = (int64_t*)realloc({dict_expr}.data, "
            f"(size_t){dict_expr}.capacity * sizeof(int64_t));"
        )
        self.indent -= 1
        self.emit("}")
        self.emit(f"{dict_expr}.data[{dict_expr}.length++] = 0;")
        self.indent -= 1
        self.emit("}")
        self.indent -= 1
        self.emit("}")
        self.emit(f"{dict_expr}.data[{key}] = {val};")
    elif (
        isinstance(node.dict_expr, A.Variable)
        and hasattr(self, "_array_vars")
        and node.dict_expr.name in self._array_vars
    ):
        # ailang_array uses .data[idx]
        self.emit(f"{dict_expr}.data[{key}] = {val};")
    else:
        # Treat as regular array assignment
        self.emit(f"{dict_expr}[{key}] = {val};")

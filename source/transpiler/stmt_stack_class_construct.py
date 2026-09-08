"""Stack-class constructor lowering for C assignments."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any

from transpiler import optimizer_decisions as opt
from transpiler import virtual_array_fields as vaf
from transpiler.class_field_ownership import (
    auto_owned_field_kind,
    is_auto_owned_field_type,
    is_auto_owned_param,
    is_string_type,
    owned_field_flag_name,
    string_len_field_name,
)
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr
from transpiler.stack_class_c import emit_stack_class_zero_init


def _plan_stack_array_fields(
    self: Any,
    var_name: str,
    class_name: str,
    value: A.NewExpr,
    fields: list[Any],
    methods: list[Any],
    init_method: Any,
) -> tuple[dict[Any, Any], set[str]]:
    record_fields = {
        class_name: [
            (str(field_name), field_type) for _vis, field_name, field_type in fields
        ]
    }
    candidate_plan = vaf.constructor_stack_array_fields(
        class_name, init_method, record_fields
    )
    if not candidate_plan or len(value.args) < len(init_method.params or []):
        return {}, set()
    planned_fields = set(candidate_plan)
    current_body = getattr(self, "_current_function_body", []) or []
    if not vaf.constructor_body_replayable_with_stack_arrays(
        init_method, candidate_plan
    ):
        return {}, set()
    if not vaf.class_array_field_uses_are_stack_safe(methods, planned_fields):
        return {}, set()
    if not vaf.function_stack_array_field_uses_are_safe(
        current_body, var_name, planned_fields
    ):
        return {}, set()

    scalar_fields = vaf.function_stack_array_field_direct_scalar_reads(
        current_body, var_name, planned_fields
    )
    scalar_fields.update(
        vaf.function_stack_array_field_method_scalar_reads(
            current_body, var_name, methods, planned_fields
        )
    )
    opt.record_stack_array_fields(
        self, value, var_name, class_name, candidate_plan, scalar_fields
    )
    vaf.emit_stack_array_c_declarations(self, var_name, candidate_plan, scalar_fields)
    return candidate_plan, scalar_fields


def _emit_field_metadata(
    self: Any,
    storage: str,
    field_name: str,
    field_type: Any,
    value_node: A.ASTNode,
    value_expr: str,
) -> None:
    if is_string_type(field_type):
        self.emit(
            f"  {storage}.{string_len_field_name(field_name)} = "
            f"{self._emit_known_strlen(value_node, value_expr)};"
        )
    if not is_auto_owned_field_type(field_type, self.classes):
        return
    kind = auto_owned_field_kind(field_type, self.classes)
    owned = (
        self._expr_produces_owned_value(value_node, kind, field_type)
        if kind is not None
        else False
    )
    self.emit(f"  {storage}.{owned_field_flag_name(field_name)} = {1 if owned else 0};")


def _default_field_expr(self: Any, default_node: A.ASTNode, field_type: Any) -> str:
    default_expr = self.expr(default_node)
    checked_default = checked_fixed_int_conversion_expr(
        self, default_node, default_expr, field_type
    )
    if checked_default is not None:
        default_expr = checked_default
    field_type_text = parsed_type_to_str(field_type).strip().lower()
    is_zero_collection = (
        field_type_text in {"array", "str_array", "stringarray", "intarray"}
        and isinstance(default_node, A.Number)
        and not isinstance(default_node.value, float)
        and int(default_node.value) == 0
    )
    if is_zero_collection:
        c_type = self._ailang_type_to_c(parsed_type_to_str(field_type))
        return f"({c_type}){{0}}"
    return default_expr


def _emit_class_defaults(
    self: Any, storage: str, fields: list[Any], class_defaults: dict[str, A.ASTNode]
) -> None:
    for _vis, field_name, field_type in fields:
        default_node = class_defaults.get(field_name)
        if default_node is None:
            continue
        default_expr = _default_field_expr(self, default_node, field_type)
        self.emit(f"  {storage}.{field_name} = {default_expr};")
        _emit_field_metadata(
            self, storage, field_name, field_type, default_node, default_expr
        )


def _append_init_argument(
    self: Any,
    call_args: list[str],
    var_name: str,
    class_name: str,
    init_method: Any,
    index: int,
    arg: A.ASTNode,
) -> None:
    can_elide_virtual = self._can_elide_virtual_string_arg(
        class_name, "init", index, arg
    )
    if can_elide_virtual:
        opt.record_virtual_string_arg(
            self,
            arg,
            class_name,
            "init",
            index,
            var_name,
            "constructor_needs_length_not_bytes",
        )
    arg_expr = "NULL" if can_elide_virtual else self.expr(arg)
    params = init_method.params or []
    source = params[index] if index < len(params) else None
    if isinstance(source, tuple) and len(source) >= 2 and not can_elide_virtual:
        checked_arg = checked_fixed_int_conversion_expr(self, arg, arg_expr, source[1])
        if checked_arg is not None:
            arg_expr = checked_arg
    call_args.append(arg_expr)
    if not isinstance(source, tuple) or len(source) < 2:
        return
    source_type = source[1]
    if is_string_type(source_type):
        call_args.append(self._emit_known_strlen(arg, arg_expr))
    if not is_auto_owned_param(source, self.classes):
        return
    kind = auto_owned_field_kind(source_type, self.classes)
    if kind is None:
        return
    owned = (
        False
        if can_elide_virtual
        else self._expr_produces_owned_value(arg, kind, source_type)
    )
    call_args.append("1" if owned else "0")


def _emit_init_constructor(
    self: Any,
    var_name: str,
    class_name: str,
    storage: str,
    value: A.NewExpr,
    init_method: Any,
    stack_array_plan: dict[Any, Any],
    stack_array_scalar_fields: set[str],
) -> None:
    if stack_array_plan and vaf.emit_stack_array_constructor_c(
        self,
        var_name,
        class_name,
        value,
        init_method,
        stack_array_plan,
        stack_array_scalar_fields,
    ):
        return
    call_args = [f"&{storage}"]
    for index, arg in enumerate(value.args):
        _append_init_argument(
            self, call_args, var_name, class_name, init_method, index, arg
        )
    self.emit(f"  {class_name}_init({', '.join(call_args)});")


def _positional_constructor_is_valid(
    value: A.NewExpr, fields: list[Any], class_defaults: dict[str, A.ASTNode]
) -> bool:
    if len(value.args) > len(fields):
        return False
    return all(
        field_name in class_defaults
        for _vis, field_name, _field_type in fields[len(value.args) :]
    )


def _emit_positional_constructor(
    self: Any, storage: str, value: A.NewExpr, fields: list[Any]
) -> None:
    for index, arg in enumerate(value.args):
        if index >= len(fields):
            break
        _vis, field_name, field_type = fields[index]
        arg_expr = self.expr(arg)
        checked_arg = checked_fixed_int_conversion_expr(self, arg, arg_expr, field_type)
        if checked_arg is not None:
            arg_expr = checked_arg
        self.emit(f"  {storage}.{field_name} = {arg_expr};")
        _emit_field_metadata(self, storage, field_name, field_type, arg, arg_expr)


def _emit_stack_class_construct(
    self: Any, var_name: str, class_name: str, value: A.NewExpr
) -> bool:
    if value.type_name != class_name:
        return False
    class_info = self.classes.get(class_name)
    fields, methods = class_info if class_info else ([], [])
    init_method = next((method for method in methods if method.name == "init"), None)
    var = self._mangle_var(var_name)
    storage = f"__ailang_stack_{var}"

    stack_array_plan: dict[Any, Any] = {}
    stack_array_scalar_fields: set[str] = set()
    if init_method is not None:
        stack_array_plan, stack_array_scalar_fields = _plan_stack_array_fields(
            self, var_name, class_name, value, fields, methods, init_method
        )

    self.emit("{")
    self.emit(f"  if ({var}) {{ {class_name}_destructor({var}); }}")
    emit_stack_class_zero_init(self, class_name, storage)
    class_defaults = self.type_info.class_field_defaults.get(class_name, {})
    _emit_class_defaults(self, storage, fields, class_defaults)
    opt.record_stack_class(self, value, var_name, class_name)

    if init_method is not None:
        _emit_init_constructor(
            self,
            var_name,
            class_name,
            storage,
            value,
            init_method,
            stack_array_plan,
            stack_array_scalar_fields,
        )
    else:
        if not _positional_constructor_is_valid(value, fields, class_defaults):
            return False
        _emit_positional_constructor(self, storage, value, fields)

    self.emit(f"  {var} = &{storage};")
    self.emit("}")
    return True

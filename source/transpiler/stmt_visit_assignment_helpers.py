"""C statement-assignment lowering helpers."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str

from ast_access import arg_at
from transpiler import optimizer_decisions as opt
from transpiler import virtual_array_fields as vaf
from transpiler.arithmetic_literal_proofs import int_literal_value
from transpiler.class_field_ownership import (
    auto_owned_field_kind,
    is_auto_owned_field_type,
    is_auto_owned_param,
    is_string_type,
    owned_field_flag_name,
    string_len_field_name,
)
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr
from transpiler.fixed_int_types import (
    c_name_for_fixed,
    info_for_c_fixed,
    info_for_fixed_int,
)
from transpiler.stack_class_c import emit_stack_class_zero_init
from transpiler.strlen_assign_cache import (
    emit_length_only_string_reassign,
)


def _emit_stack_class_construct(
    self, var_name: str, class_name: str, value: A.NewExpr
) -> bool:
    if value.type_name != class_name:
        return False
    class_info = self.classes.get(class_name)
    fields, methods = class_info if class_info else ([], [])
    init_method = next((m for m in methods if m.name == "init"), None)
    var = self._mangle_var(var_name)
    storage = f"__ailang_stack_{var}"
    stack_array_plan = {}
    stack_array_scalar_fields: set[str] = set()
    if init_method is not None:
        record_fields = {
            class_name: [
                (str(field_name), field_type) for _vis, field_name, field_type in fields
            ]
        }
        candidate_plan = vaf.constructor_stack_array_fields(
            class_name,
            init_method,
            record_fields,
        )
        planned_fields = set(candidate_plan)
        current_body = getattr(self, "_current_function_body", []) or []
        if (
            candidate_plan
            and len(value.args) >= len(init_method.params or [])
            and vaf.constructor_body_replayable_with_stack_arrays(
                init_method,
                candidate_plan,
            )
            and vaf.class_array_field_uses_are_stack_safe(methods, planned_fields)
            and vaf.function_stack_array_field_uses_are_safe(
                current_body,
                var_name,
                planned_fields,
            )
        ):
            stack_array_plan = candidate_plan
            stack_array_scalar_fields = (
                vaf.function_stack_array_field_direct_scalar_reads(
                    current_body,
                    var_name,
                    planned_fields,
                )
            )
            stack_array_scalar_fields.update(
                vaf.function_stack_array_field_method_scalar_reads(
                    current_body,
                    var_name,
                    methods,
                    planned_fields,
                )
            )
            opt.record_stack_array_fields(
                self,
                value,
                var_name,
                class_name,
                stack_array_plan,
                stack_array_scalar_fields,
            )
            vaf.emit_stack_array_c_declarations(
                self,
                var_name,
                stack_array_plan,
                stack_array_scalar_fields,
            )
    self.emit("{")
    self.emit(f"  if ({var}) {{ {class_name}_destructor({var}); }}")
    emit_stack_class_zero_init(self, class_name, storage)
    class_defaults = self.type_info.class_field_defaults.get(class_name, {})
    for _vis, field_name, field_type in fields:
        default_node = class_defaults.get(field_name)
        if default_node is None:
            continue
        default_expr = self.expr(default_node)
        checked_default = checked_fixed_int_conversion_expr(
            self, default_node, default_expr, field_type
        )
        if checked_default is not None:
            default_expr = checked_default
        field_type_text = parsed_type_to_str(field_type).strip().lower()
        if (
            field_type_text in {"array", "str_array", "stringarray", "intarray"}
            and isinstance(default_node, A.Number)
            and not isinstance(default_node.value, float)
            and int(default_node.value) == 0
        ):
            default_expr = (
                f"({self._ailang_type_to_c(parsed_type_to_str(field_type))}){{0}}"
            )
        self.emit(f"  {storage}.{field_name} = {default_expr};")
        if is_string_type(field_type):
            self.emit(
                f"  {storage}.{string_len_field_name(field_name)} = "
                f"{self._emit_known_strlen(default_node, default_expr)};"
            )
        if is_auto_owned_field_type(field_type, self.classes):
            kind = auto_owned_field_kind(field_type, self.classes)
            owned = (
                self._expr_produces_owned_value(default_node, kind, field_type)
                if kind is not None
                else False
            )
            self.emit(
                f"  {storage}.{owned_field_flag_name(field_name)} = "
                f"{1 if owned else 0};"
            )
    opt.record_stack_class(self, value, var_name, class_name)
    if init_method is not None:
        if stack_array_plan and vaf.emit_stack_array_constructor_c(
            self,
            var_name,
            class_name,
            value,
            init_method,
            stack_array_plan,
            stack_array_scalar_fields,
        ):
            pass
        else:
            call_args: list[str] = [f"&{storage}"]
            for index, arg in enumerate(value.args):
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
                if index < len(init_method.params or []):
                    source = init_method.params[index]
                    if (
                        isinstance(source, tuple)
                        and len(source) >= 2
                        and not can_elide_virtual
                    ):
                        checked_arg = checked_fixed_int_conversion_expr(
                            self, arg, arg_expr, source[1]
                        )
                        if checked_arg is not None:
                            arg_expr = checked_arg
                call_args.append(arg_expr)
                if index < len(init_method.params or []):
                    source = init_method.params[index]
                    needs_flag = is_auto_owned_param(source, self.classes)
                    kind = (
                        auto_owned_field_kind(source[1], self.classes)
                        if isinstance(source, tuple) and len(source) >= 2
                        else None
                    )
                    source_type = source[1] if isinstance(source, tuple) else None
                    if (
                        isinstance(source, tuple)
                        and len(source) >= 2
                        and is_string_type(source[1])
                    ):
                        call_args.append(self._emit_known_strlen(arg, arg_expr))
                    if needs_flag and kind is not None:
                        owned = (
                            False
                            if can_elide_virtual
                            else self._expr_produces_owned_value(arg, kind, source_type)
                        )
                        call_args.append("1" if owned else "0")
            self.emit(f"  {class_name}_init({', '.join(call_args)});")
    else:
        if len(value.args) > len(fields):
            return False
        for _vis, field_name, _field_type in fields[len(value.args) :]:
            if field_name not in class_defaults:
                return False
        for index, arg in enumerate(value.args):
            if index >= len(fields):
                break
            _vis, field_name, field_type = fields[index]
            arg_expr = self.expr(arg)
            checked_arg = checked_fixed_int_conversion_expr(
                self, arg, arg_expr, field_type
            )
            if checked_arg is not None:
                arg_expr = checked_arg
            self.emit(f"  {storage}.{field_name} = {arg_expr};")
            if is_string_type(field_type):
                self.emit(
                    f"  {storage}.{string_len_field_name(field_name)} = "
                    f"{self._emit_known_strlen(arg, arg_expr)};"
                )
            if is_auto_owned_field_type(field_type, self.classes):
                kind = auto_owned_field_kind(field_type, self.classes)
                owned = (
                    self._expr_produces_owned_value(arg, kind, field_type)
                    if kind is not None
                    else False
                )
                self.emit(
                    f"  {storage}.{owned_field_flag_name(field_name)} = "
                    f"{1 if owned else 0};"
                )
    self.emit(f"  {var} = &{storage};")
    self.emit("}")
    return True


def _emit_dyn_array_push_in_place(
    self,
    target: str,
    value_code: str,
    value_kind: str = "int",
    value_node: A.ASTNode | None = None,
) -> None:
    """Emit direct append into an `ailang_dyn_array` lvalue.
    This preserves `array_push` semantics but avoids copying the array struct
    through a function return on self-mutating assignments such as
    `arr = array_push(arr, v)` and `this.field = array_push(this.field, v)`.
    """
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


def _fixed_int_info_for_ailang_spec(self, type_spec: object):
    try:
        resolved = self._resolve_type_alias_spec(parsed_type_to_str(type_spec))
    except Exception:
        resolved = parsed_type_to_str(type_spec)
    return info_for_fixed_int(resolved)


def _c_fixed_literal_expr(value: int, target_info) -> str:
    """Build an exact C expression for a fixed-width integer literal.

    Host C integer suffixes stop at 64 bits.  Construct wider constants from
    64-bit chunks *after* casting each chunk to the target-width unsigned type;
    this avoids the historical `...ULL` truncation before i128..i8192 casts.
    """
    bits = int(target_info.bits)
    if bits <= 64:
        if value < 0:
            return str(value) + "LL"
        return f"0x{value:X}ULL" if value > 0x7FFFFFFF else f"{value}LL"
    unsigned_info = info_for_fixed_int(f"u{bits}")
    assert unsigned_info is not None
    uc = c_name_for_fixed(unsigned_info)
    bit_pattern = value & ((1 << bits) - 1)
    terms: list[str] = []
    for shift in range(0, bits, 64):
        chunk = (bit_pattern >> shift) & ((1 << 64) - 1)
        if chunk == 0:
            continue
        term = f"(({uc})0x{chunk:X}ULL)"
        if shift:
            term = f"({term} << {shift})"
        terms.append(term)
    expr = " | ".join(terms) if terms else f"({uc})0"
    target_c = c_name_for_fixed(target_info)
    return f"(({target_c})({expr}))"


def _emit_checked_fixed_int_assignment(
    self,
    target: str,
    target_spec: object,
    value_node: A.ASTNode,
    value_code: str,
) -> bool:
    """Assign one fixed integer to another without implicit C wrapping.

    The C backend uses native C/_BitInt values, whose implicit conversions are
    allowed to truncate or reinterpret.  AILang's safe implicit conversion
    contract is stricter: if the runtime value is not representable in the
    declared target type, trap before the cast.
    """
    target_info = _fixed_int_info_for_ailang_spec(self, target_spec)
    source_c = self._infer_type(value_node)
    source_info = info_for_c_fixed(source_c)
    if target_info is None or source_info is None:
        return False

    target_c = self._ailang_type_to_c(parsed_type_to_str(target_spec))
    literal = int_literal_value(value_node)
    if literal is not None:
        low = 0 if target_info.unsigned else -(1 << (target_info.bits - 1))
        high = (
            (1 << target_info.bits) - 1
            if target_info.unsigned
            else (1 << (target_info.bits - 1)) - 1
        )
        if low <= literal <= high:
            # Literals are adaptable to their declared fixed type.  Keep the
            # full target width in C rather than materializing through int64_t.
            source_info = target_info
            source_c = target_c
            value_code = _c_fixed_literal_expr(literal, target_info)
    sw, tw = source_info.bits, target_info.bits
    su, tu = source_info.unsigned, target_info.unsigned
    checks: list[str] = []
    temp = "__ailang_int_conv"

    if tu:
        if not su:
            checks.append(f"{temp} < 0")
        if tw < sw:
            # Short-circuiting after the negative check keeps signed right
            # shift confined to non-negative values.
            checks.append(f"({temp} >> {tw}) != 0")
    else:
        if su:
            # A signed iN has N-1 value bits.  Any higher source bit means the
            # unsigned value does not fit the positive half of the target.
            if tw <= sw:
                checks.append(f"({temp} >> {tw - 1}) != 0")
        elif tw < sw:
            low = f"-((({source_c})1) << {tw - 1})"
            high = f"(((({source_c})1) << {tw - 1}) - 1)"
            checks.extend([f"{temp} < {low}", f"{temp} > {high}"])

    self.emit("{")
    self.emit(f"  {source_c} {temp} = ({source_c})({value_code});")
    if checks:
        cond = " || ".join(f"({check})" for check in checks)
        target_name = f"{'u' if tu else 'i'}{tw}"
        self.emit(f"  if ({cond}) {{")
        self.emit(
            f'    fprintf(stderr, "Error: integer value does not fit {target_name}!\\n");'
        )
        self.emit(
            f'    __ailang_safety_trap("integer conversion out of range for {target_name}");'
        )
        self.emit("  }")
    self.emit(f"  {target} = ({target_c}){temp};")
    self.emit("}")
    return True


def _emit_tracked_local_reassign(
    self, var_name: str, value: A.ASTNode, val: str
) -> bool:
    """Emit ownership-aware local reassignment when ``var_name`` is tracked.
    AILang local declarations lower to one C declaration at function entry plus
    assignments at the source site. Therefore ``T x = ...`` inside a loop must
    clean the old value exactly like ``x = ...`` does.
    """
    var = self._mangle_var(var_name)
    if emit_length_only_string_reassign(self, var_name, value):
        return True
    if var_name in self._tracked_owned_string_locals:
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_safe_free((void *)(uintptr_t)({var}));")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name in self._mixed_ownership_string_locals:
        rhs_owned = 1 if self._is_owned_string_alloc(value) else 0
        flag = self._mixed_owned_flag(var_name)
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  if ({flag}) ailang_safe_free((void *)(uintptr_t)({var}));")
        self.emit(f"  {var} = __pre_assign;")
        self.emit(f"  {flag} = {rhs_owned}; }}")
        return True
    stack_classes = getattr(self, "_stack_owned_class_locals", None) or {}
    if (
        var_name in stack_classes
        and isinstance(value, A.NewExpr)
        and _emit_stack_class_construct(self, var_name, stack_classes[var_name], value)
    ):
        return True
    if var_name in self._tracked_owned_class_locals:
        cls = self._tracked_owned_class_locals[var_name]
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(
            f"  if ({var}) {{ {cls}_destructor({var}); " f"ailang_safe_free({var}); }}"
        )
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name in (getattr(self, "_str_array_locals_for_cleanup", None) or []):
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_str_array_free(&{var});")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name in (getattr(self, "_int_array_locals_for_cleanup", None) or []):
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_int_array_free(&{var});")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name in (getattr(self, "_dyn_array_locals_for_cleanup", None) or []):
        if (
            isinstance(value, A.Call)
            and value.name == "array_push"
            and value.args
            and isinstance(arg_at(value, 0), A.Variable)
            and arg_at(value, 0).name == var_name
        ):
            value_kind = (
                "class_ptr"
                if len(value.args) >= 2
                and self._class_ptr_type(arg_at(value, 1)) is not None
                else "int"
            )
            push_val = self.expr(arg_at(value, 1))
            _emit_dyn_array_push_in_place(
                self, var, push_val, value_kind, arg_at(value, 1)
            )
            return True
        if (
            isinstance(value, A.Call)
            and value.name == "array_set"
            and value.args
            and isinstance(arg_at(value, 0), A.Variable)
            and arg_at(value, 0).name == var_name
        ):
            return False
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_dyn_array_free(&{var});")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    if var_name in (getattr(self, "_lc_str_array_locals_for_cleanup", None) or []):
        if (
            isinstance(value, A.Call)
            and value.name == "str_array_push"
            and value.args
            and isinstance(arg_at(value, 0), A.Variable)
            and arg_at(value, 0).name == var_name
        ):
            return False
        self.emit(f"{{ typeof({var}) __pre_assign = {val};")
        self.emit(f"  ailang_str_array_free_v2(&{var});")
        self.emit(f"  {var} = __pre_assign; }}")
        return True
    return False

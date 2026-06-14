"""Assignment/data statement visitors for CStmtEmitter."""

from __future__ import annotations

from parser import ast as A
from parser.ast import ParsedType, parsed_type_to_str
from typing import cast

from ast_access import arg_at
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
from transpiler.stack_class_c import emit_stack_class_zero_init
from transpiler.strlen_assign_cache import (
    emit_length_only_string_reassign,
    update_strlen_cache_after_assign,
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
        for index, arg in enumerate(value.args):
            if index >= len(fields):
                break
            _vis, field_name, field_type = fields[index]
            arg_expr = self.expr(arg)
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
    self, target: str, value_code: str, value_kind: str = "int"
) -> None:
    """Emit direct append into an `ailang_dyn_array` lvalue.
    This preserves `array_push` semantics but avoids copying the array struct
    through a function return on self-mutating assignments such as
    `arr = array_push(arr, v)` and `this.field = array_push(this.field, v)`.
    """
    value = (
        f"(int64_t)(uintptr_t)({value_code})"
        if value_kind == "class_ptr"
        else value_code
    )
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
            _emit_dyn_array_push_in_place(self, var, push_val, value_kind)
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


def _emit_dict_literal_assign(self, var_name: str, node: A.DictLit) -> None:
    """Emit dict literal assignment, cleaning an old tracked local first."""
    var = self._mangle_var(var_name)
    tracked = var_name in (getattr(self, "_dict_locals_for_cleanup", None) or [])
    target = "__pre_assign" if tracked else var
    if tracked:
        self.emit(f"{{ typeof({var}) __pre_assign = dict_new();")
    else:
        self.emit(f"{var} = dict_new();")
    for key, item_val in node.pairs:
        key_code = self.expr(key)
        val_code = self.expr(item_val)
        self.emit(f"dict_set({target}, {key_code}, {val_code});")
    if tracked:
        self.emit(f"if ({var}) dict_destroy_fn({var});")
        self.emit(f"{var} = __pre_assign;")
        self.emit("}")


def visit_Assign(self, node: A.Assign) -> None:
    """Generate assignment."""
    if node.var_name in self._const_global_names:
        raise ValueError(f"Cannot assign to constant '{node.var_name}'")
    var = self._mangle_var(node.var_name)
    self._var_types[node.var_name] = self._infer_ailang_type(node.value)
    array_len_hints = getattr(self, "_array_len_hints", None)
    propagated_array_len = None
    if isinstance(array_len_hints, dict) and isinstance(node.value, A.Variable):
        scoped_src = (self.current_function, node.value.name)
        global_src = (None, node.value.name)
        if scoped_src in array_len_hints:
            propagated_array_len = int(array_len_hints[scoped_src])
        elif global_src in array_len_hints:
            propagated_array_len = int(array_len_hints[global_src])
    if isinstance(node.value, A.ArrayLit):
        elements = [self.expr(e) for e in node.value.elements]
        self.emit(f"int64_t {var}_data[] = {{ {', '.join(elements)} }};")
        self.emit(f"{var}.data = {var}_data;")
        self.emit(f"{var}.length = {len(elements)};")
        if isinstance(array_len_hints, dict):
            array_len_hints[(self.current_function, node.var_name)] = len(elements)
    # Special handling for dict literal
    elif isinstance(node.value, A.DictLit):
        if isinstance(array_len_hints, dict):
            array_len_hints.pop((self.current_function, node.var_name), None)
        _emit_dict_literal_assign(self, node.var_name, node.value)
    # Special handling for list comprehension
    elif isinstance(node.value, A.ListComprehension):
        if isinstance(array_len_hints, dict):
            array_len_hints.pop((self.current_function, node.var_name), None)
        self._generate_list_comprehension(var, node.value)
    else:
        if isinstance(array_len_hints, dict):
            key = (self.current_function, node.var_name)
            if propagated_array_len is None:
                array_len_hints.pop(key, None)
            else:
                array_len_hints[key] = propagated_array_len
        val = self.expr(node.value)
        if not _emit_tracked_local_reassign(self, node.var_name, node.value, val):
            if self._infer_type(node.value) == "const char *":
                self.emit(f"{var} = (char *)({val});")
            else:
                self.emit(f"{var} = {val};")
        update_strlen_cache_after_assign(self, node.var_name, node.value)
    # Check if this variable has a range constraint
    if hasattr(self, "_range_vars") and node.var_name in self._range_vars:
        low, high, exclusive = self._range_vars[node.var_name]
        if exclusive:
            self.emit(
                f"if ({var} < {low} || {var} >= {high}) {{ "
                f'fprintf(stderr, "Range error: %s = %lld not in %lld...%lld\\n", '
                f'"{node.var_name}", (long long){var}, (long long){low}, '
                f'(long long){high}); __ailang_safety_trap("range error"); }}'
            )
        else:
            self.emit(
                f"if ({var} < {low} || {var} > {high}) {{ "
                f'fprintf(stderr, "Range error: %s = %lld not in %lld..%lld\\n", '
                f'"{node.var_name}", (long long){var}, (long long){low}, '
                f'(long long){high}); __ailang_safety_trap("range error"); }}'
            )


def _infer_ailang_type(self, node: A.ASTNode) -> str:
    """Infer AILang type name from an expression (for typeof tracking).
    This populates `_var_types`, which feeds `_class_ptr_type` and
    the assorted dispatch decisions below — so it must recognize
    class instantiation and class-returning calls (otherwise they
    get tracked as the default `int` and downstream pointer-casts
    / method dispatch silently miss).
    """
    if isinstance(node, A.NewExpr) and node.type_name in self.classes:
        return node.type_name
    if isinstance(node, A.Call):
        if node.name in self._STR_RETURNING_BUILTINS:
            return "string"
        # Dynamic-collection builtins. Tracking the AILang-level type
        # here keeps `_var_types` aligned with `_infer_type`'s C-level
        # decision, so the call-site cast logic doesn't add redundant
        # `(ailang_dyn_array)(x)` casts (which trip -Wpedantic).
        if node.name in ("array_new", "array_push", "array_set"):
            return "array"
        if node.name in ("str_array_new", "str_array_push"):
            return "str_array"
        if node.name == "split":
            return "StringArray"
        if node.name == "split_ints":
            return "IntArray"
        if node.name == "as_class" and len(node.args) >= 2:
            tn = arg_at(node, 1)
            if isinstance(tn, A.StringLit) and tn.value in self.classes:
                return tn.value
            if isinstance(tn, A.Variable) and tn.name in self.classes:
                return tn.name
        if node.name in self.functions:
            _params, ret = self.functions[node.name]
            if ret in self.classes:
                return ret
            # User functions returning collection / string types: keep
            # the AILang type so caller-side `_var_types` matches the
            # callee's parameter type and avoids redundant casts.
            if ret in ("array", "str_array", "dict", "string"):
                return ret
    if isinstance(node, A.StringLit):
        return "string"
    if isinstance(node, A.InterpolatedString):
        return "string"
    if isinstance(node, A.Number):
        if isinstance(node.value, float):
            if hasattr(node, "raw") and node.raw and node.raw.endswith("f"):
                return "float"
            return "double"
        return "int"
    if isinstance(node, A.Bool):
        return "bool"
    if isinstance(node, A.Null):
        return "ptr"
    if isinstance(node, A.ArrayLit):
        return "array"
    if isinstance(node, A.DictLit):
        return "dict"
    if isinstance(node, A.Call):
        if node.name in ("strlen", "len", "ord", "index_of"):
            return "int"
        if node.name in (
            "char_at",
            "unsafe_char_at",
            "chr",
            "substr",
            "concat",
            "str_replace",
        ):
            return "string"
        if node.name == "typeof":
            return "string"
    # Look up in our type tracking
    if isinstance(node, A.Variable) and node.name in self._var_types:
        return self._var_types[node.name]
    return "int"


def _generate_list_comprehension(
    self, var_name: str, node: A.ListComprehension
) -> None:
    """Generate code for list comprehension."""
    # Initialize dynamic array
    self.emit(f"{var_name} = array_new(8);")

    # Handle Range iterable
    if isinstance(node.iterable, A.Range):
        start = self.expr(node.iterable.start)
        end = self.expr(node.iterable.end)
        loop_var = node.var_name

        if node.iterable.inclusive:
            cond = f"{loop_var} <= {end}"
        else:
            cond = f"{loop_var} < {end}"

        self.emit(f"for (int64_t {loop_var} = {start}; {cond}; {loop_var}++) {{")
        self.indent += 1

        if node.condition:
            cond_code = self.expr(node.condition)
            self.emit(f"if ({cond_code}) {{")
            self.indent += 1

        expr_code = self.expr(node.expr)
        self.emit(f"{var_name} = array_push({var_name}, {expr_code});")

        if node.condition:
            self.indent -= 1
            self.emit("}")

        self.indent -= 1
        self.emit("}")
    else:
        # Handle array iterable
        arr = self.expr(node.iterable)
        loop_var = node.var_name
        idx_var = f"_idx_{id(node)}"

        self.emit(
            f"for (int64_t {idx_var} = 0; {idx_var} < {arr}.length; {idx_var}++) {{"
        )
        self.indent += 1
        self.emit(f"int64_t {loop_var} = {arr}.data[{idx_var}];")

        if node.condition:
            cond_code = self.expr(node.condition)
            self.emit(f"if ({cond_code}) {{")
            self.indent += 1

        expr_code = self.expr(node.expr)
        self.emit(f"{var_name} = array_push({var_name}, {expr_code});")

        if node.condition:
            self.indent -= 1
            self.emit("}")

        self.indent -= 1
        self.emit("}")


_tuple_counter: int = 0


def visit_TupleAssign(self, node: A.TupleAssign) -> None:
    """Generate tuple unpacking assignment."""
    # Use unique counter for each tuple assignment
    base = type(self)._tuple_counter
    type(self)._tuple_counter += len(node.values)

    # First evaluate all RHS values to temp vars (for swap support)
    temps = []
    for i, val in enumerate(node.values):
        temp_name = f"_tuple_tmp_{base + i}"
        val_code = self.expr(val)
        self.emit(f"int64_t {temp_name} = {val_code};")
        temps.append(temp_name)
    # Then assign to actual variables
    for i, var_name in enumerate(node.var_names):
        if i < len(temps):
            self.emit(f"{var_name} = {temps[i]};")


def visit_VarDecl(self, node: A.VarDecl) -> None:
    """Generate variable declaration (works for both local and global)."""
    val = self.expr(node.init_value) if node.init_value else "0"
    if hasattr(self, "_array_len_hints"):
        key = (self.current_function, node.var_name)
        if isinstance(node.init_value, A.ArrayLit):
            self._array_len_hints[key] = len(node.init_value.elements)
        elif isinstance(node.init_value, A.Variable):
            src_scoped = (self.current_function, node.init_value.name)
            src_global = (None, node.init_value.name)
            if src_scoped in self._array_len_hints:
                self._array_len_hints[key] = int(self._array_len_hints[src_scoped])
            elif src_global in self._array_len_hints:
                self._array_len_hints[key] = int(self._array_len_hints[src_global])
            else:
                self._array_len_hints.pop(key, None)
        else:
            self._array_len_hints.pop(key, None)
    # Track variable type for typeof()
    if node.type_name:
        type_str = parsed_type_to_str(node.type_name)
        self._var_types[node.var_name] = self._type_name_to_ailang(type_str)

    # Check if we're at global scope (not inside a function)
    if self.current_function is None:
        if node.is_const and getattr(node, "c_header_declared", False):
            return
        # Global constant - emit as static const
        if node.type_name:
            type_for_c = parsed_type_to_str(node.type_name)
        else:
            type_for_c = "int64_t"

        # Check if init value is an array literal - need pointer type
        is_array_init = isinstance(node.init_value, A.ArrayLit)
        if is_array_init:
            type_for_c = "int64_t *"

        if node.is_const:
            decl = self._format_c_declaration(type_for_c, node.var_name)
            is_unused = (
                hasattr(self, "_globally_used_names")
                and node.var_name not in self._globally_used_names
            )
            prefix = "AILANG_UNUSED " if is_unused else ""
            if decl.startswith("const "):
                self.emit(f"{prefix}static {decl} = {val};")
            else:
                self.emit(f"{prefix}static const {decl} = {val};")
        else:
            decl = self._format_c_declaration(type_for_c, node.var_name)
            self.emit(f"static {decl} = {val};")
    else:
        # Local variable
        if isinstance(node.init_value, A.DictLit):
            _emit_dict_literal_assign(self, node.var_name, node.init_value)
            return
        if (
            node.type_name is not None
            and isinstance(node.init_value, A.ArrayLit)
            and hasattr(self, "_parse_fixed_array_type_spec")
        ):
            type_spec = parsed_type_to_str(node.type_name)
            if hasattr(self, "_resolve_type_alias_spec"):
                type_spec = self._resolve_type_alias_spec(type_spec)
            fixed = self._parse_fixed_array_type_spec(type_spec)
            if fixed is not None:
                _elem_type, size = fixed
                elems = node.init_value.elements
                for idx in range(size):
                    if idx < len(elems):
                        elem_code = self.expr(elems[idx])
                    else:
                        elem_code = "0"
                    self.emit(f"{node.var_name}[{idx}] = {elem_code};")
                return
        if not _emit_tracked_local_reassign(self, node.var_name, node.init_value, val):
            self.emit(f"{self._mangle_var(node.var_name)} = {val};")
        if node.init_value is not None:
            update_strlen_cache_after_assign(self, node.var_name, node.init_value)


def visit_RangeVarDecl(self, node: A.RangeVarDecl) -> None:
    """Generate Ada-style range-constrained variable with runtime checks."""
    var_name = node.var_name
    low = self.expr(node.range_type.low)
    high = self.expr(node.range_type.high)

    # Initial value defaults to low bound if not specified
    init_val = self.expr(node.init_value) if node.init_value else low

    # Emit assignment (variable already declared by collector)
    self.emit(f"{var_name} = {init_val};")

    # Emit range check assert
    if node.range_type.exclusive:
        # Exclusive range: low <= x < high
        self.emit(
            f"if ({var_name} < {low} || {var_name} >= {high}) {{ "
            f'fprintf(stderr, "Range error: %s = %lld not in %lld...%lld\\n", '
            f'"{var_name}", (long long){var_name}, (long long){low}, (long long){high}); '
            f'__ailang_safety_trap("range error"); }}'
        )
    else:
        # Inclusive range: low <= x <= high
        self.emit(
            f"if ({var_name} < {low} || {var_name} > {high}) {{ "
            f'fprintf(stderr, "Range error: %s = %lld not in %lld..%lld\\n", '
            f'"{var_name}", (long long){var_name}, (long long){low}, (long long){high}); '
            f'__ailang_safety_trap("range error"); }}'
        )

    # Track as range type for future assignments
    self._range_vars[node.var_name] = (low, high, node.range_type.exclusive)


def visit_TypeAlias(self, node: A.TypeAlias) -> None:
    """Generate type alias comment (ranges are runtime-checked, not C typedefs)."""
    # Keep aliases for downstream declaration/type lowering.
    self._type_aliases[node.name] = node.target_type
    if isinstance(node.target_type, A.RangeType):
        low = self.expr(node.target_type.low)
        high = self.expr(node.target_type.high)
        op = "..." if node.target_type.exclusive else ".."
        self.emit(f"/* type {node.name} = {low}{op}{high} */")
    else:
        target_type = cast(ParsedType, node.target_type)
        self.emit(f"/* type {node.name} = {parsed_type_to_str(target_type)} */")


def _type_name_to_ailang(self, type_name: str) -> str:
    """Convert type name to AILang type name for typeof()."""
    # Map canonical type names to AILang names (adjective ladder)
    type_map = {
        "i8": "tiny",
        "tiny": "tiny",
        "u8": "byte",
        "byte": "byte",
        "i16": "small",
        "small": "small",
        "u16": "usmall",
        "usmall": "usmall",
        "i32": "short",
        "short": "short",
        "u32": "ushort",
        "ushort": "ushort",
        "i64": "int",
        "int": "int",
        "u64": "uint",
        "uint": "uint",
        "i128": "long",
        "long": "long",
        "u128": "ulong",
        "ulong": "ulong",
        "i256": "wide",
        "wide": "wide",
        "u256": "uwide",
        "uwide": "uwide",
        "i512": "vast",
        "vast": "vast",
        "u512": "uvast",
        "uvast": "uvast",
        "i1024": "grand",
        "grand": "grand",
        "u1024": "ugrand",
        "ugrand": "ugrand",
        "i2048": "giant",
        "giant": "giant",
        "u2048": "ugiant",
        "ugiant": "ugiant",
        "i4096": "titan",
        "titan": "titan",
        "u4096": "utitan",
        "utitan": "utitan",
        "i8192": "colos",
        "colos": "colos",
        "u8192": "ucolos",
        "ucolos": "ucolos",
        "f32": "float",
        "float": "float",
        "f64": "double",
        "double": "double",
        "f128": "quad",
        "quad": "quad",
        "bool": "bool",
        "string": "string",
        "str": "string",
    }
    return type_map.get(type_name, type_name)


def visit_Assert(self, node: A.Assert) -> None:
    """Generate assert."""
    cond = self.expr(node.condition)
    if node.message:
        msg = self.expr(node.message)
        self.emit(
            f'if (!({cond})) {{ fprintf(stderr, "Assertion failed: %s\\n", {msg}); '
            f'__ailang_safety_trap("assertion failed"); }}'
        )
    else:
        self.emit(
            f'if (!({cond})) {{ fprintf(stderr, "Assertion failed\\n"); '
            f'__ailang_safety_trap("assertion failed"); }}'
        )

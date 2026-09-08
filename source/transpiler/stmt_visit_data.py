"""Assignment/data statement visitors for CStmtEmitter."""

from __future__ import annotations

from parser import ast as A
from parser.ast import ParsedType, parsed_type_to_str
from typing import cast

from ast_access import arg_at
from transpiler.array_literal_hints import update_array_literal_hints
from transpiler.c_bigint import is_unbounded_spec, owned_bigint_expr
from transpiler.codegen_int_ranges import range_assignment_proven, remember_assign_range
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr
from transpiler.stmt_visit_dict import _emit_dict_literal_assign
from transpiler.stmt_visit_slices import try_emit_fixed_array_slice_alias
from transpiler.strlen_assign_cache import (
    update_strlen_cache_after_assign,
)
from transpiler.type_name_aliases import type_name_to_ailang
from transpiler.wide_int_types import info_for_ailang

from .stmt_visit_assignment_helpers import (
    _emit_checked_fixed_int_assignment,
    _emit_tracked_local_reassign,
    _fixed_int_info_for_ailang_spec,
)


def visit_Assign(self, node: A.Assign) -> None:
    """Generate assignment."""
    if node.var_name in self._const_global_names:
        raise ValueError(f"Cannot assign to constant '{node.var_name}'")
    var = self._mangle_var(node.var_name)
    # Preserve an explicitly tracked fixed-width wide integer type across
    # assignments. Re-inferring a BinaryOp used to collapse `wide x; x = x <<
    # 200` back to plain `int`, causing the C backend to route the operation
    # through the 64-bit safety helpers.
    local_type = getattr(self, "_current_local_c_types", {}).get(node.var_name)
    existing_type = (
        local_type if local_type is not None else self._var_types.get(node.var_name)
    )
    if local_type is None and (
        existing_type is None
        or (
            not is_unbounded_spec(self, existing_type)
            and info_for_ailang(existing_type) is None
            and _fixed_int_info_for_ailang_spec(self, existing_type) is None
        )
    ):
        self._var_types[node.var_name] = self._infer_ailang_type(node.value)
    if existing_type is not None and is_unbounded_spec(self, existing_type):
        fresh = owned_bigint_expr(self, node.value)
        tmp = f"__ailang_bigint_assign_{abs(id(node))}"
        self.emit("{")
        self.emit(f"  void *{tmp} = {fresh};")
        self.emit(f"  ailang_bigint_free({var});")
        self.emit(f"  {var} = {tmp};")
        self.emit("}")
        remember_assign_range(self, node.var_name, node.value)
        return
    array_len_hints = getattr(self, "_array_len_hints", None)
    update_array_literal_hints(self, node.var_name, node.value)
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
        target_spec = (
            local_type if local_type is not None else self._var_types.get(node.var_name)
        )
        if target_spec is not None and _emit_checked_fixed_int_assignment(
            self, var, target_spec, node.value, val
        ):
            pass
        elif not _emit_tracked_local_reassign(self, node.var_name, node.value, val):
            if self._infer_type(node.value) == "const char *":
                self.emit(f"{var} = (char *)({val});")
            else:
                self.emit(f"{var} = {val};")
        update_strlen_cache_after_assign(self, node.var_name, node.value)
    # Check if this variable has a range constraint
    if hasattr(self, "_range_vars") and node.var_name in self._range_vars:
        low, high, exclusive = self._range_vars[node.var_name]
        proven = range_assignment_proven(self, node.value, low, high, exclusive)
        if exclusive and not proven:
            self.emit(
                f"if ({var} < {low} || {var} >= {high}) {{ "
                f'fprintf(stderr, "Range error: %s = %lld not in %lld...%lld\\n", '
                f'"{node.var_name}", (long long){var}, (long long){low}, '
                f'(long long){high}); __ailang_safety_trap("range error"); }}'
            )
        elif not exclusive and not proven:
            self.emit(
                f"if ({var} < {low} || {var} > {high}) {{ "
                f'fprintf(stderr, "Range error: %s = %lld not in %lld..%lld\\n", '
                f'"{node.var_name}", (long long){var}, (long long){low}, '
                f'(long long){high}); __ailang_safety_trap("range error"); }}'
            )
    remember_assign_range(self, node.var_name, node.value)


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
    if (
        isinstance(node, A.BinaryOp)
        and node.op == "+"
        and (self._might_be_string(node.left) or self._might_be_string(node.right))
    ):
        return "string"
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
        if node.name in (
            "strlen",
            "len",
            "ord",
            "index_of",
            "char_at",
            "unsafe_char_at",
        ):
            return "int"
        if node.name in ("chr", "substr", "concat", "str_replace"):
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
    declared_unbounded = node.type_name is not None and is_unbounded_spec(
        self, node.type_name
    )
    val = (
        owned_bigint_expr(self, node.init_value)
        if declared_unbounded and node.init_value is not None
        else (
            "ailang_bigint_from_int(0)"
            if declared_unbounded
            else (self.expr(node.init_value) if node.init_value else "0")
        )
    )
    if node.init_value is not None:
        update_array_literal_hints(self, node.var_name, node.init_value)
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
        if declared_unbounded:
            raise ValueError(
                "global unbounded initialization is not implemented yet; "
                "use a local value until runtime global initialization has ownership semantics"
            )
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
        if declared_unbounded:
            self.emit(f"{self._mangle_var(node.var_name)} = {val};")
            if node.init_value is not None:
                remember_assign_range(self, node.var_name, node.init_value)
            return
        if isinstance(node.init_value, A.DictLit):
            _emit_dict_literal_assign(self, node.var_name, node.init_value)
            return
        if try_emit_fixed_array_slice_alias(self, node):
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
                        elem_node = elems[idx]
                        elem_code = self.expr(elem_node)
                        checked_elem = checked_fixed_int_conversion_expr(
                            self, elem_node, elem_code, _elem_type
                        )
                        if checked_elem is not None:
                            elem_code = checked_elem
                    else:
                        elem_code = "0"
                    self.emit(f"{node.var_name}[{idx}] = {elem_code};")
                return
        target = self._mangle_var(node.var_name)
        if (
            node.type_name is not None
            and node.init_value is not None
            and _emit_checked_fixed_int_assignment(
                self, target, node.type_name, node.init_value, val
            )
        ):
            pass
        elif not _emit_tracked_local_reassign(
            self, node.var_name, node.init_value, val
        ):
            self.emit(f"{target} = {val};")
        if node.init_value is not None:
            update_strlen_cache_after_assign(self, node.var_name, node.init_value)
            remember_assign_range(self, node.var_name, node.init_value)


def visit_RangeVarDecl(self, node: A.RangeVarDecl) -> None:
    """Generate Ada-style range-constrained variable with runtime checks."""
    var_name = node.var_name
    low = self.expr(node.range_type.low)
    high = self.expr(node.range_type.high)

    # Initial value defaults to low bound if not specified
    init_val = self.expr(node.init_value) if node.init_value else low

    # Emit assignment (variable already declared by collector)
    self.emit(f"{var_name} = {init_val};")

    # Emit range check assert only when the initializer is not already proven.
    proven = range_assignment_proven(
        self,
        node.init_value or node.range_type.low,
        low,
        high,
        node.range_type.exclusive,
    )
    if node.range_type.exclusive and not proven:
        # Exclusive range: low <= x < high
        self.emit(
            f"if ({var_name} < {low} || {var_name} >= {high}) {{ "
            f'fprintf(stderr, "Range error: %s = %lld not in %lld...%lld\\n", '
            f'"{var_name}", (long long){var_name}, (long long){low}, (long long){high}); '
            f'__ailang_safety_trap("range error"); }}'
        )
    elif not node.range_type.exclusive and not proven:
        # Inclusive range: low <= x <= high
        self.emit(
            f"if ({var_name} < {low} || {var_name} > {high}) {{ "
            f'fprintf(stderr, "Range error: %s = %lld not in %lld..%lld\\n", '
            f'"{var_name}", (long long){var_name}, (long long){low}, (long long){high}); '
            f'__ailang_safety_trap("range error"); }}'
        )

    # Track as range type for future assignments
    self._range_vars[node.var_name] = (low, high, node.range_type.exclusive)
    remember_assign_range(self, node.var_name, node.init_value or node.range_type.low)


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
    return type_name_to_ailang(type_name)


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

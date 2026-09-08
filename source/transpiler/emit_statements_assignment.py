"""Assignment and range-proof statement emission helpers."""

from __future__ import annotations

from parser.ast import (
    Assign,
    Call,
    NewExpr,
    Number,
    RangeType,
    TupleAssign,
)
from typing import cast

from codegen.strlen_fact_cache import (
    consume_value_strlen_fact,
    invalidate_strlen_facts,
    maybe_register_strlen_fact,
    register_strlen_fact,
)
from codegen.strlen_scalarization import (
    try_emit_known_string_length,
    try_emit_length_only_str_assignment,
)
from llvmlite import ir
from transpiler.codegen_int_ranges import (
    clear_codegen_int_proofs,
    expr_int_range,
    expr_invalidates_local_int_proofs,
    remember_assign_range,
)
from transpiler.llvm_bigint import (
    bigint_from_decimal_literal,
    clone_if_borrowed,
    fixed_to_bigint,
    is_bigint_value,
    is_unbounded_spec,
)
from transpiler.llvm_fixed_dicts import emit_fixed_dict_init
from transpiler.llvm_fixed_int_casts import (
    cast_to_declared_int,
    fixed_int_info_for_spec,
)
from transpiler.llvm_int_narrowing import (
    cast_for_narrowed_storage,
    effective_local_type_name,
    maybe_narrow_local_type,
)

from . import llvm_stack_arrays
from .emit_statements_common import StmtGenError


def _remember_local_constant(codegen, name: str, value: ir.Value) -> None:
    constants = getattr(codegen, "local_constant_values", None)
    if not isinstance(constants, dict):
        return
    if isinstance(value, ir.Constant) and isinstance(value.type, ir.IntType):
        constants[name] = value
        return
    constants.pop(name, None)


def _forget_local_constant(codegen, name: str) -> None:
    constants = getattr(codegen, "local_constant_values", None)
    if isinstance(constants, dict):
        constants.pop(name, None)


def _emit_owned_bigint_for_expr(self, expr) -> ir.Value:
    """Materialize an owning BigInt for storage/call/return boundaries."""
    if isinstance(expr, Number):
        return bigint_from_decimal_literal(self.codegen, self.builder, expr)
    value = self.codegen.generate_expr(expr)
    if isinstance(value, tuple):
        raise StmtGenError("unbounded value cannot be an array tuple")
    if is_bigint_value(self.codegen, value):
        return clone_if_borrowed(self.codegen, self.builder, expr, value)
    if isinstance(value.type, ir.IntType):
        return fixed_to_bigint(
            self.codegen,
            self.builder,
            value,
            unsigned=self.codegen.is_unsigned_value(value),
        )
    raise StmtGenError(f"cannot convert {value.type} to unbounded")


def _emit_range_check(
    self,
    var_name: str,
    var_ptr: ir.Value,
    low_val: ir.Value,
    high_val: ir.Value,
    exclusive: bool,
) -> None:
    """Emit runtime range check for a variable."""
    loaded = self.builder.load(var_ptr, name=f"{var_name}_check")
    too_low = self.builder.icmp_signed("<", loaded, low_val, name="range_low")
    if exclusive:
        too_high = self.builder.icmp_signed(">=", loaded, high_val, name="range_high")
    else:
        too_high = self.builder.icmp_signed(">", loaded, high_val, name="range_high")
    out_of_range = self.builder.or_(too_low, too_high, name="out_of_range")
    # Branch on error
    error_bb = self.builder.append_basic_block(name="range_error")
    ok_bb = self.builder.append_basic_block(name="range_ok")
    self.builder.cbranch(out_of_range, error_bb, ok_bb)
    # Error block: print and exit
    self.builder.position_at_end(error_bb)
    self.codegen._emit_range_error(var_name, loaded, low_val, high_val)
    # Continue in ok block
    self.builder.position_at_end(ok_bb)


def _int_number_value(node) -> int | None:
    if isinstance(node, Number) and isinstance(node.value, int):
        return int(node.value)
    return None


def _range_bounds(range_type: RangeType) -> tuple[int, int, bool] | None:
    low = _int_number_value(range_type.low)
    high = _int_number_value(range_type.high)
    if low is None or high is None:
        return None
    return low, high, range_type.exclusive


def _can_elide_range_check_for_expr(
    self, expr, range_type: RangeType, *, default_to_low: bool = False
) -> bool:
    bounds = _range_bounds(range_type)
    if bounds is None:
        return False
    return _can_elide_range_check_for_bounds(
        self, expr, bounds, default_to_low=default_to_low
    )


def _can_elide_range_check_for_bounds(
    self,
    expr,
    bounds: tuple[int, int, bool],
    *,
    default_to_low: bool = False,
) -> bool:
    low, high, exclusive = bounds
    if expr is None:
        return default_to_low
    literal = _int_number_value(expr)
    if literal is not None:
        max_allowed = high - 1 if exclusive else high
        return low <= literal <= max_allowed
    rng = expr_int_range(self.codegen, expr)
    if rng is not None:
        max_allowed = high - 1 if exclusive else high
        return low <= rng[0] and rng[1] <= max_allowed
    facts = getattr(self.codegen, "range_facts", None)
    if facts is None or not hasattr(facts, "can_prove_range_assignment"):
        return False
    scope = getattr(self.codegen, "_current_function_name", None)
    try:
        return bool(facts.can_prove_range_assignment(expr, bounds, scope))
    except Exception:
        return False


def _llvm_const_int(value: ir.Value) -> int | None:
    found = getattr(value, "constant", None)
    if isinstance(found, int):
        return int(found)
    return None


def _infer_decl_type_from_expr(node) -> str | None:
    if isinstance(node, NewExpr):
        return node.type_name
    if isinstance(node, Call):
        name = node.name
        if name in {"str_array_new", "str_array_push", "str_array_set"}:
            return "str_array"
        if name in {"array_new", "array_push", "array_set"}:
            return "array"
        if name == "split":
            return "stringarray"
        if name == "split_ints":
            return "intarray"
        if name in {"split_str_get", "str_array_get", "substr", "chr", "str"}:
            return "string"
    return None


def visit_Assign(self, node: Assign):
    class_name: str | None = None
    if isinstance(node.value, NewExpr):
        class_name = node.value.type_name
    if _try_emit_record_new_assign(self, node):
        return
    if emit_fixed_dict_init(self.codegen, node.var_name, node.value):
        if expr_invalidates_local_int_proofs(node.value):
            clear_codegen_int_proofs(self.codegen)
        remember_assign_range(self.codegen, node.var_name, node.value)
        invalidate_strlen_facts(self.codegen, node.var_name)
        return
    if llvm_stack_arrays.emit_assign(self, node):
        if expr_invalidates_local_int_proofs(node.value):
            clear_codegen_int_proofs(self.codegen)
        remember_assign_range(self.codegen, node.var_name, node.value)
        return
    declared_spec = self.codegen.local_decl_types.get(node.var_name)
    if declared_spec is not None and is_unbounded_spec(self.codegen, declared_spec):
        slot = self.codegen.locals.get(node.var_name)
        if slot is None or not isinstance(getattr(slot, "type", None), ir.PointerType):
            raise StmtGenError(
                f"unbounded assignment target {node.var_name!r} has no owning slot"
            )
        new_value = _emit_owned_bigint_for_expr(self, node.value)
        old_value = self.builder.load(slot, name=f"{node.var_name}_bigint_old")
        self.builder.store(new_value, slot)
        self.builder.call(self.codegen._get_bigint_free(), [old_value])
        _forget_local_constant(self.codegen, node.var_name)
        invalidate_strlen_facts(self.codegen, node.var_name)
        clear_codegen_int_proofs(self.codegen)
        return
    array_meta: tuple[int, ir.Type] | None = None
    strlen_value = None
    scalar = try_emit_length_only_str_assignment(
        self.codegen, node.var_name, node.value
    )
    if scalar is not None:
        value, strlen_value = scalar
    else:
        evaluated = self.codegen.generate_expr(node.value)
        if isinstance(evaluated, tuple) and len(evaluated) == 3:
            value, array_len, elem_type = evaluated
            array_meta = (array_len, cast(ir.Type, elem_type))
        else:
            value = evaluated
            strlen_value = consume_value_strlen_fact(self.codegen, value)
    if node.var_name in self.codegen.locals:
        slot = self.codegen.locals[node.var_name]
        if isinstance(slot.type, ir.PointerType):
            target_type = slot.type.pointee
            declared_spec = self.codegen.local_decl_types.get(node.var_name)
            if value.type != target_type or (
                isinstance(value.type, ir.IntType)
                and declared_spec is not None
                and fixed_int_info_for_spec(self.codegen, declared_spec) is not None
            ):
                value = cast_to_declared_int(
                    self.codegen, value, target_type, declared_spec
                )
            self.builder.store(value, slot)
            _remember_local_constant(self.codegen, node.var_name, value)
        else:
            target_type = slot.type
            declared_spec = self.codegen.local_decl_types.get(node.var_name)
            if value.type != target_type or (
                isinstance(value.type, ir.IntType)
                and declared_spec is not None
                and fixed_int_info_for_spec(self.codegen, declared_spec) is not None
            ):
                value = cast_to_declared_int(
                    self.codegen, value, target_type, declared_spec
                )
            self.codegen.locals[node.var_name] = value
            _remember_local_constant(self.codegen, node.var_name, value)
        inferred_decl_type = _infer_decl_type_from_expr(node.value)
        if (
            inferred_decl_type is not None
            and node.var_name not in self.codegen.local_decl_types
        ):
            self.codegen.local_decl_types[node.var_name] = inferred_decl_type
    elif node.var_name in self.codegen.globals:
        # Assign to existing global variable
        global_var = self.codegen.globals[node.var_name]
        # Block reassignment of const globals
        if global_var.global_constant:
            raise StmtGenError(f"Cannot assign to constant '{node.var_name}'")
        target_type = global_var.type.pointee
        declared_spec = getattr(self.codegen, "global_decl_types", {}).get(
            node.var_name
        )
        if value.type != target_type or (
            isinstance(value.type, ir.IntType)
            and declared_spec is not None
            and fixed_int_info_for_spec(self.codegen, declared_spec) is not None
        ):
            value = cast_to_declared_int(
                self.codegen, value, target_type, declared_spec
            )
        self.builder.store(value, global_var)
    else:
        # Create alloca in entry block for better mem2reg/SROA optimization
        target_type = maybe_narrow_local_type(
            self.codegen,
            node.var_name,
            value.type,
            hint_range=expr_int_range(self.codegen, node.value),
        )
        if value.type != target_type:
            value = cast_for_narrowed_storage(self.codegen, value, target_type)
        var_ptr = self.codegen.alloca_in_entry_block(target_type, node.var_name)
        self.builder.store(value, var_ptr)
        self.codegen.locals[node.var_name] = var_ptr
        _remember_local_constant(self.codegen, node.var_name, value)
        inferred_decl_type = _infer_decl_type_from_expr(node.value)
        narrowed_decl_type = effective_local_type_name(target_type, inferred_decl_type)
        if narrowed_decl_type is not None:
            self.codegen.local_decl_types[node.var_name] = narrowed_decl_type
        # Best effort: inherit signedness from RHS if detectable, else assume signed
        inferred_unsigned = self.codegen.is_unsigned_value(value)
        self.codegen.var_signedness[node.var_name] = (
            not inferred_unsigned
            if inferred_unsigned
            else self.codegen.var_signedness.get(node.var_name, True)
        )
        self.codegen.set_signedness(var_ptr, self.codegen.var_signedness[node.var_name])
        # Register for RAII cleanup if this is a class with a destructor
        if class_name is not None:
            self.codegen.register_for_cleanup(node.var_name, class_name, value)
    # Check range constraint if this variable has one
    if node.var_name in self.codegen.range_vars:
        low_val, high_val, exclusive = self.codegen.range_vars[node.var_name]
        slot = self.codegen.locals[node.var_name]
        low_int = _llvm_const_int(low_val)
        high_int = _llvm_const_int(high_val)
        bounds = (
            (low_int, high_int, exclusive)
            if low_int is not None and high_int is not None
            else None
        )
        if bounds is None or not _can_elide_range_check_for_bounds(
            self, node.value, bounds
        ):
            self._emit_range_check(node.var_name, slot, low_val, high_val, exclusive)
    if array_meta:
        self.codegen.array_metadata[node.var_name] = array_meta
    elif node.var_name in self.codegen.array_metadata:
        del self.codegen.array_metadata[node.var_name]
    invalidate_strlen_facts(self.codegen, node.var_name)
    if strlen_value is not None:
        register_strlen_fact(self.codegen, node.var_name, strlen_value)
    else:
        known_strlen = try_emit_known_string_length(self.codegen, node.value)
        if known_strlen is not None:
            register_strlen_fact(self.codegen, node.var_name, known_strlen)
        else:
            maybe_register_strlen_fact(self.codegen, node, value)
    if expr_invalidates_local_int_proofs(node.value):
        clear_codegen_int_proofs(self.codegen)
    remember_assign_range(self.codegen, node.var_name, node.value)


def _try_emit_record_new_assign(self, node: Assign) -> bool:
    if not isinstance(node.value, NewExpr):
        return False
    type_name = node.value.type_name
    record_type = self.codegen.record_types.get(type_name)
    if record_type is None or type_name in getattr(self.codegen, "class_types", {}):
        return False
    if node.var_name in self.codegen.locals:
        var_ptr = self.codegen.locals[node.var_name]
        if not isinstance(getattr(var_ptr, "type", None), ir.PointerType):
            return False
        if not _try_emit_record_new_into_slot(
            self, var_ptr, var_ptr.type.pointee, node.var_name, node.value
        ):
            return False
    elif node.var_name in self.codegen.globals:
        return False
    else:
        var_ptr = self.codegen.alloca_in_entry_block(record_type, node.var_name)
        if not _try_emit_record_new_into_slot(
            self, var_ptr, record_type, node.var_name, node.value
        ):
            return False
        self.codegen.locals[node.var_name] = var_ptr
        self.codegen.var_signedness[node.var_name] = True
        self.codegen.set_signedness(var_ptr, True)
    self.codegen.local_decl_types[node.var_name] = type_name
    if node.var_name in self.codegen.array_metadata:
        del self.codegen.array_metadata[node.var_name]
    invalidate_strlen_facts(self.codegen, node.var_name)
    remember_assign_range(self.codegen, node.var_name, node.value)
    return True


def _try_emit_record_new_into_slot(
    self,
    var_ptr: ir.Value,
    llvm_type: ir.Type,
    var_name: str,
    init_value,
) -> bool:
    if not isinstance(init_value, NewExpr):
        return False
    type_name = init_value.type_name
    if type_name in getattr(self.codegen, "class_types", {}):
        return False
    record_type = self.codegen.record_types.get(type_name)
    fields = self.codegen.record_fields.get(type_name)
    if record_type is None or fields is None or llvm_type != record_type:
        return False
    visible_fields = [
        field for field in fields if not str(field[0]).startswith("__ailang_")
    ]
    if len(init_value.args) != len(visible_fields):
        return False
    for index, (field_info, arg_expr) in enumerate(
        zip(visible_fields, init_value.args, strict=False)
    ):
        field_name, field_type_name = field_info
        field_type = self.codegen.get_llvm_type(field_type_name)
        arg_value = self.codegen.generate_expr(arg_expr)
        arg_value = cast_to_declared_int(
            self.codegen, arg_value, field_type, field_type_name
        )
        field_index, _ = self.codegen.get_field_info(type_name, field_name)
        field_ptr = self.builder.gep(
            var_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
            name=f"{var_name}_{index}_ptr",
        )
        self.builder.store(arg_value, field_ptr)
        if _is_string_type_name(field_type_name):
            _store_record_string_len(
                self, var_ptr, type_name, field_name, arg_expr, arg_value
            )
    return True


def _store_record_string_len(
    self,
    var_ptr: ir.Value,
    type_name: str,
    field_name: str,
    arg_expr,
    arg_value: ir.Value,
) -> None:
    hidden_name = f"__ailang_{field_name}_len"
    try:
        hidden_idx, _ = self.codegen.get_field_info(type_name, hidden_name)
    except Exception:
        return
    hidden_ptr = self.builder.gep(
        var_ptr,
        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), hidden_idx)],
        name=f"{field_name}_len_ptr",
    )
    self.builder.store(
        self.codegen.expr_generator.call_emitter._emit_string_len_for_expr(
            arg_expr, arg_value
        ),
        hidden_ptr,
    )


def _is_string_type_name(type_name) -> bool:
    return str(type_name).strip().lower() in {"string", "str"}


def visit_TupleAssign(self, node: TupleAssign) -> None:
    """Handle tuple unpacking by evaluating RHS values before storing them."""
    temp_values: list[ir.Value] = []
    for value_expr in node.values:
        val = self.codegen.generate_expr(value_expr)
        if isinstance(val, tuple) and len(val) == 3:
            val = val[0]
        temp_values.append(val)
    if len(temp_values) != len(node.var_names):
        raise StmtGenError(
            f"Tuple unpacking mismatch: {len(node.var_names)} targets, "
            f"{len(temp_values)} values"
        )
    for var_name, value in zip(node.var_names, temp_values, strict=False):
        if var_name in self.codegen.locals:
            slot = self.codegen.locals[var_name]
            if isinstance(slot.type, ir.PointerType):
                target_type = slot.type.pointee
                if value.type != target_type:
                    value = self.codegen.cast_value(value, target_type)
                self.builder.store(value, slot)
            else:
                self.codegen.locals[var_name] = value
        else:
            var_ptr = self.codegen.alloca_in_entry_block(value.type, var_name)
            self.builder.store(value, var_ptr)
            self.codegen.locals[var_name] = var_ptr

"""LLVM statement visitors for field/dict assignment and control flow."""

from __future__ import annotations

from parser.ast import (
    Call,
    DictAssign,
    FieldAccess,
    FieldAssign,
    If,
    ThisExpr,
    Variable,
)

from codegen.strlen_fact_cache import clear_strlen_facts
from llvmlite import ir
from transpiler.codegen_int_ranges import (
    clear_codegen_int_proofs,
    expr_contains_call,
    merge_codegen_ranges,
    remember_field_assign_range,
    restore_codegen_ranges,
    snapshot_codegen_ranges,
)
from transpiler.llvm_fixed_dicts import try_fixed_dict_assign
from transpiler.llvm_fixed_int_casts import (
    cast_to_declared_int,
    fixed_int_info_for_spec,
)
from transpiler.local_constant_flow import (
    branch_assigned_names,
    forget_local_constants,
    restore_local_constants,
    snapshot_local_constants,
)


def _is_string_type(type_name: object) -> bool:
    return str(type_name).strip().lower() in {"string", "str"}


def _same_object_expr(left, right) -> bool:
    if isinstance(left, ThisExpr) and isinstance(right, ThisExpr):
        return True
    if isinstance(left, Variable) and isinstance(right, Variable):
        return left.name == right.name
    return False


def _try_emit_field_array_push_in_place(
    self, node: FieldAssign, field_ptr: ir.Value, field_type_str: str
) -> bool:
    """Lower field = array_push(field, value) without a generic call/store roundtrip."""
    if str(field_type_str).lower() != "array":
        return False
    value = node.value
    if (
        not isinstance(value, Call)
        or value.name != "array_push"
        or len(value.args) != 2
    ):
        return False
    array_arg, push_arg = value.args
    if not isinstance(array_arg, FieldAccess):
        return False
    if array_arg.field_name != node.field_name:
        return False
    if not _same_object_expr(array_arg.object_expr, node.object_expr):
        return False

    i64 = ir.IntType(64)
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    data_ptr = self.builder.load(field_ptr, name=f"{node.field_name}_data")
    if str(data_ptr.type) != "i64*":
        data_ptr = self.builder.bitcast(data_ptr, i64.as_pointer(), name="arr_i64cast")

    hdr = self.builder.gep(data_ptr, [ir.Constant(i32, -2)], name="arr_hdr")
    cap_ptr = self.builder.gep(hdr, [ir.Constant(i32, 1)], name="arr_cap_ptr")
    len_val = self.builder.load(hdr, name="arr_len")
    cap_val = self.builder.load(cap_ptr, name="arr_cap")
    need_grow = self.builder.icmp_unsigned(">=", len_val, cap_val)

    grow_block = self.func.append_basic_block("arr_field_grow")
    ok_block = self.func.append_basic_block("arr_field_ok")
    merge_block = self.func.append_basic_block("arr_field_push_merge")
    self.builder.cbranch(need_grow, grow_block, ok_block)

    self.builder.position_at_end(grow_block)
    one = ir.Constant(i64, 1)
    new_cap = self.builder.select(
        self.builder.icmp_unsigned("==", cap_val, ir.Constant(i64, 0)),
        one,
        self.builder.mul(cap_val, ir.Constant(i64, 2), name="arr_cap2"),
    )
    bytes_needed = self.builder.add(
        self.builder.mul(new_cap, ir.Constant(i64, 8)),
        ir.Constant(i64, 16),
        name="arr_bytes2",
    )
    raw_base = self.builder.gep(data_ptr, [ir.Constant(i32, -2)], name="arr_raw_base")
    raw_base_i8 = self.builder.bitcast(raw_base, i8_ptr)
    new_raw = self.builder.call(
        self.codegen.get_realloc(), [raw_base_i8, bytes_needed], name="arr_realloc"
    )
    new_i64 = self.builder.bitcast(new_raw, i64.as_pointer(), name="arr_realloc_i64")
    new_hdr = new_i64
    new_cap_ptr = self.builder.gep(
        new_hdr, [ir.Constant(i32, 1)], name="arr_new_cap_ptr"
    )
    self.builder.store(new_cap, new_cap_ptr)
    new_data = self.builder.gep(new_i64, [ir.Constant(i32, 2)], name="arr_new_data")
    self.builder.store(new_data, field_ptr)
    self.builder.branch(merge_block)
    grow_end = self.builder.block

    self.builder.position_at_end(ok_block)
    self.builder.branch(merge_block)
    ok_end = self.builder.block

    self.builder.position_at_end(merge_block)
    data_phi = self.builder.phi(i64.as_pointer(), name="arr_data_phi")
    hdr_phi = self.builder.phi(i64.as_pointer(), name="arr_hdr_phi")
    data_phi.add_incoming(data_ptr, ok_end)
    hdr_phi.add_incoming(hdr, ok_end)
    data_phi.add_incoming(new_data, grow_end)
    hdr_phi.add_incoming(new_hdr, grow_end)

    push_value = self.codegen.ensure_int64(self.codegen.generate_expr(push_arg))
    len_cur = self.builder.load(hdr_phi, name="arr_len_cur")
    dest_ptr = self.builder.gep(data_phi, [len_cur], name="arr_dest")
    self.builder.store(push_value, dest_ptr)
    len_next = self.builder.add(len_cur, ir.Constant(i64, 1))
    self.builder.store(len_next, hdr_phi)
    return True


def visit_FieldAssign(self, node: FieldAssign):
    object_ptr = None
    if isinstance(node.object_expr, Variable):
        storage = self.codegen.locals.get(node.object_expr.name)
        if isinstance(getattr(storage, "type", None), ir.PointerType) and isinstance(
            storage.type.pointee, ir.LiteralStructType
        ):
            object_ptr = storage
    if object_ptr is None:
        object_ptr = self.codegen.generate_expr(node.object_expr)
    if not isinstance(object_ptr.type, ir.PointerType):
        raise TypeError("Field assignment requires pointer operand")
    struct_type = object_ptr.type.pointee
    record_name = self.codegen.get_record_name_from_type(struct_type)
    field_idx, field_type_str = self.codegen.get_field_info(
        record_name, node.field_name
    )
    field_ptr = self.builder.gep(
        object_ptr,
        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_idx)],
        name=f"{node.field_name}_ptr",
    )
    field_type = self.codegen.get_llvm_type(field_type_str)
    if _try_emit_field_array_push_in_place(self, node, field_ptr, field_type_str):
        return
    value = self.codegen.generate_expr(node.value)
    if isinstance(value, tuple) and len(value) == 3:
        value = value[0]
    if value.type != field_type or (
        isinstance(value.type, ir.IntType)
        and fixed_int_info_for_spec(self.codegen, field_type_str) is not None
    ):
        value = cast_to_declared_int(self.codegen, value, field_type, field_type_str)
    self.builder.store(value, field_ptr)
    remember_field_assign_range(
        self.codegen, node.object_expr, node.field_name, node.value
    )
    if expr_contains_call(node.value):
        clear_codegen_int_proofs(self.codegen)
    if _is_string_type(field_type_str):
        hidden_name = f"__ailang_{node.field_name}_len"
        try:
            hidden_idx, _ = self.codegen.get_field_info(record_name, hidden_name)
        except Exception:
            hidden_idx = -1
        if hidden_idx >= 0:
            hidden_ptr = self.builder.gep(
                object_ptr,
                [
                    ir.Constant(ir.IntType(32), 0),
                    ir.Constant(ir.IntType(32), hidden_idx),
                ],
                name=f"{node.field_name}_len_ptr",
            )
            self.builder.store(
                self.codegen.expr_generator.call_emitter._emit_string_len_for_expr(
                    node.value, value
                ),
                hidden_ptr,
            )


def visit_DictAssign(self, node: DictAssign):
    """Handle dict[key] = value assignment."""
    has_call = (
        expr_contains_call(node.dict_expr)
        or expr_contains_call(node.key_expr)
        or expr_contains_call(node.value_expr)
    )
    if try_fixed_dict_assign(self.codegen, node):
        if has_call:
            clear_codegen_int_proofs(self.codegen)
        return

    # This also handles array subscript assignment
    dict_result = self.codegen.generate_expr(node.dict_expr)
    key_val = self.codegen.generate_expr(node.key_expr)
    value = self.codegen.generate_expr(node.value_expr)
    fixed_elem_spec = None
    if isinstance(node.dict_expr, Variable):
        local_slot = self.codegen.locals.get(node.dict_expr.name)
        if isinstance(getattr(local_slot, "type", None), ir.PointerType) and isinstance(
            local_slot.type.pointee, ir.ArrayType
        ):
            dict_result = local_slot
            declared = self.codegen.local_decl_types.get(node.dict_expr.name)
            try:
                resolved = self.codegen._resolve_type_alias_spec(declared)
            except Exception:
                resolved = declared
            if (
                isinstance(resolved, tuple)
                and len(resolved) >= 2
                and resolved[0] == "fixed_array"
            ):
                fixed_elem_spec = resolved[1]
            elif (
                isinstance(resolved, str)
                and resolved.startswith("[")
                and resolved.endswith("]")
                and ";" in resolved
            ):
                fixed_elem_spec = resolved[1:-1].rsplit(";", 1)[0].strip()
    # Handle global arrays which return (ptr, len, elem_type) tuple
    if isinstance(dict_result, tuple) and len(dict_result) == 3:
        array_ptr, _array_len, elem_type = dict_result
        key_i32 = self.builder.trunc(
            self.codegen.expr_generator.ensure_int64(key_val),
            ir.IntType(32),
            name="idx32",
        )
        zero = ir.Constant(ir.IntType(32), 0)
        elem_ptr = self.builder.gep(array_ptr, [zero, key_i32], name="elem_ptr")
        # Cast value to element type if needed
        if value.type != elem_type:
            value = self.codegen.cast_value(value, elem_type)
        self.builder.store(value, elem_ptr)
        if has_call:
            clear_codegen_int_proofs(self.codegen)
        return
    dict_ptr = dict_result
    # Check if this is a dict or array based on type
    if isinstance(dict_ptr.type, ir.PointerType):
        pointee = dict_ptr.type.pointee
        if isinstance(pointee, ir.LiteralStructType):
            # It's a dict - use dict_set with type tagging
            dict_set = self.codegen.get_dict_set_func()
            # Detect type tag BEFORE converting
            type_tag = self.codegen.expr_generator._get_dict_type_tag(value)
            # Convert value to i64 (preserving bits)
            value_i64 = self.codegen.expr_generator._convert_dict_value(value)
            self.builder.call(dict_set, [dict_ptr, key_val, value_i64, type_tag])
        elif isinstance(pointee, ir.ArrayType):
            # It's a pointer to a fixed-size array [N x T]*
            # Need two indices: first to dereference ptr, second for element
            key_i32 = self.builder.trunc(
                self.codegen.expr_generator.ensure_int64(key_val),
                ir.IntType(32),
                name="idx32",
            )
            zero = ir.Constant(ir.IntType(32), 0)
            elem_ptr = self.builder.gep(dict_ptr, [zero, key_i32], name="elem_ptr")
            # Cast value to element type if needed
            elem_type = pointee.element
            if fixed_elem_spec is not None:
                value = cast_to_declared_int(
                    self.codegen, value, elem_type, fixed_elem_spec
                )
            elif value.type != elem_type:
                value = self.codegen.cast_value(value, elem_type)
            self.builder.store(value, elem_ptr)
        else:
            # It's a pointer to elements (T*) - single index (dynamic array)
            key_i64 = self.codegen.expr_generator.ensure_int64(key_val)
            # Dynamic bounds check - assumes array has header at offset -2
            # Skip if unsafe flag is set on the original node
            if not getattr(node, "unsafe", False):
                hdr_ptr = self.builder.gep(
                    dict_ptr,
                    [ir.Constant(ir.IntType(32), -2)],
                    name="dyn_arr_hdr",
                )
                arr_length = self.builder.load(hdr_ptr, name="dyn_arr_len")
                self.codegen.check_bounds_dynamic(key_i64, arr_length)
            elem_ptr = self.builder.gep(dict_ptr, [key_i64], name="elem_ptr")
            self.builder.store(value, elem_ptr)
    else:
        raise TypeError("Subscript assignment requires pointer type")
    if has_call:
        clear_codegen_int_proofs(self.codegen)


def visit_If(self, node: If):
    before_ranges = snapshot_codegen_ranges(self.codegen)
    before_constants = snapshot_local_constants(self.codegen)
    assigned_names = branch_assigned_names(node)
    cond = self.codegen.to_bool(self.codegen.generate_expr(node.cond))
    then_block = self.func.append_basic_block(name="then")
    if node.else_body:
        else_block = self.func.append_basic_block(name="else")
    else:
        else_block = None
    merge_block = self.func.append_basic_block(name="merge")
    if else_block:
        self.builder.cbranch(cond, then_block, else_block)
    else:
        self.builder.cbranch(cond, then_block, merge_block)
    self.builder.position_at_end(then_block)
    restore_codegen_ranges(self.codegen, before_ranges)
    restore_local_constants(self.codegen, before_constants)
    for stmt in node.then_body:
        self.generate_stmt(stmt)
    then_ranges = snapshot_codegen_ranges(self.codegen)
    if not self.builder.block.is_terminated:
        self.builder.branch(merge_block)
    if else_block:
        self.builder.position_at_end(else_block)
        restore_codegen_ranges(self.codegen, before_ranges)
        restore_local_constants(self.codegen, before_constants)
        for stmt in node.else_body:
            self.generate_stmt(stmt)
        else_ranges = snapshot_codegen_ranges(self.codegen)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_block)
    else:
        else_ranges = before_ranges
    self.builder.position_at_end(merge_block)
    merge_codegen_ranges(self.codegen, then_ranges, else_ranges, source_if=node)
    clear_strlen_facts(self.codegen)
    restore_local_constants(self.codegen, before_constants)
    forget_local_constants(self.codegen, assigned_names)

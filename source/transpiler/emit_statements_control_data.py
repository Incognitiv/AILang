"""LLVM statement visitors for field/dict assignment and control flow."""

from __future__ import annotations

from parser.ast import (
    Block,
    Call,
    DictAssign,
    DoWhile,
    FieldAccess,
    FieldAssign,
    For,
    Foreach,
    If,
    Loop,
    Range,
    Repeat,
    ThisExpr,
    Variable,
    While,
)

from ast_access import param_at
from llvmlite import ir

from .emit_statements_cleanup import _emit_stack_class_cleanup
from .emit_statements_common import StmtGenError


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


def _cleanup_immediate_stack_class_locals(self, body: list) -> None:
    _cleanup_current_loop_stack_class_locals(self)


def _push_loop_stack_class_cleanup(self) -> None:
    self.codegen._loop_stack_class_cleanup.append([])


def _pop_loop_stack_class_cleanup(self) -> None:
    if getattr(self.codegen, "_loop_stack_class_cleanup", []):
        self.codegen._loop_stack_class_cleanup.pop()


def _cleanup_current_loop_stack_class_locals(self) -> None:
    cleanup_stack = getattr(self.codegen, "_loop_stack_class_cleanup", [])
    if not cleanup_stack:
        return
    for var_name in reversed(cleanup_stack[-1]):
        _emit_stack_class_cleanup(self, var_name)


def _block_times(self, count_val: ir.Value, block: Block) -> None:
    """Implement n.times |i| block"""
    int64 = ir.IntType(64)
    # Ensure count is i64
    if count_val.type != int64:
        count_val = self.builder.sext(count_val, int64)
    # Create loop - use entry block alloca for optimization
    idx_ptr = self.codegen.alloca_in_entry_block(int64, "times_idx")
    self.builder.store(ir.Constant(int64, 0), idx_ptr)
    cond_block = self.func.append_basic_block("times_cond")
    body_block = self.func.append_basic_block("times_body")
    exit_block = self.func.append_basic_block("times_exit")
    self.codegen.loop_stack.append((cond_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.builder.branch(cond_block)
    # Condition
    self.builder.position_at_end(cond_block)
    idx = self.builder.load(idx_ptr)
    cond = self.builder.icmp_signed("<", idx, count_val)
    self.builder.cbranch(cond, body_block, exit_block)
    # Body
    self.builder.position_at_end(body_block)
    # Bind block parameter (the index)
    if block.params:
        param_name = param_at(block, 0)
        param_ptr = self.codegen.alloca_in_entry_block(int64, param_name)
        self.builder.store(idx, param_ptr)
        prev_binding = self.codegen.locals.get(param_name)
        self.codegen.locals[param_name] = param_ptr
    # Execute block body
    for stmt in block.body:
        self.generate_stmt(stmt)
    # Restore binding
    if block.params:
        if prev_binding is not None:
            self.codegen.locals[param_at(block, 0)] = prev_binding
        else:
            self.codegen.locals.pop(param_at(block, 0), None)
    # Increment and loop
    if not self.builder.block.is_terminated:
        _cleanup_current_loop_stack_class_locals(self)
        next_idx = self.builder.add(idx, ir.Constant(int64, 1))
        self.builder.store(next_idx, idx_ptr)
        self.builder.branch(cond_block)
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)


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
    if value.type != field_type:
        value = self.codegen.cast_value(value, field_type)
    self.builder.store(value, field_ptr)
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
    # This also handles array subscript assignment
    dict_result = self.codegen.generate_expr(node.dict_expr)
    key_val = self.codegen.generate_expr(node.key_expr)
    value = self.codegen.generate_expr(node.value_expr)
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
            if value.type != elem_type:
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


def visit_If(self, node: If):
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
    for stmt in node.then_body:
        self.generate_stmt(stmt)
    if not self.builder.block.is_terminated:
        self.builder.branch(merge_block)
    if else_block:
        self.builder.position_at_end(else_block)
        for stmt in node.else_body:
            self.generate_stmt(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_block)
    self.builder.position_at_end(merge_block)


def _close_streams_if_outer_loop(self) -> None:
    """Call stream close function when exiting outermost loop."""
    # Only close if the stream infrastructure was actually used
    if self.codegen.loop_depth == 0 and (self.codegen._stream_write_func is not None):
        self.builder.call(self.codegen.get_stream_close_func(), [])


def _setup_loop_bound(self, max_iterations, name: str):
    if max_iterations is None:
        return None, None
    counter = self.builder.alloca(ir.IntType(64), name=name)
    self.builder.store(ir.Constant(ir.IntType(64), 0), counter)
    return counter, self.codegen.generate_expr(max_iterations)


def _and_loop_bound(
    self, cond: ir.Value, counter: ir.Value | None, max_val
) -> ir.Value:
    if counter is None or max_val is None:
        return cond
    current_iter = self.builder.load(counter, name="current_iter")
    within_bound = self.builder.icmp_signed(
        "<", current_iter, max_val, name="within_bound"
    )
    return self.builder.and_(cond, within_bound, name="bounded_cond")


def _increment_loop_bound(self, counter: ir.Value | None) -> None:
    if counter is None:
        return
    current = self.builder.load(counter)
    incremented = self.builder.add(
        current, ir.Constant(ir.IntType(64), 1), name="iter_inc"
    )
    self.builder.store(incremented, counter)


def _enter_loop(self, continue_block: ir.Block, exit_block: ir.Block) -> None:
    self.codegen.loop_stack.append((continue_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.codegen.loop_depth += 1


def _leave_loop(self, exit_block: ir.Block) -> None:
    self.codegen.loop_depth -= 1
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)
    self._close_streams_if_outer_loop()


def _branch_on_loop_condition(
    self,
    condition_expr,
    iter_counter: ir.Value | None,
    max_val,
    body_block: ir.Block,
    exit_block: ir.Block,
) -> None:
    cond_val = self.codegen.generate_expr(condition_expr)
    cond = self.codegen.to_bool(cond_val)
    self.builder.cbranch(
        _and_loop_bound(self, cond, iter_counter, max_val),
        body_block,
        exit_block,
    )


def _emit_loop_body(
    self, body, iter_counter: ir.Value | None, next_block: ir.Block
) -> None:
    _increment_loop_bound(self, iter_counter)
    for stmt in body:
        self.generate_stmt(stmt)
    if not self.builder.block.is_terminated:
        _cleanup_current_loop_stack_class_locals(self)
        self.builder.branch(next_block)


def visit_While(self, node: While):
    """Generate while loop with optional max_iterations bound.
    If max_iterations is set, the loop will terminate after that many
    iterations even if the condition is still true.
    """
    cond_block = self.func.append_basic_block(name="while_cond")
    body_block = self.func.append_basic_block(name="while_body")
    exit_block = self.func.append_basic_block(name="while_exit")
    _enter_loop(self, cond_block, exit_block)
    iter_counter, max_val = _setup_loop_bound(self, node.max_iterations, "loop_iter")
    self.builder.branch(cond_block)
    self.builder.position_at_end(cond_block)
    _branch_on_loop_condition(
        self, node.cond, iter_counter, max_val, body_block, exit_block
    )
    self.builder.position_at_end(body_block)
    _emit_loop_body(self, node.body, iter_counter, cond_block)
    _leave_loop(self, exit_block)


def visit_DoWhile(self, node: DoWhile):
    """Generate do-while loop with optional max_iterations bound.
    Body executes first, then condition checked.
    This generates the LLVM-optimal 'rotated' loop form.
    """
    body_block = self.func.append_basic_block(name="dowhile_body")
    cond_block = self.func.append_basic_block(name="dowhile_cond")
    exit_block = self.func.append_basic_block(name="dowhile_exit")
    # For break/continue: continue goes to cond, break goes to exit
    self.codegen.loop_stack.append((cond_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.codegen.loop_depth += 1
    # If bounded, create iteration counter
    iter_counter = None
    max_val = None
    if node.max_iterations is not None:
        iter_counter = self.builder.alloca(ir.IntType(64), name="dowhile_iter")
        self.builder.store(ir.Constant(ir.IntType(64), 0), iter_counter)
        max_val = self.codegen.generate_expr(node.max_iterations)
    # Jump directly to body (no initial condition check)
    self.builder.branch(body_block)
    # Generate body
    self.builder.position_at_end(body_block)
    # Increment iteration counter at start of body
    if iter_counter is not None:
        current = self.builder.load(iter_counter)
        incremented = self.builder.add(
            current, ir.Constant(ir.IntType(64), 1), name="iter_inc"
        )
        self.builder.store(incremented, iter_counter)
    for stmt in node.body:
        self.generate_stmt(stmt)
    if not self.builder.block.is_terminated:
        _cleanup_current_loop_stack_class_locals(self)
        self.builder.branch(cond_block)
    # Generate condition check at the END (after body)
    self.builder.position_at_end(cond_block)
    cond_val = self.codegen.generate_expr(node.cond)
    cond = self.codegen.to_bool(cond_val)
    # If bounded, also check iteration count
    if iter_counter is not None and max_val is not None:
        current_iter = self.builder.load(iter_counter, name="current_iter")
        within_bound = self.builder.icmp_signed(
            "<", current_iter, max_val, name="within_bound"
        )
        cond = self.builder.and_(cond, within_bound, name="bounded_cond")
    self.builder.cbranch(cond, body_block, exit_block)
    self.codegen.loop_depth -= 1
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)
    self._close_streams_if_outer_loop()


def visit_For(self, node: For):
    """Generate for loop with optional max_iterations bound."""
    if node.init:
        self.generate_stmt(node.init)
    cond_block = self.func.append_basic_block(name="for_cond")
    body_block = self.func.append_basic_block(name="for_body")
    step_block = self.func.append_basic_block(name="for_step")
    exit_block = self.func.append_basic_block(name="for_exit")
    _enter_loop(self, step_block, exit_block)
    iter_counter, max_val = _setup_loop_bound(self, node.max_iterations, "for_iter")
    self.builder.branch(cond_block)
    self.builder.position_at_end(cond_block)
    _branch_on_loop_condition(
        self, node.cond, iter_counter, max_val, body_block, exit_block
    )
    self.builder.position_at_end(body_block)
    _emit_loop_body(self, node.body, iter_counter, step_block)
    self.builder.position_at_end(step_block)
    if node.step:
        self.generate_stmt(node.step)
    self.builder.branch(cond_block)
    _leave_loop(self, exit_block)


def visit_Loop(self, node: Loop):
    """Generate infinite loop with optional max_iterations bound.
    Infinite loops normally exit via break/return. If max_iterations
    is set, the loop will terminate after that many iterations.
    """
    body_block = self.func.append_basic_block(name="loop_body")
    exit_block = self.func.append_basic_block(name="loop_exit")
    self.codegen.loop_stack.append((body_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.codegen.loop_depth += 1
    # If bounded, create iteration counter and condition block
    iter_counter = None
    max_val = None
    cond_block = None
    if node.max_iterations is not None:
        cond_block = self.func.append_basic_block(name="loop_cond")
        iter_counter = self.builder.alloca(ir.IntType(64), name="loop_iter")
        self.builder.store(ir.Constant(ir.IntType(64), 0), iter_counter)
        max_val = self.codegen.generate_expr(node.max_iterations)
        self.builder.branch(cond_block)
        # Condition block: check if within bound
        self.builder.position_at_end(cond_block)
        current_iter = self.builder.load(iter_counter, name="current_iter")
        within_bound = self.builder.icmp_signed(
            "<", current_iter, max_val, name="within_bound"
        )
        self.builder.cbranch(within_bound, body_block, exit_block)
    else:
        self.builder.branch(body_block)
    self.builder.position_at_end(body_block)
    # Increment iteration counter at start of body
    if iter_counter is not None:
        current = self.builder.load(iter_counter)
        incremented = self.builder.add(
            current, ir.Constant(ir.IntType(64), 1), name="iter_inc"
        )
        self.builder.store(incremented, iter_counter)
    for stmt in node.body:
        self.generate_stmt(stmt)
    if not self.builder.block.is_terminated:
        _cleanup_current_loop_stack_class_locals(self)
        # Jump back to condition (if bounded) or body (if unbounded)
        if cond_block is not None:
            self.builder.branch(cond_block)
        else:
            self.builder.branch(body_block)
    self.codegen.loop_depth -= 1
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)


def visit_Repeat(self, node: Repeat):
    """Generate repeat N times loop using PHI nodes for efficiency.
    PHI nodes eliminate load/store overhead for the loop counter.
    """
    int64 = ir.IntType(64)
    # Evaluate the count expression
    limit_value = self.codegen.ensure_int64(self.codegen.generate_expr(node.count))
    # Create loop blocks
    cond_block = self.func.append_basic_block(name="repeat_cond")
    body_block = self.func.append_basic_block(name="repeat_body")
    step_block = self.func.append_basic_block(name="repeat_step")
    exit_block = self.func.append_basic_block(name="repeat_exit")
    # Save entry block reference for PHI
    entry_block = self.builder.block
    # Branch to condition block
    self.builder.branch(cond_block)
    # === Condition Block ===
    self.builder.position_at_end(cond_block)
    # Create PHI node for the counter
    # It receives 0 from entry, or incremented value from step
    counter_phi = self.builder.phi(int64, name="repeat_i")
    counter_phi.add_incoming(ir.Constant(int64, 0), entry_block)
    # Compare counter < limit
    cond = self.builder.icmp_signed(
        "<", counter_phi, limit_value, name="repeat_cond_check"
    )
    self.builder.cbranch(cond, body_block, exit_block)
    # === Body Block ===
    self.builder.position_at_end(body_block)
    # Push loop context for break/continue
    self.codegen.loop_stack.append((step_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.codegen.loop_depth += 1
    # Generate body statements
    for stmt in node.body:
        self.generate_stmt(stmt)
    # Fall through to step block if not terminated
    if not self.builder.block.is_terminated:
        _cleanup_current_loop_stack_class_locals(self)
        self.builder.branch(step_block)
    # === Step Block ===
    self.builder.position_at_end(step_block)
    # Increment counter
    counter_next = self.builder.add(
        counter_phi, ir.Constant(int64, 1), name="repeat_i_next"
    )
    # Add incoming edge to PHI from this block
    counter_phi.add_incoming(counter_next, step_block)
    # Loop back to condition
    self.builder.branch(cond_block)
    # Pop loop context
    self.codegen.loop_depth -= 1
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    # Continue at exit block
    self.builder.position_at_end(exit_block)
    self._close_streams_if_outer_loop()


def visit_Foreach(self, node: Foreach) -> None:
    """Handle foreach loops over arrays and ranges using PHI nodes.
    Supports:
        foreach x in array then ... end
        for i in 1..10 then ... end      (inclusive range)
        for i in 1...10 then ... end     (exclusive range)
    """
    # Check if iterating over a Range (1..10 or 1...10)
    if isinstance(node.iterable, Range):
        self._visit_foreach_range(node)
        return
    if not isinstance(node.iterable, Variable):
        raise StmtGenError("foreach currently only supports array variables or ranges")
    arr_name = node.iterable.name
    if arr_name not in self.codegen.array_metadata:
        raise StmtGenError(f"foreach on non-array variable: {arr_name}")
    array_len, elem_type = self.codegen.array_metadata[arr_name]
    previous_binding = self.codegen.locals.get(node.var_name)
    array_ptr = self.codegen.generate_expr(node.iterable)
    # Global arrays return (global_var, length, elem_type) tuple
    if isinstance(array_ptr, tuple):
        global_var, array_len, elem_type = array_ptr
        zero = ir.Constant(ir.IntType(32), 0)
        array_ptr = self.builder.gep(
            global_var, [zero, zero], name="foreach_global_ptr"
        )
    if not isinstance(array_ptr.type, ir.PointerType):
        raise TypeError("foreach iterable must evaluate to a pointer")
    typed_ptr = self.builder.bitcast(
        array_ptr, elem_type.as_pointer(), name="foreach_data"
    )
    int64 = ir.IntType(64)
    length_val = ir.Constant(int64, array_len)
    # Create blocks
    cond_block = self.func.append_basic_block(name="foreach_cond")
    body_block = self.func.append_basic_block(name="foreach_body")
    step_block = self.func.append_basic_block(name="foreach_step")
    exit_block = self.func.append_basic_block(name="foreach_exit")
    # Save entry block for PHI
    entry_block = self.builder.block
    self.builder.branch(cond_block)
    # === Condition Block ===
    self.builder.position_at_end(cond_block)
    # PHI for index - eliminates load/store overhead
    index_phi = self.builder.phi(int64, name="foreach_i")
    index_phi.add_incoming(ir.Constant(int64, 0), entry_block)
    # Compare index < length
    cond = self.builder.icmp_signed(
        "<", index_phi, length_val, name="foreach_cond_check"
    )
    self.builder.cbranch(cond, body_block, exit_block)
    # === Body Block ===
    self.builder.position_at_end(body_block)
    # Load element at current index
    elem_ptr = self.builder.gep(typed_ptr, [index_phi], name="foreach_elem_ptr")
    elem_val = self.builder.load(elem_ptr, name=node.var_name)
    # Loop variable still needs alloca for potential mutation in body
    loop_var_ptr = self.codegen.alloca_in_entry_block(elem_type, node.var_name)
    self.builder.store(elem_val, loop_var_ptr)
    self.codegen.locals[node.var_name] = loop_var_ptr
    # Push loop context
    self.codegen.loop_stack.append((step_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.codegen.loop_depth += 1
    for stmt in node.body:
        self.generate_stmt(stmt)
    if not self.builder.block.is_terminated:
        _cleanup_current_loop_stack_class_locals(self)
        self.builder.branch(step_block)
    # === Step Block ===
    self.builder.position_at_end(step_block)
    # Increment index
    index_next = self.builder.add(
        index_phi, ir.Constant(int64, 1), name="foreach_i_next"
    )
    index_phi.add_incoming(index_next, step_block)
    self.builder.branch(cond_block)
    self.codegen.loop_depth -= 1
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)
    self._close_streams_if_outer_loop()
    if previous_binding is not None:
        self.codegen.locals[node.var_name] = previous_binding
    else:
        self.codegen.locals.pop(node.var_name, None)

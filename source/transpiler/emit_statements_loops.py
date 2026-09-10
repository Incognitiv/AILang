"""LLVM loop and block-times statement emission."""

from __future__ import annotations

from parser.ast import (
    Block,
    DoWhile,
    For,
    Foreach,
    Loop,
    Range,
    Repeat,
    Variable,
    While,
)

from ast_access import param_at
from codegen.strlen_fact_cache import clear_strlen_facts
from llvmlite import ir
from transpiler.codegen_int_ranges import (
    clear_loop_variant_ranges,
    prepare_while_loop_ranges,
)
from transpiler.control_loop_utils import (
    and_loop_bound,
    close_streams_if_outer_loop,
    increment_loop_bound,
    setup_loop_bound,
)
from transpiler.local_constant_flow import (
    clear_local_constants,
    forget_local_constants,
    loop_assigned_names,
)

from .emit_statements_cleanup import _emit_stack_class_cleanup
from .emit_statements_common import StmtGenError


def _clear_local_constants(self) -> None:
    clear_local_constants(self.codegen)


def _forget_loop_local_constants(self, node) -> None:
    forget_local_constants(self.codegen, loop_assigned_names(node))


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


def _enter_loop(self, continue_block: ir.Block, exit_block: ir.Block, node) -> None:
    _forget_loop_local_constants(self, node)
    self.codegen.loop_stack.append((continue_block, exit_block))
    _push_loop_stack_class_cleanup(self)
    self.codegen.loop_depth += 1


def _leave_loop(self, exit_block: ir.Block, node) -> None:
    self.codegen.loop_depth -= 1
    _pop_loop_stack_class_cleanup(self)
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)
    clear_strlen_facts(self.codegen)
    _forget_loop_local_constants(self, node)
    close_streams_if_outer_loop(self)


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
        and_loop_bound(self, cond, iter_counter, max_val),
        body_block,
        exit_block,
    )


def _emit_loop_body(
    self, body, iter_counter: ir.Value | None, next_block: ir.Block
) -> None:
    increment_loop_bound(self, iter_counter)
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
    prepare_while_loop_ranges(self.codegen, node)
    _enter_loop(self, cond_block, exit_block, node)
    iter_counter, max_val = setup_loop_bound(self, node.max_iterations, "loop_iter")
    self.builder.branch(cond_block)
    self.builder.position_at_end(cond_block)
    _branch_on_loop_condition(
        self, node.cond, iter_counter, max_val, body_block, exit_block
    )
    self.builder.position_at_end(body_block)
    _emit_loop_body(self, node.body, iter_counter, cond_block)
    _leave_loop(self, exit_block, node)


def visit_DoWhile(self, node: DoWhile):
    """Generate do-while loop with optional max_iterations bound.
    Body executes first, then condition checked.
    This generates the LLVM-optimal 'rotated' loop form.
    """
    body_block = self.func.append_basic_block(name="dowhile_body")
    cond_block = self.func.append_basic_block(name="dowhile_cond")
    exit_block = self.func.append_basic_block(name="dowhile_exit")
    clear_loop_variant_ranges(self.codegen, node.body)
    # For break/continue: continue goes to cond, break goes to exit
    self.codegen.loop_stack.append((cond_block, exit_block))
    _forget_loop_local_constants(self, node)
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
    _forget_loop_local_constants(self, node)
    close_streams_if_outer_loop(self)


def visit_For(self, node: For):
    """Generate for loop with optional max_iterations bound."""
    if node.init:
        self.generate_stmt(node.init)
    cond_block = self.func.append_basic_block(name="for_cond")
    body_block = self.func.append_basic_block(name="for_body")
    step_block = self.func.append_basic_block(name="for_step")
    exit_block = self.func.append_basic_block(name="for_exit")
    clear_loop_variant_ranges(self.codegen, node.body)
    if node.step:
        clear_loop_variant_ranges(self.codegen, [node.step])
    _enter_loop(self, step_block, exit_block, node)
    iter_counter, max_val = setup_loop_bound(self, node.max_iterations, "for_iter")
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
    _leave_loop(self, exit_block, node)


def visit_Loop(self, node: Loop):
    """Generate infinite loop with optional max_iterations bound.
    Infinite loops normally exit via break/return. If max_iterations
    is set, the loop will terminate after that many iterations.
    """
    body_block = self.func.append_basic_block(name="loop_body")
    exit_block = self.func.append_basic_block(name="loop_exit")
    clear_loop_variant_ranges(self.codegen, node.body)
    self.codegen.loop_stack.append((body_block, exit_block))
    _forget_loop_local_constants(self, node)
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
    _forget_loop_local_constants(self, node)


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
    clear_loop_variant_ranges(self.codegen, node.body)
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
    _forget_loop_local_constants(self, node)
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
    _forget_loop_local_constants(self, node)
    close_streams_if_outer_loop(self)


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
    _forget_loop_local_constants(self, node)
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
    close_streams_if_outer_loop(self)
    if previous_binding is not None:
        self.codegen.locals[node.var_name] = previous_binding
    else:
        self.codegen.locals.pop(node.var_name, None)

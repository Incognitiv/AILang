from __future__ import annotations

from parser.ast import Block

from ast_access import param_at
from llvmlite import ir

from .emit_statements_common import StmtGenError
from .emit_statements_control_data import _cleanup_current_loop_stack_class_locals


def _block_each(self, array_obj: ir.Value, block: Block) -> None:
    """Implement .each |x| block for arrays"""
    # Get array metadata
    # For now, assume array_obj is from a variable with known metadata
    # This is a simplified implementation
    # Get the variable name to look up metadata
    var_name = getattr(array_obj, "name", "").replace("_val", "")
    if var_name.endswith("_slot"):
        var_name = var_name[:-5]
    meta = self.codegen.array_metadata.get(var_name)
    if not meta:
        raise StmtGenError("Cannot iterate: unknown array size")
    array_len, _elem_type = meta  # _elem_type reserved for future typed iteration
    int64 = ir.IntType(64)
    # Create loop - use entry block alloca for optimization
    idx_ptr = self.codegen.alloca_in_entry_block(int64, "each_idx")
    self.builder.store(ir.Constant(int64, 0), idx_ptr)
    cond_block = self.func.append_basic_block("each_cond")
    body_block = self.func.append_basic_block("each_body")
    exit_block = self.func.append_basic_block("each_exit")
    self.codegen.loop_stack.append((cond_block, exit_block))
    self.codegen._loop_stack_class_cleanup.append([])
    self.builder.branch(cond_block)
    # Condition
    self.builder.position_at_end(cond_block)
    idx = self.builder.load(idx_ptr)
    cond = self.builder.icmp_signed("<", idx, ir.Constant(int64, array_len))
    self.builder.cbranch(cond, body_block, exit_block)
    # Body
    self.builder.position_at_end(body_block)
    # Get element at index
    array_ptr = self.builder.load(self.codegen.locals.get(var_name, array_obj))
    idx_i32 = self.builder.trunc(idx, ir.IntType(32))
    elem_ptr = self.builder.gep(
        array_ptr,
        [ir.Constant(ir.IntType(32), 0), idx_i32],
        name="elem_ptr",
    )
    elem_val = self.builder.load(elem_ptr, name="elem")
    # Bind block parameter
    if block.params:
        param_name = param_at(block, 0)
        param_ptr = self.codegen.alloca_in_entry_block(elem_val.type, param_name)
        self.builder.store(elem_val, param_ptr)
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
    if getattr(self.codegen, "_loop_stack_class_cleanup", []):
        self.codegen._loop_stack_class_cleanup.pop()
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)

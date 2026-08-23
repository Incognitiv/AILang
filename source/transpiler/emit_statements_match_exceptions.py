"""LLVM statement visitors for match lowering, exceptions, and helpers."""

from __future__ import annotations

from parser.ast import (
    Foreach,
    Match,
    MatchPattern,
    Range,
    Throw,
    TryExcept,
    Variable,
)
from typing import cast

from ast_access import arg_at
from llvmlite import ir


def visit_Match(self, node: Match):
    match_val = self.codegen.generate_expr(node.expr)
    if isinstance(match_val, tuple) and len(match_val) == 3:
        match_val = match_val[0]
    # Check if any case uses MatchPattern (destructuring)
    has_patterns = any(
        isinstance(case_expr, MatchPattern) for case_expr, _ in node.cases
    )
    if has_patterns:
        self._generate_pattern_match(node, match_val)
        return
    merge_block = self.func.append_basic_block(name="match_merge")
    case_blocks = [
        self.func.append_basic_block(name=f"match_case_{i}")
        for i, _ in enumerate(node.cases)
    ]
    default_block = (
        self.func.append_basic_block(name="match_default")
        if node.default_case
        else merge_block
    )
    # Try to use LLVM switch instruction for integer constants
    # This generates a jump table for O(1) dispatch
    if self._can_use_switch(node, match_val):
        self._generate_switch(node, match_val, case_blocks, default_block)
    else:
        # Fall back to sequential comparisons for non-constant cases
        self._generate_sequential_match(node, match_val, case_blocks, default_block)
    # Generate case bodies
    for index, (_, case_body) in enumerate(node.cases):
        self.builder.position_at_end(case_blocks[index])
        for stmt in case_body:
            self.generate_stmt(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_block)
    if node.default_case:
        self.builder.position_at_end(default_block)
        for stmt in node.default_case:
            self.generate_stmt(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_block)
    self.builder.position_at_end(merge_block)


def _generate_pattern_match(self, node: Match, match_val: ir.Value) -> None:
    """Generate code for match with destructuring patterns."""
    merge_block = self.func.append_basic_block(name="match_merge")
    # For data enums, match_val should be a pointer to the enum struct
    # Get the tag value for comparison
    if isinstance(match_val.type, ir.PointerType):
        tag_ptr = self.builder.gep(
            match_val,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
            name="tag_ptr",
        )
        tag_val = self.builder.load(tag_ptr, name="tag")
    else:
        # If it's already an integer, use it directly
        tag_val = match_val
    case_blocks = [
        self.func.append_basic_block(name=f"pattern_case_{i}")
        for i, _ in enumerate(node.cases)
    ]
    compare_blocks = [
        self.func.append_basic_block(name=f"pattern_cmp_{i}")
        for i in range(len(node.cases))
    ]
    default_block = (
        self.func.append_basic_block(name="pattern_default")
        if node.default_case
        else merge_block
    )
    # Branch to first comparison
    if compare_blocks:
        self.builder.branch(compare_blocks[0])
    else:
        self.builder.branch(default_block)
    # Generate comparisons
    for index, (case_expr, _case_body) in enumerate(node.cases):
        self.builder.position_at_end(compare_blocks[index])
        if isinstance(case_expr, MatchPattern):
            enum_name = case_expr.enum_name
            variant_name = case_expr.variant_name
            # Get the expected tag value
            if enum_name in self.codegen.data_enum_tags:
                expected_tag = self.codegen.data_enum_tags[enum_name][variant_name]
            else:
                full_name = f"{enum_name}.{variant_name}"
                expected_tag = self.codegen.enum_values.get(full_name, 0)
            expected_tag_val = ir.Constant(ir.IntType(32), expected_tag)
            cond = self.builder.icmp_signed(
                "==", tag_val, expected_tag_val, name="tag_match"
            )
        else:
            # Regular expression comparison
            case_val = self.codegen.generate_expr(case_expr)
            if isinstance(case_val, tuple) and len(case_val) == 3:
                case_val = case_val[0]
            cond = self._values_equal(tag_val, case_val)
        next_block = (
            compare_blocks[index + 1] if index + 1 < len(node.cases) else default_block
        )
        self.builder.cbranch(cond, case_blocks[index], next_block)
    # Generate case bodies with binding extraction
    for index, (case_expr, case_body) in enumerate(node.cases):
        self.builder.position_at_end(case_blocks[index])
        # Extract bindings for MatchPattern
        if isinstance(case_expr, MatchPattern) and case_expr.bindings:
            self._extract_pattern_bindings(match_val, case_expr)
        # Generate body statements
        for stmt in case_body:
            self.generate_stmt(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_block)
    # Generate default case
    if node.default_case:
        self.builder.position_at_end(default_block)
        for stmt in node.default_case:
            self.generate_stmt(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_block)
    self.builder.position_at_end(merge_block)


def _extract_pattern_bindings(self, match_val: ir.Value, pattern: MatchPattern) -> None:
    """Extract bound variables from a matched enum variant."""
    enum_name = pattern.enum_name
    variant_name = pattern.variant_name
    bindings = pattern.bindings
    if enum_name not in self.codegen.data_enums:
        return  # Not a data enum, nothing to extract
    variant_data = self.codegen.data_enums[enum_name]
    if variant_name not in variant_data:
        return
    fields = variant_data[variant_name]
    if not fields or not bindings:
        return
    # Get pointer to data array
    data_ptr = self.builder.gep(
        match_val,
        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
        name="data_ptr",
    )
    # Extract each bound field
    offset = 0
    for (field_name, field_type), binding in zip(fields, bindings, strict=False):
        field_llvm_type = self.codegen.get_llvm_type(field_type)
        # Get pointer to field
        field_ptr = self.builder.gep(
            data_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), offset)],
            name=f"field_{field_name}_byte_ptr",
        )
        # Cast and load
        typed_ptr = self.builder.bitcast(
            field_ptr, field_llvm_type.as_pointer(), name=f"field_{field_name}_ptr"
        )
        value = self.builder.load(typed_ptr, name=binding)
        # Store in locals for the body to use
        binding_ptr = self.builder.alloca(field_llvm_type, name=f"{binding}_ptr")
        self.builder.store(value, binding_ptr)
        self.codegen.locals[binding] = binding_ptr
        # Update offset
        offset += self.codegen._type_size(field_llvm_type)


def _can_use_switch(self, node: Match, match_val: ir.Value) -> bool:
    """Check if we can use LLVM switch instruction (all cases are int constants)"""
    from parser.ast import Call, Number

    # Match value must be an integer type
    if not isinstance(match_val.type, ir.IntType):
        return False
    # All case expressions must be integer constants
    for case_expr, _ in node.cases:
        # MatchPattern requires sequential comparison with destructuring
        if isinstance(case_expr, MatchPattern):
            return False
        if isinstance(case_expr, Number):
            continue
        # Handle ord("x") calls - these are constant
        if isinstance(case_expr, Call) and (
            isinstance(case_expr.name, Variable)
            and (case_expr.name.name == "ord" and len(case_expr.args) == 1)
        ):
            continue
        return False
    return True


def _generate_switch(
    self,
    node: Match,
    match_val: ir.Value,
    case_blocks: list,
    default_block: ir.Block,
):
    """Generate LLVM switch instruction for O(1) dispatch"""
    from parser.ast import Call, Number, StringLit

    switch = self.builder.switch(match_val, default_block)
    for index, (case_expr, _) in enumerate(node.cases):
        # Get the constant value
        if isinstance(case_expr, Number):
            const_val = ir.Constant(match_val.type, case_expr.value)
        elif isinstance(case_expr, Call):
            # Handle ord("x") - extract the character value
            arg = arg_at(case_expr, 0)
            if isinstance(arg, StringLit) and len(arg.value) >= 1:
                char_code = ord(arg.value[0])
                const_val = ir.Constant(match_val.type, char_code)
            else:
                # Shouldn't happen if _can_use_switch returned True
                const_val = ir.Constant(match_val.type, 0)
        else:
            const_val = ir.Constant(match_val.type, 0)
        switch.add_case(const_val, case_blocks[index])


def _generate_sequential_match(
    self,
    node: Match,
    match_val: ir.Value,
    case_blocks: list,
    default_block: ir.Block,
):
    """Generate sequential comparisons (fallback for non-constant cases)"""
    if node.cases:
        compare_blocks = [
            self.func.append_basic_block(name=f"match_cmp_{i}")
            for i in range(len(node.cases))
        ]
        self.builder.branch(compare_blocks[0])
    else:
        self.builder.branch(default_block)
        return
    for index, (case_expr, _) in enumerate(node.cases):
        self.builder.position_at_end(compare_blocks[index])
        case_val = self.codegen.generate_expr(case_expr)
        if isinstance(case_val, tuple) and len(case_val) == 3:
            case_val = case_val[0]
        if case_val.type != match_val.type:
            try:
                case_val = self.codegen.cast_value(case_val, match_val.type)
            except TypeError as exc:
                raise TypeError(
                    f"match arm {index}: cannot compare value of type "
                    f"{match_val.type} against case of type {case_val.type} "
                    f"({exc})"
                ) from exc
        cond = self._values_equal(match_val, case_val)
        next_block = (
            compare_blocks[index + 1] if index + 1 < len(node.cases) else default_block
        )
        self.builder.cbranch(cond, case_blocks[index], next_block)


def visit_TryExcept(self, node: TryExcept):
    self.codegen.generate_try_except(node)


def visit_Throw(self, node: Throw):
    from .emit_statements_basic import _cleanup_all_stack_class_locals

    _cleanup_all_stack_class_locals(self)
    self.codegen.generate_throw(node)


def _default_value(self, llvm_type: ir.Type) -> ir.Constant:
    return self.codegen.default_value(llvm_type)


def _values_equal(self, left: ir.Value, right: ir.Value) -> ir.Value:
    left_type = left.type
    right_type = right.type
    if isinstance(left_type, ir.PointerType) and isinstance(right_type, ir.PointerType):
        if left_type != right_type:
            right = self.codegen.cast_value(right, left_type)
            right_type = right.type
        if self._is_string_pointer(left_type) and self._is_string_pointer(right_type):
            cmp_res = self.builder.call(
                self.codegen.get_strcmp(), [left, right], name="match_strcmp"
            )
            zero = ir.Constant(ir.IntType(32), 0)
            return self.builder.icmp_signed("==", cmp_res, zero, name="match_str_eq")
        return self.builder.icmp_unsigned("==", left, right, name="match_ptr_eq")
    if isinstance(left_type, ir.IntType) and isinstance(right_type, ir.IntType):
        if left_type != right_type:
            right = self.codegen.cast_value(right, left_type)
        return self.builder.icmp_signed("==", left, right, name="match_int_eq")
    if isinstance(left_type, (ir.FloatType, ir.DoubleType)) and isinstance(
        right_type, (ir.FloatType, ir.DoubleType)
    ):
        if left_type != right_type:
            right = self.codegen.cast_value(right, left_type)
        return self.builder.fcmp_ordered("==", left, right, name="match_float_eq")
    raise TypeError("Unsupported types in match expression")


def _is_string_pointer(self, llvm_type: ir.Type) -> bool:
    if not isinstance(llvm_type, ir.PointerType):
        return False
    pointee = llvm_type.pointee
    return isinstance(pointee, ir.IntType) and pointee.width == 8


def _visit_foreach_range(self, node: Foreach) -> None:
    """Handle foreach over a Range: for i in 1..10 then ... end
    Generates a standard counting loop from start to end.
    For inclusive range (1..10): i goes from 1 to 10 (<=)
    For exclusive range (1...10): i goes from 1 to 9 (<)
    """
    range_node = cast(Range, node.iterable)
    # Evaluate start and end expressions
    start_val = self.codegen.generate_expr(range_node.start)
    end_val = self.codegen.generate_expr(range_node.end)
    # Ensure both are integers
    int64 = ir.IntType(64)
    if start_val.type != int64:
        start_val = self.builder.sext(start_val, int64, name="range_start")
    if end_val.type != int64:
        end_val = self.builder.sext(end_val, int64, name="range_end")
    # Save previous binding if loop var exists
    previous_binding = self.codegen.locals.get(node.var_name)
    # Allocate loop variable in entry block for optimization
    loop_var_ptr = self.codegen.alloca_in_entry_block(int64, node.var_name)
    self.builder.store(start_val, loop_var_ptr)
    self.codegen.locals[node.var_name] = loop_var_ptr
    # Create basic blocks
    cond_block = self.func.append_basic_block(name="range_cond")
    body_block = self.func.append_basic_block(name="range_body")
    step_block = self.func.append_basic_block(name="range_step")
    exit_block = self.func.append_basic_block(name="range_exit")
    self.codegen.loop_stack.append((step_block, exit_block))
    self.codegen._loop_stack_class_cleanup.append([])
    self.codegen.loop_depth += 1
    self.builder.branch(cond_block)
    # Condition: i <= end (inclusive) or i < end (exclusive)
    self.builder.position_at_end(cond_block)
    current_val = self.builder.load(loop_var_ptr, name="range_i")
    if range_node.inclusive:
        cond = self.builder.icmp_signed("<=", current_val, end_val, name="range_cond")
    else:
        cond = self.builder.icmp_signed("<", current_val, end_val, name="range_cond")
    self.builder.cbranch(cond, body_block, exit_block)
    # Body
    self.builder.position_at_end(body_block)
    for stmt in node.body:
        self.generate_stmt(stmt)
    if not self.builder.block.is_terminated:
        from .emit_statements_basic import _cleanup_current_loop_stack_class_locals

        _cleanup_current_loop_stack_class_locals(self)
        self.builder.branch(step_block)
    # Step: i = i + 1
    self.builder.position_at_end(step_block)
    current_val = self.builder.load(loop_var_ptr, name="range_i_step")
    next_val = self.builder.add(current_val, ir.Constant(int64, 1), name="range_inc")
    self.builder.store(next_val, loop_var_ptr)
    self.builder.branch(cond_block)
    # Exit
    self.codegen.loop_depth -= 1
    if getattr(self.codegen, "_loop_stack_class_cleanup", []):
        self.codegen._loop_stack_class_cleanup.pop()
    self.codegen.loop_stack.pop()
    self.builder.position_at_end(exit_block)
    self._close_streams_if_outer_loop()
    # Restore previous binding
    if previous_binding is not None:
        self.codegen.locals[node.var_name] = previous_binding
    else:
        self.codegen.locals.pop(node.var_name, None)

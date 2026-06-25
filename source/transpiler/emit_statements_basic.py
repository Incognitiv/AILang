"""Basic LLVM statement visitors: returns, declarations, assignments, blocks."""

from __future__ import annotations

from parser.ast import (
    ArrayLit,
    Assert,
    Assign,
    BlockCall,
    Break,
    Call,
    Continue,
    InlineAsm,
    NewExpr,
    RangeType,
    RangeVarDecl,
    Return,
    TupleAssign,
    TypeAlias,
    VarDecl,
    Variable,
    parsed_type_to_str,
)
from typing import cast

from codegen.strlen_fact_cache import (
    invalidate_strlen_facts,
    maybe_register_strlen_fact,
    register_strlen_fact,
)
from codegen.strlen_scalarization import try_emit_length_only_str_assignment
from llvmlite import ir
from target_info import os_from_triple

from .emit_statements_common import StmtGenError
from .stack_class_lowering import _try_emit_stack_class_vardecl


def visit_Return(self, node: Return):
    ret_type = self.func.function_type.return_type
    value = None
    skip_cleanup_names: set[str] = set()
    if node.value is not None and not isinstance(ret_type, ir.VoidType):
        # Evaluate the return value before RAII cleanup. If the returned value
        # is a local class pointer, ownership escapes to the caller, so its
        # destructor must not run in this callee.
        value = self.codegen.generate_expr(node.value)
        if isinstance(value, tuple) and len(value) == 3:
            value = value[0]
        if isinstance(node.value, Variable):
            local_type = getattr(self.codegen, "local_decl_types", {}).get(
                node.value.name
            )
            if isinstance(local_type, str) and local_type in getattr(
                self.codegen, "class_types", {}
            ):
                skip_cleanup_names.add(node.value.name)
            else:
                local_value = getattr(self.codegen, "locals", {}).get(node.value.name)
                if local_value is not None and isinstance(
                    getattr(local_value, "type", None), ir.PointerType
                ):
                    pointee = local_value.type.pointee
                    if isinstance(pointee, ir.PointerType):
                        struct_type = pointee.pointee
                        if isinstance(struct_type, ir.LiteralStructType):
                            class_name = self.codegen.get_record_name_from_type(
                                struct_type
                            )
                            if class_name in getattr(self.codegen, "class_types", {}):
                                skip_cleanup_names.add(node.value.name)
    _cleanup_all_stack_class_locals(self, skip_names=skip_cleanup_names)
    # Call destructors for all objects in current scope before returning (RAII)
    self.codegen.cleanup_all_scopes(skip_names=skip_cleanup_names)
    # Unlock @synchronized mutex before returning (Ada protected exit)
    if self.codegen._synchronized_mutex_ptr is not None:
        unlock_func = self.codegen.get_mutex_func("unlock")
        if unlock_func is not None:
            self.builder.call(unlock_func, [self.codegen._synchronized_mutex_ptr])
    # Destroy string arena before returning from main()
    if self.func.name == "main" and self.codegen._string_arena is not None:
        self.codegen._arena_gen.arena_destroy(self.codegen._string_arena)
    if isinstance(ret_type, ir.VoidType):
        if node.value is not None:
            raise TypeError(
                "Are you trying to return something in a void function? "
                "Void functions cannot return values. "
                "Either remove the return value or change the function to a non-void type."
            )
        # Decrement recursion depth before returning (skip in @unchecked mode)
        if self.codegen._function_needs_recursion_guard():
            self.codegen._emit_recursion_decrement()
        # Profile instrumentation: log explicit void return.
        self.codegen.emit_profile_exit(self.func.name)
        self.builder.ret_void()
        return
    if node.value is not None:
        if value is None:
            raise StmtGenError("return value expression produced no value")
        if value.type != ret_type:
            value = self.codegen.cast_value(value, ret_type)
        # Decrement recursion depth AFTER evaluating, BEFORE returning
        # Skip in @unchecked mode for maximum performance
        if self.codegen._function_needs_recursion_guard():
            self.codegen._emit_recursion_decrement()
        # Profile instrumentation: log explicit value return.
        self.codegen.emit_profile_exit(self.func.name)
        self.builder.ret(value)
    else:
        # Decrement recursion depth before returning (skip in @unchecked mode)
        if self.codegen._function_needs_recursion_guard():
            self.codegen._emit_recursion_decrement()
        # Profile instrumentation: log explicit default return.
        self.codegen.emit_profile_exit(self.func.name)
        self.builder.ret(self._default_value(ret_type))


def visit_Break(self, node: Break):
    if not self.codegen.loop_stack:
        raise StmtGenError("break statement outside of loop")
    _cleanup_current_loop_stack_class_locals(self)
    # Run RAII destructors for objects created in the loop scope
    self.codegen.pop_scope()
    _, break_block = self.codegen.loop_stack[-1]
    self.builder.branch(break_block)


def visit_Continue(self, node: Continue):
    if not self.codegen.loop_stack:
        raise StmtGenError("continue statement outside of loop")
    _cleanup_current_loop_stack_class_locals(self)
    # Run RAII destructors for objects created in the loop scope
    self.codegen.pop_scope()
    continue_block, _ = self.codegen.loop_stack[-1]
    self.builder.branch(continue_block)


def _cleanup_current_loop_stack_class_locals(self) -> None:
    cleanup_stack = getattr(self.codegen, "_loop_stack_class_cleanup", [])
    if not cleanup_stack:
        return
    from .emit_statements_control_data import _emit_stack_class_cleanup

    for var_name in reversed(cleanup_stack[-1]):
        _emit_stack_class_cleanup(self, var_name)


def _cleanup_all_stack_class_locals(self, skip_names: set[str] | None = None) -> None:
    plans = getattr(self.codegen, "_stack_class_cleanup_plans", {})
    if not plans:
        return
    from .emit_statements_control_data import _emit_stack_class_cleanup

    skip_names = skip_names or set()
    for var_name in reversed(list(plans)):
        if var_name in skip_names:
            continue
        _emit_stack_class_cleanup(self, var_name)


def visit_Assert(self, node: Assert):
    """Generate code for assert statement.
    assert condition [, message]
    If condition is false, prints assertion error and exits with code 1.
    """
    # Generate condition
    cond_val = self.codegen.generate_expr(node.condition)
    # Ensure condition is boolean (i1)
    if cond_val.type != ir.IntType(1):
        cond_val = self.builder.icmp_signed(
            "!=", cond_val, ir.Constant(cond_val.type, 0)
        )
    # Create blocks
    assert_fail_block = self.func.append_basic_block("assert_fail")
    assert_pass_block = self.func.append_basic_block("assert_pass")
    # Branch based on condition
    self.builder.cbranch(cond_val, assert_pass_block, assert_fail_block)
    # Generate failure path
    self.builder.position_at_end(assert_fail_block)
    # Print assertion error message
    if node.message:
        # Custom message provided - print it with printf %s format
        msg_val = self.codegen.generate_expr(node.message)
        printf = self.codegen.get_printf()
        fmt_str = self.codegen.create_string_constant("%s\n")
        self.builder.call(printf, [fmt_str, msg_val])
    else:
        # Default message
        error_msg = self.codegen.create_string_constant("Assertion failed!\n")
        printf = self.codegen.get_printf()
        self.builder.call(printf, [error_msg])
    # Catchable safety error or fatal exit
    self.codegen._emit_safety_trap("Assertion failed")
    # Continue on success path
    self.builder.position_at_end(assert_pass_block)


def visit_InlineAsm(self, node: InlineAsm):
    """Generate inline assembly instruction.
    Creates LLVM inline assembly that will be emitted directly to the output.
    Used for CPU-specific instructions like cli, hlt, etc.
    """
    # Create void inline assembly (no outputs, no inputs)
    asm_type = ir.FunctionType(ir.VoidType(), [])
    asm_func = ir.InlineAsm(asm_type, node.code, "", side_effect=True)
    self.builder.call(asm_func, [])


def visit_ComptimeExpr(self, node):
    """Evaluate compile-time expression.
    The expression is evaluated at compile time and the result
    is substituted as a constant in the generated code.
    """
    # For simple constant expressions, evaluate at compile time
    result = self._evaluate_comptime(node.expr)
    if result is not None:
        # Return the constant value
        if isinstance(result, int):
            return ir.Constant(ir.IntType(64), result)
        if isinstance(result, float):
            return ir.Constant(ir.DoubleType(), result)
        if isinstance(result, bool):
            return ir.Constant(ir.IntType(1), 1 if result else 0)
        if isinstance(result, str):
            return self.codegen.create_string_constant(result)
    # Fall back to runtime evaluation if can't compute at compile time
    return self.codegen.generate_expr(node.expr)


def visit_ComptimeBlock(self, node):
    """Execute compile-time block.
    All statements in the block are evaluated at compile time.
    Useful for computing constants or generating code.
    """
    for stmt in node.body:
        self.generate_stmt(stmt)


def visit_ComptimeIf(self, node):
    """Handle compile-time conditional.
    The condition is evaluated at compile time and only the
    appropriate branch is compiled into the output.
    """
    # Try to evaluate condition at compile time
    cond_result = self._evaluate_comptime(node.cond)
    if cond_result is not None:
        # Compile only the appropriate branch
        if cond_result:
            for stmt in node.then_body:
                self.generate_stmt(stmt)
        else:
            for stmt in node.else_body:
                self.generate_stmt(stmt)
    else:
        # Fall back to runtime conditional if can't evaluate
        # (This shouldn't normally happen with comptime if)
        raise StmtGenError("comptime if condition must be evaluable at compile time")


def visit_StaticAssert(self, node):
    """Handle static assertion.
    Evaluated at compile time; fails compilation if false.
    """
    result = self._evaluate_comptime(node.condition)
    if result is None:
        raise StmtGenError("static_assert condition must be evaluable at compile time")
    if not result:
        msg = node.message or "Static assertion failed"
        raise StmtGenError(f"static_assert failed: {msg}")


def visit_Call(self, node: Call):
    """Emit expression calls used as statements.

    `dealloc(a, b, c)` is statement sugar for sequential single-target
    deallocations. Expression-form dealloc remains single-target so its value
    semantics stay unchanged.
    """
    if node.name in {"dealloc", "free"} and len(node.args) != 1:
        if not node.args:
            raise StmtGenError(f"{node.name}() expects at least 1 argument")
        for arg in node.args:
            self.codegen.generate_expr(Call(node.name, [arg], unsafe=node.unsafe))
        return None
    return self.codegen.generate_expr(node)


def _evaluate_comptime(self, expr):
    """Evaluate an expression at compile time if possible.
    Returns the computed value, or None if cannot be evaluated.
    """
    from parser import ast as A

    if isinstance(expr, A.Number):
        if expr.is_float:
            return float(expr.value)
        return int(expr.value)
    if isinstance(expr, A.Bool):
        return expr.value
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.Call):
        if expr.args:
            return None
        if expr.name == "target_os":
            return os_from_triple(self.codegen.module.triple)
        if expr.name == "target_backend":
            return "llvm"
    if isinstance(expr, A.BinaryOp):
        left = self._evaluate_comptime(expr.left)
        right = self._evaluate_comptime(expr.right)
        if left is None or right is None:
            return None
        op = expr.op
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                raise StmtGenError("Division by zero in comptime expression")
            return left // right if isinstance(left, int) else left / right
        if op == "%":
            return left % right
        if op == "**":
            return left**right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
        if op in ("and", "&&"):
            return left and right
        if op in ("or", "||"):
            return left or right
    if isinstance(expr, A.UnaryOp):
        operand = self._evaluate_comptime(expr.operand)
        if operand is None:
            return None
        if expr.op == "-":
            return -operand
        if expr.op in ("not", "!"):
            return not operand
    if isinstance(expr, A.TernaryOp):
        cond = self._evaluate_comptime(expr.cond)
        if cond is None:
            return None
        if cond:
            return self._evaluate_comptime(expr.true_expr)
        return self._evaluate_comptime(expr.false_expr)
    # Can't evaluate at compile time
    return None


def visit_VarDecl(self, node: VarDecl):
    llvm_type = self.codegen.get_llvm_type(node.type_name)
    if _try_emit_stack_class_vardecl(self, node, llvm_type):
        return
    # OPTIMIZATION: Create alloca in entry block for better mem2reg/SROA
    var_ptr = self.codegen.alloca_in_entry_block(llvm_type, node.var_name)
    fixed_array = _fixed_array_decl_spec(self, node.type_name)
    if fixed_array is not None:
        elem_type_name, fixed_len = fixed_array
        elem_type = self.codegen.get_llvm_type(elem_type_name)
        if isinstance(node.init_value, ArrayLit) or node.init_value is None:
            for idx in range(fixed_len):
                if isinstance(node.init_value, ArrayLit) and idx < len(
                    node.init_value.elements
                ):
                    elem_value = self.codegen.generate_expr(
                        node.init_value.elements[idx]
                    )
                    if elem_value.type != elem_type:
                        elem_value = self.codegen.cast_value(elem_value, elem_type)
                else:
                    elem_value = self._default_value(elem_type)
                elem_ptr = self.builder.gep(
                    var_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                    name=f"{node.var_name}_{idx}_ptr",
                )
                self.builder.store(elem_value, elem_ptr)
        else:
            init_value = self.codegen.generate_expr(node.init_value)
            if init_value.type != llvm_type:
                init_value = self.codegen.cast_value(init_value, llvm_type)
            self.builder.store(init_value, var_ptr)
        self.codegen.locals[node.var_name] = var_ptr
        self.codegen.local_decl_types[node.var_name] = parsed_type_to_str(
            node.type_name
        )
        self.codegen.var_signedness[node.var_name] = not elem_type_name.startswith("u")
        self.codegen.set_signedness(var_ptr, self.codegen.var_signedness[node.var_name])
        self.codegen.array_metadata[node.var_name] = (fixed_len, elem_type)
        return
    init_value = None
    array_meta: tuple[int, ir.Type] | None = None
    strlen_value = None
    if node.init_value is not None:
        scalar = try_emit_length_only_str_assignment(
            self.codegen, node.var_name, node.init_value
        )
        if scalar is not None:
            init_value, strlen_value = scalar
        else:
            evaluated = self.codegen.generate_expr(node.init_value)
            if isinstance(evaluated, tuple) and len(evaluated) == 3:
                data_ptr, array_len, elem_type = evaluated
                array_meta = (array_len, cast(ir.Type, elem_type))
                init_value = data_ptr
            else:
                init_value = evaluated
    if init_value is None:
        init_value = self._default_value(llvm_type)
    elif init_value.type != llvm_type:
        init_value = self.codegen.cast_value(init_value, llvm_type)
    self.builder.store(init_value, var_ptr)
    self.codegen.locals[node.var_name] = var_ptr
    canon_type = parsed_type_to_str(node.type_name)
    self.codegen.local_decl_types[node.var_name] = canon_type
    self.codegen.var_signedness[node.var_name] = not canon_type.startswith("u")
    self.codegen.set_signedness(var_ptr, self.codegen.var_signedness[node.var_name])
    if array_meta:
        self.codegen.array_metadata[node.var_name] = array_meta
    elif node.var_name in self.codegen.array_metadata:
        del self.codegen.array_metadata[node.var_name]
    invalidate_strlen_facts(self.codegen, node.var_name)
    if strlen_value is not None:
        register_strlen_fact(self.codegen, node.var_name, strlen_value)


def _fixed_array_decl_spec(self, type_spec) -> tuple[str, int] | None:
    resolved = self.codegen._resolve_type_alias_spec(type_spec)
    if isinstance(resolved, tuple) and len(resolved) >= 3:
        tag, elem_type, size = resolved[:3]
        if tag == "fixed_array":
            return parsed_type_to_str(elem_type), int(size)
    text = parsed_type_to_str(resolved).strip()
    if not (text.startswith("[") and text.endswith("]") and ";" in text):
        return None
    inner = text[1:-1]
    elem_part, size_part = inner.rsplit(";", 1)
    size_text = size_part.strip()
    if not size_text.isdigit():
        return None
    size = int(size_text)
    if size <= 0:
        return None
    return elem_part.strip(), size


def visit_RangeVarDecl(self, node: RangeVarDecl) -> None:
    """Generate Ada-style range variable with runtime bounds checking."""
    llvm_type = ir.IntType(64)  # Range vars are always i64
    var_ptr = self.codegen.alloca_in_entry_block(llvm_type, node.var_name)
    # Get bounds
    low_val = self.codegen.generate_expr(node.range_type.low)
    high_val = self.codegen.generate_expr(node.range_type.high)
    # Store range constraint for future assignments
    self.codegen.range_vars[node.var_name] = (
        low_val,
        high_val,
        node.range_type.exclusive,
    )
    # Get initial value (defaults to low bound)
    if node.init_value:
        init_val = self.codegen.generate_expr(node.init_value)
    else:
        init_val = low_val
    # Cast to i64 if needed
    if init_val.type != llvm_type:
        init_val = self.codegen.cast_value(init_val, llvm_type)
    # Store initial value
    self.builder.store(init_val, var_ptr)
    self.codegen.locals[node.var_name] = var_ptr
    self.codegen.var_signedness[node.var_name] = True  # Signed
    # Generate range check
    self._emit_range_check(
        node.var_name, var_ptr, low_val, high_val, node.range_type.exclusive
    )


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


def visit_TypeAlias(self, node: TypeAlias) -> None:
    """Type aliases are compile-time only, no runtime code needed."""
    # Store for potential future use
    if isinstance(node.target_type, RangeType):
        self.codegen.type_aliases[node.name] = node.target_type


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
    # Check if RHS is a NewExpr to track for RAII cleanup
    class_name: str | None = None
    if isinstance(node.value, NewExpr):
        class_name = node.value.type_name
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
    if node.var_name in self.codegen.locals:
        slot = self.codegen.locals[node.var_name]
        if isinstance(slot.type, ir.PointerType):
            target_type = slot.type.pointee
            if value.type != target_type:
                value = self.codegen.cast_value(value, target_type)
            self.builder.store(value, slot)
        else:
            target_type = slot.type
            if value.type != target_type:
                value = self.codegen.cast_value(value, target_type)
            self.codegen.locals[node.var_name] = value
        inferred_decl_type = _infer_decl_type_from_expr(node.value)
        if inferred_decl_type is not None:
            self.codegen.local_decl_types[node.var_name] = inferred_decl_type
    elif node.var_name in self.codegen.globals:
        # Assign to existing global variable
        global_var = self.codegen.globals[node.var_name]
        # Block reassignment of const globals
        if global_var.global_constant:
            raise StmtGenError(f"Cannot assign to constant '{node.var_name}'")
        target_type = global_var.type.pointee
        if value.type != target_type:
            value = self.codegen.cast_value(value, target_type)
        self.builder.store(value, global_var)
    else:
        # Create alloca in entry block for better mem2reg/SROA optimization
        var_ptr = self.codegen.alloca_in_entry_block(value.type, node.var_name)
        self.builder.store(value, var_ptr)
        self.codegen.locals[node.var_name] = var_ptr
        inferred_decl_type = _infer_decl_type_from_expr(node.value)
        if inferred_decl_type is not None:
            self.codegen.local_decl_types[node.var_name] = inferred_decl_type
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
        self._emit_range_check(node.var_name, slot, low_val, high_val, exclusive)
    if array_meta:
        self.codegen.array_metadata[node.var_name] = array_meta
    elif node.var_name in self.codegen.array_metadata:
        del self.codegen.array_metadata[node.var_name]
    invalidate_strlen_facts(self.codegen, node.var_name)
    if strlen_value is not None:
        register_strlen_fact(self.codegen, node.var_name, strlen_value)
    else:
        maybe_register_strlen_fact(self.codegen, node, value)


def visit_TupleAssign(self, node: TupleAssign) -> None:
    """Handle tuple unpacking: a, b = b, a or a, b = get_pair()
    Strategy: Evaluate all RHS expressions first into temps,
    then assign to all LHS variables. This correctly handles swaps.
    """
    # First, evaluate all right-hand side values into temporaries
    temp_values: list[ir.Value] = []
    for value_expr in node.values:
        val = self.codegen.generate_expr(value_expr)
        # Handle array literals that return tuples
        if isinstance(val, tuple) and len(val) == 3:
            val = val[0]  # Get the array pointer
        temp_values.append(val)
    # If single RHS but multiple LHS, we need to handle function returns
    # For now, require matching counts
    if len(temp_values) != len(node.var_names):
        raise StmtGenError(
            f"Tuple unpacking mismatch: {len(node.var_names)} targets, "
            f"{len(temp_values)} values"
        )
    # Now assign each temp to its corresponding variable
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


def visit_BlockCall(self, node: BlockCall) -> None:
    """Handle block call: items.each |x| then ... end
    For built-in iterators like .each, .map, .times, we inline the loop.
    """
    method_name = node.method_name.lower()
    # Get the object being iterated
    obj = self.codegen.generate_expr(node.object_expr)
    if method_name == "each":
        self._block_each(obj, node.block)
    elif method_name == "times":
        self._block_times(obj, node.block)
    else:
        raise StmtGenError(f"Unknown block method: {node.method_name}")

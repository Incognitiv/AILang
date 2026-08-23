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
    Number,
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
from target_info import os_from_triple
from transpiler.array_literal_hints import update_array_literal_hints
from transpiler.codegen_int_ranges import (
    call_preserves_local_int_proofs,
    clear_codegen_int_proofs,
    expr_int_range,
    expr_invalidates_local_int_proofs,
    remember_assign_range,
)
from transpiler.llvm_fixed_dicts import emit_fixed_dict_init
from transpiler.llvm_fixed_int_casts import cast_to_declared_int, fixed_int_info_for_spec
from transpiler.llvm_bigint import (
    bigint_from_decimal_literal,
    bigint_to_fixed,
    clone_if_borrowed,
    expression_is_borrowed_bigint,
    fixed_to_bigint,
    free_if_owned_temp,
    is_bigint_value,
    is_unbounded_spec,
)
from transpiler.llvm_int_narrowing import (
    cast_for_narrowed_storage,
    effective_local_type_name,
    maybe_narrow_local_type,
)

from . import llvm_stack_arrays
from .emit_statements_common import StmtGenError
from .stack_class_lowering import _try_emit_stack_class_vardecl


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


def visit_Return(self, node: Return):
    ret_type = self.func.function_type.return_type
    function_node = getattr(self.codegen, "_function_nodes", {}).get(
        getattr(self.codegen, "_current_function_name", "")
    )
    return_spec = getattr(function_node, "return_type", None)
    return_is_bigint = return_spec is not None and is_unbounded_spec(
        self.codegen, return_spec
    )
    value = None
    skip_cleanup_names: set[str] = set()

    if node.value is not None and not isinstance(ret_type, ir.VoidType):
        if return_is_bigint:
            value = _emit_owned_bigint_for_expr(self, node.value)
        else:
            value = self.codegen.generate_expr(node.value)
            if isinstance(value, tuple) and len(value) == 3:
                value = value[0]
            if is_bigint_value(self.codegen, value) and isinstance(ret_type, ir.IntType):
                return_info = fixed_int_info_for_spec(self.codegen, return_spec)
                target_unsigned = bool(return_info.unsigned) if return_info is not None else False
                converted = bigint_to_fixed(
                    self.codegen, self.builder, value, ret_type, unsigned=target_unsigned
                )
                free_if_owned_temp(self.codegen, self.builder, node.value, value)
                value = converted

        if isinstance(node.value, Variable) and not return_is_bigint:
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
    self.codegen.cleanup_all_scopes(skip_names=skip_cleanup_names)
    if self.codegen._synchronized_mutex_ptr is not None:
        unlock_func = self.codegen.get_mutex_func("unlock")
        if unlock_func is not None:
            self.builder.call(unlock_func, [self.codegen._synchronized_mutex_ptr])
    if self.func.name == "main" and self.codegen._string_arena is not None:
        self.codegen._arena_gen.arena_destroy(self.codegen._string_arena)

    if isinstance(ret_type, ir.VoidType):
        if node.value is not None:
            raise TypeError(
                "Are you trying to return something in a void function? "
                "Void functions cannot return values. "
                "Either remove the return value or change the function to a non-void type."
            )
        if self.codegen._function_needs_recursion_guard():
            self.codegen._emit_recursion_decrement()
        self.codegen.emit_profile_exit(self.func.name)
        self.builder.ret_void()
        return

    if node.value is None:
        raise StmtGenError(
            f"bare return is not valid in non-void function {self.func.name!r}"
        )
    if value is None:
        raise StmtGenError("return value expression produced no value")

    if not return_is_bigint and not is_bigint_value(self.codegen, value):
        return_info = (
            fixed_int_info_for_spec(self.codegen, return_spec)
            if return_spec is not None
            else None
        )
        if (
            return_info is not None
            and isinstance(value.type, ir.IntType)
            and isinstance(ret_type, ir.IntType)
        ):
            value = self.codegen.cast_value(
                value, ret_type, target_unsigned=return_info.unsigned
            )
        elif value.type != ret_type:
            value = self.codegen.cast_value(value, ret_type)

    if value.type != ret_type:
        raise StmtGenError(
            f"return type mismatch in {self.func.name!r}: expected {ret_type}, got {value.type}"
        )
    if self.codegen._function_needs_recursion_guard():
        self.codegen._emit_recursion_decrement()
    self.codegen.emit_profile_exit(self.func.name)
    self.builder.ret(value)


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
        clear_codegen_int_proofs(self.codegen)
        return None
    result = self.codegen.generate_expr(node)
    if not call_preserves_local_int_proofs(node):
        clear_codegen_int_proofs(self.codegen)
    return result


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
    if is_unbounded_spec(self.codegen, node.type_name):
        var_ptr = self.codegen.alloca_in_entry_block(llvm_type, node.var_name)
        if node.init_value is None:
            init_value = self.builder.call(
                self.codegen._get_bigint_from_int(),
                [ir.Constant(ir.IntType(64), 0)],
                name=f"{node.var_name}_bigint_zero",
            )
        else:
            init_value = _emit_owned_bigint_for_expr(self, node.init_value)
        self.builder.store(init_value, var_ptr)
        self.codegen.locals[node.var_name] = var_ptr
        self.codegen.local_decl_types[node.var_name] = "unbounded"
        self.codegen.var_signedness[node.var_name] = True
        self.codegen.register_bigint_for_cleanup(node.var_name, var_ptr)
        _forget_local_constant(self.codegen, node.var_name)
        invalidate_strlen_facts(self.codegen, node.var_name)
        return
    init_range = (
        expr_int_range(self.codegen, node.init_value)
        if node.init_value is not None
        else None
    )
    llvm_type = maybe_narrow_local_type(
        self.codegen, node.var_name, llvm_type, hint_range=init_range
    )
    if _try_emit_stack_class_vardecl(self, node, llvm_type):
        return
    if emit_fixed_dict_init(self.codegen, node.var_name, node.init_value):
        invalidate_strlen_facts(self.codegen, node.var_name)
        return
    if llvm_stack_arrays.emit(self, node, llvm_type):
        return
    if _try_emit_fixed_array_slice_alias(self, node, llvm_type):
        return
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
                    if (
                        elem_value.type != elem_type
                        or (
                            isinstance(elem_value.type, ir.IntType)
                            and fixed_int_info_for_spec(self.codegen, elem_type_name)
                            is not None
                        )
                    ):
                        elem_value = cast_to_declared_int(
                            self.codegen, elem_value, elem_type, elem_type_name
                        )
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
        _forget_local_constant(self.codegen, node.var_name)
        if node.init_value is not None:
            update_array_literal_hints(self.codegen, node.var_name, node.init_value)
        if node.init_value is not None:
            remember_assign_range(self.codegen, node.var_name, node.init_value)
        return
    if _try_emit_record_new_into_slot(
        self, var_ptr, llvm_type, node.var_name, node.init_value
    ):
        canon_type = parsed_type_to_str(node.type_name)
        self.codegen.locals[node.var_name] = var_ptr
        self.codegen.local_decl_types[node.var_name] = canon_type
        self.codegen.var_signedness[node.var_name] = not canon_type.startswith("u")
        self.codegen.set_signedness(var_ptr, self.codegen.var_signedness[node.var_name])
        _forget_local_constant(self.codegen, node.var_name)
        invalidate_strlen_facts(self.codegen, node.var_name)
        remember_assign_range(self.codegen, node.var_name, node.init_value)
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
                strlen_value = consume_value_strlen_fact(self.codegen, init_value)
    if init_value is None:
        init_value = self._default_value(llvm_type)
    elif init_value.type != llvm_type or (
        isinstance(init_value.type, ir.IntType)
        and fixed_int_info_for_spec(self.codegen, node.type_name) is not None
    ):
        init_value = cast_to_declared_int(
            self.codegen, init_value, llvm_type, node.type_name
        )
    self.builder.store(init_value, var_ptr)
    self.codegen.locals[node.var_name] = var_ptr
    _remember_local_constant(self.codegen, node.var_name, init_value)
    declared_type = parsed_type_to_str(node.type_name)
    canon_type = effective_local_type_name(llvm_type, declared_type) or declared_type
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
    elif node.init_value is not None:
        known_strlen = try_emit_known_string_length(self.codegen, node.init_value)
        if known_strlen is not None:
            register_strlen_fact(self.codegen, node.var_name, known_strlen)
    if node.init_value is not None:
        remember_assign_range(self.codegen, node.var_name, node.init_value)


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


def _is_slice_or_view_type(type_spec) -> bool:
    text = parsed_type_to_str(type_spec).strip().lower()
    return (text.startswith("slice[") and text.endswith("]")) or (
        text.startswith("view[") and text.endswith("]")
    )


def _try_emit_fixed_array_slice_alias(self, node: VarDecl, llvm_type: ir.Type) -> bool:
    if not _is_slice_or_view_type(node.type_name):
        return False
    if not isinstance(node.init_value, Variable):
        return False
    if not isinstance(llvm_type, ir.PointerType):
        return False
    source_name = node.init_value.name
    source_meta = self.codegen.array_metadata.get(source_name)
    if source_meta is None:
        return False
    source_storage = self.codegen.locals.get(source_name)
    if source_storage is None or not isinstance(source_storage.type, ir.PointerType):
        return False

    elem_count, elem_type = source_meta
    pointee = source_storage.type.pointee
    if isinstance(pointee, ir.ArrayType):
        data_ptr = self.builder.gep(
            source_storage,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
            name=f"{source_name}_slice_ptr",
        )
    elif isinstance(pointee, ir.PointerType):
        data_ptr = self.builder.load(source_storage, name=f"{source_name}_slice_ptr")
    else:
        return False

    if data_ptr.type != llvm_type:
        data_ptr = self.builder.bitcast(data_ptr, llvm_type, name="slice_alias_cast")

    var_ptr = self.codegen.alloca_in_entry_block(llvm_type, node.var_name)
    self.builder.store(data_ptr, var_ptr)
    self.codegen.locals[node.var_name] = var_ptr
    _forget_local_constant(self.codegen, node.var_name)
    canon_type = parsed_type_to_str(node.type_name)
    self.codegen.local_decl_types[node.var_name] = canon_type
    self.codegen.var_signedness[node.var_name] = not canon_type.startswith("u")
    self.codegen.set_signedness(var_ptr, self.codegen.var_signedness[node.var_name])
    self.codegen.array_metadata[node.var_name] = (elem_count, elem_type)
    update_array_literal_hints(self.codegen, node.var_name, node.init_value)
    remember_assign_range(self.codegen, node.var_name, node.init_value)
    return True


def visit_RangeVarDecl(self, node: RangeVarDecl) -> None:
    """Generate Ada-style range variable with runtime bounds checking."""
    # Keep the value at language-level width until it passes the range check.
    # Narrowing before validation could turn 4_294_967_297 into 1 and accept it
    # for a 0..100 range.
    llvm_type = ir.IntType(64)
    var_ptr = self.codegen.alloca_in_entry_block(llvm_type, node.var_name)
    # Get bounds
    low_val = self.codegen.generate_expr(node.range_type.low)
    high_val = self.codegen.generate_expr(node.range_type.high)
    if low_val.type != llvm_type:
        low_val = cast_for_narrowed_storage(self.codegen, low_val, llvm_type)
    if high_val.type != llvm_type:
        high_val = cast_for_narrowed_storage(self.codegen, high_val, llvm_type)
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
    # Cast to narrowed storage if needed
    if init_val.type != llvm_type:
        init_val = cast_for_narrowed_storage(self.codegen, init_val, llvm_type)
    # Store initial value
    self.builder.store(init_val, var_ptr)
    self.codegen.locals[node.var_name] = var_ptr
    self.codegen.local_decl_types[node.var_name] = (
        "i32" if isinstance(llvm_type, ir.IntType) and llvm_type.width == 32 else "i64"
    )
    self.codegen.var_signedness[node.var_name] = True  # Signed
    if not _can_elide_range_check_for_expr(
        self, node.init_value, node.range_type, default_to_low=True
    ):
        self._emit_range_check(
            node.var_name, var_ptr, low_val, high_val, node.range_type.exclusive
        )
    remember_assign_range(
        self.codegen, node.var_name, node.init_value or node.range_type.low
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


def visit_TypeAlias(self, node: TypeAlias) -> None:
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
            raise StmtGenError(f"unbounded assignment target {node.var_name!r} has no owning slot")
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
        declared_spec = getattr(self.codegen, "global_decl_types", {}).get(node.var_name)
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


def visit_BlockCall(self, node: BlockCall) -> None:
    """Handle built-in block calls by inlining their loop body."""
    method_name = node.method_name.lower()
    obj = self.codegen.generate_expr(node.object_expr)
    if method_name == "each":
        self._block_each(obj, node.block)
    elif method_name == "times":
        self._block_times(obj, node.block)
    else:
        raise StmtGenError(f"Unknown block method: {node.method_name}")

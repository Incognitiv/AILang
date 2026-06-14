"""Exception-control service for the LLVM backend.

This service contains try/throw control-flow methods that were
originally defined on ``CodeGen``.
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir
from transpiler.strlen_cache import (
    enter_strlen_cache_control,
    leave_strlen_cache_control,
)


class ExceptionEmitter:
    """Try/throw and personality plumbing for LLVM exception-like control flow."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        # Keep service transparent while ``CodeGen`` owns shared state.
        return getattr(self._cg, name)

    def _emit_safety_trap(
        self,
        error_msg_str: str,
        builder: Any = None,
    ) -> None:
        """Emit catchable safety error or fatal exit.

        If inside a try block, stores the error message and branches to
        the exception handler (making safety errors catchable as SafetyError).
        If not inside a try block, calls exit(1) (fatal, same as before).

        Must be called at the end of an error basic block, after any
        diagnostic printf calls. This method terminates the current block.
        """
        b = builder if builder is not None else self._cg.current_builder

        if self._cg._exc_handler_stack:
            # Inside a try block: throw SafetyError instead of dying.
            self._ensure_exc_globals()
            i8_ptr = ir.IntType(8).as_pointer()
            i32 = ir.IntType(32)

            msg_const = self._cg.create_string_constant(error_msg_str)
            if msg_const.type != i8_ptr:
                msg_const = b.bitcast(msg_const, i8_ptr, name="safety_msg")
            b.store(msg_const, self._cg._exc_msg_global)

            # SafetyError type code (hash of "SafetyError" = 1355569627)
            b.store(ir.Constant(i32, 1355569627), self._cg._exc_type_global)

            # Branch to the nearest exception handler.
            b.branch(self._cg._exc_handler_stack[-1])
        else:
            # Not in try block: fatal exit (preserves current behavior).
            exit_func = self._cg._get_or_declare_exit()
            b.call(exit_func, [ir.Constant(ir.IntType(32), 1)])
            b.unreachable()

    def _ensure_exc_globals(self) -> None:
        """Lazily declare exception handling global state."""
        if self._cg._exc_msg_global is not None:
            return

        i8_ptr = ir.IntType(8).as_pointer()
        i32 = ir.IntType(32)

        # Exception message pointer.
        self._cg._exc_msg_global = ir.GlobalVariable(
            self._cg.module, i8_ptr, "__ailang_exc_msg"
        )
        self._cg._exc_msg_global.initializer = ir.Constant(i8_ptr, None)
        self._cg._exc_msg_global.linkage = "internal"

        # Exception type code (0 = none, hash-based for named types).
        self._cg._exc_type_global = ir.GlobalVariable(
            self._cg.module, i32, "__ailang_exc_type"
        )
        self._cg._exc_type_global.initializer = ir.Constant(i32, 0)
        self._cg._exc_type_global.linkage = "internal"

    def ensure_personality_function(self) -> None:
        """Ensure personality function exists for exception handling."""
        cg = self._cg
        if cg.personality_func is None:
            # Create C++ personality function for exception handling.
            i32 = ir.IntType(32)
            personality_type = ir.FunctionType(i32, [], var_arg=True)
            cg.personality_func = ir.Function(
                cg.module, personality_type, name="__gxx_personality_v0"
            )

        # Set personality on current function.
        if cg.func and not cg.current_function.attributes.personality:
            cg.current_function.attributes.personality = cg.personality_func

    def call_or_invoke(self, func: Any, args: Any, name: str = "") -> Any:
        """
        Call a function using ``invoke`` if in try block, otherwise ``call``.

        JIT integration for ``invoke`` is currently disabled; this method
        currently always uses a normal call.
        """
        # Exception invoke/landingpad is disabled until C++ exception runtime
        # linking is resolved for JIT mode.
        # if cg.in_try_block and cg.current_landingpad:
        #     normal_block = cg.current_function.append_basic_block(f"{name}_normal")
        #     result = cg.current_builder.invoke(
        #         func, args, normal_block, cg.current_landingpad, name=name
        #     )
        #     cg.current_builder.position_at_end(normal_block)
        #     return result
        # else:
        #     return cg.current_builder.call(func, args, name=name)

        # Always use call for now.
        cg = self._cg
        return cg.current_builder.call(func, args, name=name)

    def generate_try_except(self, node: Any) -> None:
        """Generate exception handling using direct branching."""
        cg = self._cg
        cg._ensure_exc_globals()

        i8_ptr = ir.IntType(8).as_pointer()
        i32 = ir.IntType(32)

        try_block = cg.current_function.append_basic_block("try_body")
        dispatch_block = cg.current_function.append_basic_block("exc_dispatch")

        catch_handler_blocks = []
        for _i, (error_type, _var, _body) in enumerate(node.catch_blocks):
            catch_handler_blocks.append(
                cg.current_function.append_basic_block(f"catch_{error_type}")
            )

        except_handler_block = None
        if node.except_block:
            except_handler_block = cg.current_function.append_basic_block("except")

        finally_block = None
        if node.finally_block:
            finally_block = cg.current_function.append_basic_block("finally")

        merge_block = cg.current_function.append_basic_block("try_merge")

        # Branch to try body.
        cg.current_builder.branch(try_block)
        cg.current_builder.position_at_end(try_block)

        # Push handler so throw can find it.
        cg._exc_handler_stack.append(dispatch_block)
        old_in_try = cg.in_try_block
        cg.in_try_block = True

        if node.try_expr:
            cg.generate_expr(node.try_expr)
        enter_strlen_cache_control(cg)
        for stmt in node.try_body:
            cg.generate_stmt(stmt)
        leave_strlen_cache_control(cg)

        cg.in_try_block = old_in_try
        cg._exc_handler_stack.pop()

        # After successful try, branch to finally or merge.
        if not cg.current_builder.block.is_terminated:
            if finally_block:
                cg.current_builder.branch(finally_block)
            else:
                cg.current_builder.branch(merge_block)

        # --- Exception dispatch.
        cg.current_builder.position_at_end(dispatch_block)
        exc_type_val = cg.current_builder.load(cg._exc_type_global, name="exc_type")

        # Dispatch to typed catch blocks.
        if catch_handler_blocks:
            for i, (error_type, _var, _body) in enumerate(node.catch_blocks):
                type_code = self._error_type_hash(error_type)
                matches = cg.current_builder.icmp_unsigned(
                    "==",
                    exc_type_val,
                    ir.Constant(i32, type_code),
                    name=f"match_{error_type}",
                )
                next_check = cg.current_function.append_basic_block(f"check_{i + 1}")
                cg.current_builder.cbranch(matches, catch_handler_blocks[i], next_check)
                cg.current_builder.position_at_end(next_check)

        # Fall through to except or finally or merge.
        if not cg.current_builder.block.is_terminated:
            if except_handler_block:
                cg.current_builder.branch(except_handler_block)
            elif finally_block:
                cg.current_builder.branch(finally_block)
            else:
                cg.current_builder.branch(merge_block)

        # --- Catch handler blocks.
        for i, (_error_type, var, body) in enumerate(node.catch_blocks):
            cg.current_builder.position_at_end(catch_handler_blocks[i])

            if var:
                exc_msg = cg.current_builder.load(cg._exc_msg_global, name="exc_msg")
                error_ptr = cg.current_builder.alloca(i8_ptr, name=var)
                cg.current_builder.store(exc_msg, error_ptr)
                cg.locals[var] = error_ptr

            enter_strlen_cache_control(cg)
            for stmt in body:
                cg.generate_stmt(stmt)
            leave_strlen_cache_control(cg)

            if not cg.current_builder.block.is_terminated:
                if finally_block:
                    cg.current_builder.branch(finally_block)
                else:
                    cg.current_builder.branch(merge_block)

        # --- Except handler (catch-all).
        if except_handler_block:
            cg.current_builder.position_at_end(except_handler_block)
            error_var, except_body = node.except_block

            if error_var:
                exc_msg = cg.current_builder.load(
                    cg._exc_msg_global, name="exc_msg_all"
                )
                error_ptr = cg.current_builder.alloca(i8_ptr, name=error_var)
                cg.current_builder.store(exc_msg, error_ptr)
                cg.locals[error_var] = error_ptr

            enter_strlen_cache_control(cg)
            for stmt in except_body:
                cg.generate_stmt(stmt)
            leave_strlen_cache_control(cg)

            if not cg.current_builder.block.is_terminated:
                if finally_block:
                    cg.current_builder.branch(finally_block)
                else:
                    cg.current_builder.branch(merge_block)

        # --- Finally block (always executes).
        if finally_block:
            cg.current_builder.position_at_end(finally_block)
            for stmt in node.finally_block:
                cg.generate_stmt(stmt)
            if not cg.current_builder.block.is_terminated:
                cg.current_builder.branch(merge_block)

        # --- Merge.
        cg.current_builder.position_at_end(merge_block)

    def generate_throw(self, node: Any) -> None:
        """Generate throw by branching to nearest active handler."""
        cg = self._cg
        cg._ensure_exc_globals()

        i8_ptr = ir.IntType(8).as_pointer()
        i32 = ir.IntType(32)

        # Evaluate message expression.
        if node.message is not None:
            msg_val = cg.generate_expr(node.message)
            if msg_val.type != i8_ptr:
                msg_val = cg.current_builder.bitcast(msg_val, i8_ptr, name="throw_msg")
        else:
            msg_val = cg.create_string_constant("Unknown error")

        # Store message and type code.
        cg.current_builder.store(msg_val, cg._exc_msg_global)
        type_code = 0
        if node.error_type:
            type_code = self._error_type_hash(node.error_type)
        cg.current_builder.store(ir.Constant(i32, type_code), cg._exc_type_global)

        if cg._exc_handler_stack:
            handler = cg._exc_handler_stack[-1]
            cg.current_builder.branch(handler)
        else:
            # No enclosing try: print and exit.
            fmt = cg.create_string_constant("Unhandled exception: %s\\n")
            printf_fn = cg.get_printf()
            cg.current_builder.call(printf_fn, [fmt, msg_val])
            exit_fn = cg._get_or_declare_exit()
            cg.current_builder.call(exit_fn, [ir.Constant(i32, 1)])
            cg.current_builder.unreachable()

    @staticmethod
    def _error_type_hash(error_type: str) -> int:
        """Deterministic hash for error type names (catch dispatch)."""
        result = 5381
        for char in error_type:
            result = ((result * 33) + ord(char)) & 0xFFFFFFFF
        return result if result != 0 else 1

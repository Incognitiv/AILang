"""SQL builtins for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression split.
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir
from runtime.modes import CompilationContext
from transpiler.expr_builtin_sql_text import emit_sql_bind_text_i64_direct
from transpiler.expr_common import (
    ARG_FIFTH,
    ARG_FIRST,
    ARG_FOURTH,
    ARG_SECOND,
    ARG_THIRD,
    ExprGenError,
)

SQLITE_OPEN_READONLY = 0x00000001
SQLITE_OPEN_READWRITE = 0x00000002
SQLITE_OPEN_CREATE = 0x00000004
SQLITE_OPEN_URI = 0x00000040


class ExprBuiltinSqlEmitter:
    """SQL builtin expression service for ``ExprGenerator``."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _sql_last_open_status_global(self):
        name = "ailang_sql_last_open_status"
        module = self.codegen.module
        existing = module.globals.get(name)
        if existing is not None:
            return existing
        status = ir.GlobalVariable(module, ir.IntType(32), name=name)
        status.linkage = "internal"
        status.global_constant = False
        status.initializer = ir.Constant(ir.IntType(32), 0)
        return status

    def _builtin_sql_open_with_flags(self, args, flags: int, name: str):
        # Requires SQLite library
        CompilationContext.require_feature("sqlite", f"{name}()")

        if len(args) != 1:
            raise ExprGenError(f"{name}() expects filename")
        # If sqlite not available, return fake handle 1
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 1)

        filename = self.generate_expr(args[ARG_FIRST])
        char_ptr = ir.IntType(8).as_pointer()
        handle_ptr = self.builder.alloca(char_ptr, name="db_ptr_ptr")
        self.builder.store(ir.Constant(char_ptr, None), handle_ptr)
        open_flags = ir.Constant(ir.IntType(32), flags)
        null_vfs = ir.Constant(char_ptr, None)
        rc = self.builder.call(
            self.codegen.get_sqlite3_open_v2(),
            [filename, handle_ptr, open_flags, null_vfs],
            name="sqlite_open_rc",
        )
        self.builder.store(rc, self._sql_last_open_status_global())
        ok = self.builder.icmp_unsigned(
            "==", rc, ir.Constant(ir.IntType(32), 0), name="sqlite_open_ok"
        )
        db_ptr = self.builder.load(handle_ptr, name="db_ptr")
        # If open failed, return 0 handle
        zero64 = ir.Constant(ir.IntType(64), 0)
        handle_val = self.builder.ptrtoint(db_ptr, ir.IntType(64), name="db_handle")
        return self.builder.select(ok, handle_val, zero64, name="db_handle_sel")

    def _builtin_sql_open(self, args):
        # READWRITE | CREATE | URI. URI support lets existing sql_open()
        # callers request SQLite options such as mutex=no through the path
        # string without adding a dedicated language-level SQLite option API.
        return self._builtin_sql_open_with_flags(
            args,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_URI,
            "sql_open",
        )

    def _builtin_sql_open_readonly(self, args):
        # Read-only open for PID1/config readers: no implicit creation and no
        # write permission request, while preserving URI path support.
        return self._builtin_sql_open_with_flags(
            args, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, "sql_open_readonly"
        )

    def _builtin_sql_last_open_status(self, args):
        CompilationContext.require_feature("sqlite", "sql_last_open_status()")
        if args:
            raise ExprGenError("sql_last_open_status() expects no arguments")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 1)
        status = self.builder.load(
            self._sql_last_open_status_global(), name="sqlite_last_open_status"
        )
        return self.builder.zext(status, ir.IntType(64), name="sqlite_status_i64")

    def _builtin_sql_exec(self, args):
        CompilationContext.require_feature("sqlite", "sql_exec()")

        if len(args) != 2:
            raise ExprGenError("sql_exec() expects handle and SQL string")
        db_handle = self.generate_expr(args[ARG_FIRST])
        sql = self.generate_expr(args[ARG_SECOND])
        char_ptr = ir.IntType(8).as_pointer()
        db_ptr = self.builder.inttoptr(db_handle, char_ptr, name="db_ptr")
        null_ptr = ir.Constant(char_ptr, None)
        null_ptr_ptr = ir.Constant(char_ptr.as_pointer(), None)
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 0)

        is_null = self.builder.icmp_unsigned(
            "==", db_handle, ir.Constant(ir.IntType(64), 0), name="db_is_null"
        )
        null_block = self.function.append_basic_block("sql_exec_null")
        call_block = self.function.append_basic_block("sql_exec_call")
        merge_block = self.function.append_basic_block("sql_exec_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        # Null-handle path
        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        # Call path
        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_exec(),
            [db_ptr, sql, null_ptr, null_ptr, null_ptr_ptr],
            name="exec_result",
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="exec_result_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        # Merge
        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="exec_result_phi")
        phi.add_incoming(
            ir.Constant(ir.IntType(64), 1), null_end
        )  # failure if null handle
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_close(self, args):
        CompilationContext.require_feature("sqlite", "sql_close()")

        if len(args) != 1:
            raise ExprGenError("sql_close() expects handle")
        db_handle = self.generate_expr(args[ARG_FIRST])
        char_ptr = ir.IntType(8).as_pointer()
        db_ptr = self.builder.inttoptr(db_handle, char_ptr, name="db_ptr")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 0)

        is_null = self.builder.icmp_unsigned(
            "==", db_handle, ir.Constant(ir.IntType(64), 0), name="db_is_null_close"
        )
        null_block = self.function.append_basic_block("sql_close_null")
        call_block = self.function.append_basic_block("sql_close_call")
        merge_block = self.function.append_basic_block("sql_close_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        # Null-handle path
        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        # Call path
        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_close(), [db_ptr], name="close_result"
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="close_result_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        # Merge
        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="close_result_phi")
        phi.add_incoming(
            ir.Constant(ir.IntType(64), 0), null_end
        )  # success if null handle
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_prepare(self, args):
        """sql_prepare(db_handle, sql_text) -> stmt_handle (i64).

        Wraps sqlite3_prepare_v2. Returns 0 on failure or null db handle.
        """
        CompilationContext.require_feature("sqlite", "sql_prepare()")

        if len(args) != 2:
            raise ExprGenError("sql_prepare() expects (db_handle, sql_text)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 0)

        db_handle = self.generate_expr(args[ARG_FIRST])
        sql = self.generate_expr(args[ARG_SECOND])
        char_ptr = ir.IntType(8).as_pointer()
        db_ptr = self.builder.inttoptr(db_handle, char_ptr, name="db_ptr_prep")

        # Allocate stmt out-pointer; null-init in case prepare fails.
        stmt_ptr_ptr = self.builder.alloca(char_ptr, name="stmt_ptr_ptr")
        self.builder.store(ir.Constant(char_ptr, None), stmt_ptr_ptr)

        # Branch on null db handle.
        is_null = self.builder.icmp_unsigned(
            "==",
            db_handle,
            ir.Constant(ir.IntType(64), 0),
            name="db_is_null_prep",
        )
        null_block = self.function.append_basic_block("sql_prepare_null")
        call_block = self.function.append_basic_block("sql_prepare_call")
        merge_block = self.function.append_basic_block("sql_prepare_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        # Null path: skip the call.
        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        # Call path: sqlite3_prepare_v2(db, sql, -1, &stmt, NULL).
        self.builder.position_at_end(call_block)
        neg_one = ir.Constant(ir.IntType(32), -1)
        null_tail = ir.Constant(char_ptr.as_pointer(), None)
        rc = self.builder.call(
            self.codegen.get_sqlite3_prepare_v2(),
            [db_ptr, sql, neg_one, stmt_ptr_ptr, null_tail],
            name="prepare_rc",
        )
        ok = self.builder.icmp_unsigned(
            "==", rc, ir.Constant(ir.IntType(32), 0), name="prepare_ok"
        )
        stmt_loaded = self.builder.load(stmt_ptr_ptr, name="stmt_loaded")
        stmt_int = self.builder.ptrtoint(stmt_loaded, ir.IntType(64), name="stmt_int")
        # Failure branch returns 0; success returns the stmt handle.
        zero64 = ir.Constant(ir.IntType(64), 0)
        call_handle = self.builder.select(
            ok, stmt_int, zero64, name="prepare_handle_sel"
        )
        self.builder.branch(merge_block)
        call_end = self.builder.block

        # Merge.
        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="prepare_handle_phi")
        phi.add_incoming(zero64, null_end)
        phi.add_incoming(call_handle, call_end)
        return phi

    def _builtin_sql_step(self, args):
        """sql_step(stmt_handle) -> int.

        Wraps sqlite3_step. Returns SQLITE_ROW (100), SQLITE_DONE (101),
        another sqlite result code, or -1 if stmt handle is null.
        """
        CompilationContext.require_feature("sqlite", "sql_step()")

        if len(args) != 1:
            raise ExprGenError("sql_step() expects (stmt_handle)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_step")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_step",
        )
        null_block = self.function.append_basic_block("sql_step_null")
        call_block = self.function.append_basic_block("sql_step_call")
        merge_block = self.function.append_basic_block("sql_step_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_step(), [stmt_ptr], name="step_rc"
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="step_rc_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="step_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_bind_int(self, args):
        """sql_bind_int(stmt_handle, param_idx, value) -> sqlite result code."""
        CompilationContext.require_feature("sqlite", "sql_bind_int()")

        if len(args) != 3:
            raise ExprGenError("sql_bind_int() expects (stmt_handle, idx, value)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        idx = self.generate_expr(args[ARG_SECOND])
        val = self.generate_expr(args[ARG_THIRD])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_bi")
        idx_i32 = self.builder.trunc(idx, ir.IntType(32), name="idx_i32_bi")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_bi",
        )
        null_block = self.function.append_basic_block("sql_bind_int_null")
        call_block = self.function.append_basic_block("sql_bind_int_call")
        merge_block = self.function.append_basic_block("sql_bind_int_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_bind_int64(),
            [stmt_ptr, idx_i32, val],
            name="bind_int_rc",
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="bind_int_rc_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="bind_int_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_bind_text(self, args):
        """sql_bind_text(stmt_handle, param_idx, text) -> sqlite result code."""
        CompilationContext.require_feature("sqlite", "sql_bind_text()")

        if len(args) != 3:
            raise ExprGenError("sql_bind_text() expects (stmt_handle, idx, text)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        idx = self.generate_expr(args[ARG_SECOND])
        text = self.generate_expr(args[ARG_THIRD])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_bt")
        idx_i32 = self.builder.trunc(idx, ir.IntType(32), name="idx_i32_bt")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_bt",
        )
        null_block = self.function.append_basic_block("sql_bind_text_null")
        call_block = self.function.append_basic_block("sql_bind_text_call")
        merge_block = self.function.append_basic_block("sql_bind_text_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        transient = self.builder.inttoptr(
            ir.Constant(ir.IntType(64), -1), char_ptr, name="sqlite_transient"
        )
        result = self.builder.call(
            self.codegen.get_sqlite3_bind_text(),
            [
                stmt_ptr,
                idx_i32,
                text,
                ir.Constant(ir.IntType(32), -1),
                transient,
            ],
            name="bind_text_rc",
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="bind_text_rc_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="bind_text_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_bind_text_i64(self, args):
        """Bind prefix + int directly, avoiding a heap string in C parity paths."""
        if len(args) != 4:
            raise ExprGenError("sql_bind_text_i64() expects (stmt, idx, prefix, value)")
        return self._builtin_sql_bind_text_i64_parts(
            [args[ARG_FIRST], args[ARG_SECOND], args[ARG_THIRD], args[ARG_FOURTH], None]
        )

    def _builtin_sql_bind_text_i64_parts(self, args):
        """Bind prefix + int + suffix as text without exposing temp strings."""
        CompilationContext.require_feature("sqlite", "sql_bind_text_i64_parts()")

        if len(args) != 5:
            raise ExprGenError(
                "sql_bind_text_i64_parts() expects (stmt, idx, prefix, value, suffix)"
            )
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        idx = self.generate_expr(args[ARG_SECOND])
        prefix = self.generate_expr(args[ARG_THIRD])
        val = self.generate_expr(args[ARG_FOURTH])
        suffix = (
            self.codegen.create_string_constant("")
            if args[ARG_FIFTH] is None
            else self.generate_expr(args[ARG_FIFTH])
        )

        char = ir.IntType(8)
        char_ptr = char.as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_bti")
        idx_i32 = self.builder.trunc(idx, ir.IntType(32), name="idx_i32_bti")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_bti",
        )
        null_block = self.function.append_basic_block("sql_bind_text_i64_null")
        call_block = self.function.append_basic_block("sql_bind_text_i64_call")
        merge_block = self.function.append_basic_block("sql_bind_text_i64_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result64 = emit_sql_bind_text_i64_direct(
            self, stmt_ptr, idx_i32, prefix, val, suffix
        )
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="bind_text_i64_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_bind_null(self, args):
        """sql_bind_null(stmt_handle, param_idx) -> sqlite result code."""
        CompilationContext.require_feature("sqlite", "sql_bind_null()")

        if len(args) != 2:
            raise ExprGenError("sql_bind_null() expects (stmt_handle, idx)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        idx = self.generate_expr(args[ARG_SECOND])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_bn")
        idx_i32 = self.builder.trunc(idx, ir.IntType(32), name="idx_i32_bn")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_bn",
        )
        null_block = self.function.append_basic_block("sql_bind_null_null")
        call_block = self.function.append_basic_block("sql_bind_null_call")
        merge_block = self.function.append_basic_block("sql_bind_null_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_bind_null(),
            [stmt_ptr, idx_i32],
            name="bind_null_rc",
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="bind_null_rc_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="bind_null_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_clear_bindings(self, args):
        """sql_clear_bindings(stmt_handle) -> sqlite result code."""
        CompilationContext.require_feature("sqlite", "sql_clear_bindings()")

        if len(args) != 1:
            raise ExprGenError("sql_clear_bindings() expects (stmt_handle)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_cb")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_cb",
        )
        null_block = self.function.append_basic_block("sql_clear_bindings_null")
        call_block = self.function.append_basic_block("sql_clear_bindings_call")
        merge_block = self.function.append_basic_block("sql_clear_bindings_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_clear_bindings(),
            [stmt_ptr],
            name="clear_bindings_rc",
        )
        result64 = self.builder.sext(
            result, ir.IntType(64), name="clear_bindings_rc_64"
        )
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="clear_bindings_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_reset(self, args):
        """sql_reset(stmt_handle) -> int.

        Wraps sqlite3_reset. Returns sqlite result code or -1 on null
        statement handle.
        """
        CompilationContext.require_feature("sqlite", "sql_reset()")

        if len(args) != 1:
            raise ExprGenError("sql_reset() expects (stmt_handle)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), -1)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_reset")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_reset",
        )
        null_block = self.function.append_basic_block("sql_reset_null")
        call_block = self.function.append_basic_block("sql_reset_call")
        merge_block = self.function.append_basic_block("sql_reset_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_reset(), [stmt_ptr], name="reset_rc"
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="reset_rc_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="reset_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), -1), null_end)
        phi.add_incoming(result64, call_end)
        return phi

    def _builtin_sql_column_int(self, args):
        """sql_column_int(stmt_handle, col_idx) -> i64.

        Wraps sqlite3_column_int64 (always 64-bit; safer for ADAPT's clock-ns
        timestamps which exceed i32 range). Returns 0 if stmt handle is null.
        """
        CompilationContext.require_feature("sqlite", "sql_column_int()")

        if len(args) != 2:
            raise ExprGenError("sql_column_int() expects (stmt_handle, col_idx)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 0)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        col_idx = self.generate_expr(args[ARG_SECOND])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_ci")
        col_i32 = self.builder.trunc(col_idx, ir.IntType(32), name="col_i32_ci")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_ci",
        )
        null_block = self.function.append_basic_block("sql_col_int_null")
        call_block = self.function.append_basic_block("sql_col_int_call")
        merge_block = self.function.append_basic_block("sql_col_int_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_column_int64(),
            [stmt_ptr, col_i32],
            name="col_int_val",
        )
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="col_int_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), 0), null_end)
        phi.add_incoming(result, call_end)
        return phi

    def _builtin_sql_column_text(self, args):
        """sql_column_text(stmt_handle, col_idx) -> string (i8*).

        Wraps sqlite3_column_text. Returns "" if stmt handle is null or the
        column is SQL NULL. The returned pointer is owned by SQLite and is
        valid only until the next sql_step or sql_finalize on this stmt.
        """
        CompilationContext.require_feature("sqlite", "sql_column_text()")

        if len(args) != 2:
            raise ExprGenError("sql_column_text() expects (stmt_handle, col_idx)")
        char_ptr = ir.IntType(8).as_pointer()
        if not self.codegen.sqlite_available:
            return self.codegen.create_string_constant("")

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        col_idx = self.generate_expr(args[ARG_SECOND])
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_ct")
        col_i32 = self.builder.trunc(col_idx, ir.IntType(32), name="col_i32_ct")

        empty_ptr = self.codegen.create_string_constant("")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_ct",
        )
        null_block = self.function.append_basic_block("sql_col_text_null")
        call_block = self.function.append_basic_block("sql_col_text_call")
        merge_block = self.function.append_basic_block("sql_col_text_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        raw = self.builder.call(
            self.codegen.get_sqlite3_column_text(),
            [stmt_ptr, col_i32],
            name="col_text_raw",
        )
        # SQLite returns NULL for SQL NULL columns; substitute "" so AILang
        # callers never see a null string pointer.
        is_raw_null = self.builder.icmp_unsigned(
            "==",
            self.builder.ptrtoint(raw, ir.IntType(64), name="col_text_int"),
            ir.Constant(ir.IntType(64), 0),
            name="col_text_is_null",
        )
        text_or_empty = self.builder.select(
            is_raw_null, empty_ptr, raw, name="col_text_sel"
        )
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(char_ptr, name="col_text_phi")
        phi.add_incoming(empty_ptr, null_end)
        phi.add_incoming(text_or_empty, call_end)
        return phi

    def _builtin_sql_finalize(self, args):
        """sql_finalize(stmt_handle) -> int (sqlite result code, 0 = OK).

        Wraps sqlite3_finalize. Always safe to call on a null handle (returns 0).
        """
        CompilationContext.require_feature("sqlite", "sql_finalize()")

        if len(args) != 1:
            raise ExprGenError("sql_finalize() expects (stmt_handle)")
        if not self.codegen.sqlite_available:
            return ir.Constant(ir.IntType(64), 0)

        stmt_handle = self.generate_expr(args[ARG_FIRST])
        char_ptr = ir.IntType(8).as_pointer()
        stmt_ptr = self.builder.inttoptr(stmt_handle, char_ptr, name="stmt_ptr_fin")

        is_null = self.builder.icmp_unsigned(
            "==",
            stmt_handle,
            ir.Constant(ir.IntType(64), 0),
            name="stmt_is_null_fin",
        )
        null_block = self.function.append_basic_block("sql_finalize_null")
        call_block = self.function.append_basic_block("sql_finalize_call")
        merge_block = self.function.append_basic_block("sql_finalize_merge")
        self.builder.cbranch(is_null, null_block, call_block)

        self.builder.position_at_end(null_block)
        self.builder.branch(merge_block)
        null_end = self.builder.block

        self.builder.position_at_end(call_block)
        result = self.builder.call(
            self.codegen.get_sqlite3_finalize(), [stmt_ptr], name="finalize_rc"
        )
        result64 = self.builder.sext(result, ir.IntType(64), name="finalize_rc_64")
        self.builder.branch(merge_block)
        call_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="finalize_phi")
        phi.add_incoming(ir.Constant(ir.IntType(64), 0), null_end)
        phi.add_incoming(result64, call_end)
        return phi

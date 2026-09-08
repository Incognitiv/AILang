"""Extracted responsibilities for :class:`ExprBuiltinSqlEmitter`."""

from __future__ import annotations

from typing import Any

from llvmlite import ir
from runtime.modes import CompilationContext
from transpiler.expr_common import (
    ARG_FIRST,
    ARG_SECOND,
    ExprGenError,
)


class ExprBuiltinSqlColumnMixin:
    def _builtin_sql_column_text(self: Any, args):
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

    def _builtin_sql_finalize(self: Any, args):
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

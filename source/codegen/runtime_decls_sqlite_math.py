"""SQLite and libm runtime declarations for LLVM codegen."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class RuntimeDeclsSqliteMathMixin:
    _cg: Any

    def get_sqlite3_open(self) -> ir.Function:
        """Lazy declaration of sqlite3_open"""
        if self._cg.sqlite3_open_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_open_ty = ir.FunctionType(
                ir.IntType(32), [char_ptr, char_ptr.as_pointer()]
            )
            self._cg.sqlite3_open_func = ir.Function(
                self._cg.module, sqlite3_open_ty, "sqlite3_open"
            )
        return self._cg.sqlite3_open_func

    def get_sqlite3_open_v2(self) -> ir.Function:
        """Lazy declaration of sqlite3_open_v2."""
        if self._cg.sqlite3_open_v2_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_open_v2_ty = ir.FunctionType(
                ir.IntType(32),
                [char_ptr, char_ptr.as_pointer(), ir.IntType(32), char_ptr],
            )
            self._cg.sqlite3_open_v2_func = ir.Function(
                self._cg.module, sqlite3_open_v2_ty, "sqlite3_open_v2"
            )
        return self._cg.sqlite3_open_v2_func

    def get_sqlite3_close(self) -> ir.Function:
        """Lazy declaration of sqlite3_close"""
        if self._cg.sqlite3_close_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_close_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.sqlite3_close_func = ir.Function(
                self._cg.module, sqlite3_close_ty, "sqlite3_close"
            )
        return self._cg.sqlite3_close_func

    def get_sqlite3_exec(self) -> ir.Function:
        """Lazy declaration of sqlite3_exec"""
        if self._cg.sqlite3_exec_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_exec_ty = ir.FunctionType(
                ir.IntType(32),
                [char_ptr, char_ptr, char_ptr, char_ptr, char_ptr.as_pointer()],
            )
            self._cg.sqlite3_exec_func = ir.Function(
                self._cg.module, sqlite3_exec_ty, "sqlite3_exec"
            )
        return self._cg.sqlite3_exec_func

    def get_sqlite3_errmsg(self) -> ir.Function:
        """Lazy declaration of sqlite3_errmsg"""
        if self._cg.sqlite3_errmsg_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_errmsg_ty = ir.FunctionType(char_ptr, [char_ptr])
            self._cg.sqlite3_errmsg_func = ir.Function(
                self._cg.module, sqlite3_errmsg_ty, "sqlite3_errmsg"
            )
        return self._cg.sqlite3_errmsg_func

    def get_sqlite3_prepare_v2(self) -> ir.Function:
        """Lazy declaration of sqlite3_prepare_v2.

        ``int sqlite3_prepare_v2(sqlite3 *db, const char *zSql,
                                 int nByte, sqlite3_stmt **ppStmt,
                                 const char **pzTail)``
        """
        if self._cg.sqlite3_prepare_v2_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_prepare_v2_ty = ir.FunctionType(
                ir.IntType(32),
                [
                    char_ptr,
                    char_ptr,
                    ir.IntType(32),
                    char_ptr.as_pointer(),
                    char_ptr.as_pointer(),
                ],
            )
            self._cg.sqlite3_prepare_v2_func = ir.Function(
                self._cg.module, sqlite3_prepare_v2_ty, "sqlite3_prepare_v2"
            )
        return self._cg.sqlite3_prepare_v2_func

    def get_sqlite3_step(self) -> ir.Function:
        """Lazy declaration of sqlite3_step.

        ``int sqlite3_step(sqlite3_stmt *stmt)``
        """
        if self._cg.sqlite3_step_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_step_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.sqlite3_step_func = ir.Function(
                self._cg.module, sqlite3_step_ty, "sqlite3_step"
            )
        return self._cg.sqlite3_step_func

    def get_sqlite3_reset(self) -> ir.Function:
        """Lazy declaration of sqlite3_reset.

        ``int sqlite3_reset(sqlite3_stmt *stmt)``
        """
        if self._cg.sqlite3_reset_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_reset_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.sqlite3_reset_func = ir.Function(
                self._cg.module, sqlite3_reset_ty, "sqlite3_reset"
            )
        return self._cg.sqlite3_reset_func

    def get_sqlite3_bind_int64(self) -> ir.Function:
        """Lazy declaration of sqlite3_bind_int64."""
        if self._cg.sqlite3_bind_int64_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_bind_int64_ty = ir.FunctionType(
                ir.IntType(32), [char_ptr, ir.IntType(32), ir.IntType(64)]
            )
            self._cg.sqlite3_bind_int64_func = ir.Function(
                self._cg.module,
                sqlite3_bind_int64_ty,
                "sqlite3_bind_int64",
            )
        return self._cg.sqlite3_bind_int64_func

    def get_sqlite3_bind_text(self) -> ir.Function:
        """Lazy declaration of sqlite3_bind_text."""
        if self._cg.sqlite3_bind_text_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_bind_text_ty = ir.FunctionType(
                ir.IntType(32),
                [char_ptr, ir.IntType(32), char_ptr, ir.IntType(32), char_ptr],
            )
            self._cg.sqlite3_bind_text_func = ir.Function(
                self._cg.module,
                sqlite3_bind_text_ty,
                "sqlite3_bind_text",
            )
        return self._cg.sqlite3_bind_text_func

    def get_sqlite3_bind_null(self) -> ir.Function:
        """Lazy declaration of sqlite3_bind_null."""
        if self._cg.sqlite3_bind_null_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_bind_null_ty = ir.FunctionType(
                ir.IntType(32), [char_ptr, ir.IntType(32)]
            )
            self._cg.sqlite3_bind_null_func = ir.Function(
                self._cg.module,
                sqlite3_bind_null_ty,
                "sqlite3_bind_null",
            )
        return self._cg.sqlite3_bind_null_func

    def get_sqlite3_clear_bindings(self) -> ir.Function:
        """Lazy declaration of sqlite3_clear_bindings."""
        if self._cg.sqlite3_clear_bindings_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_clear_bindings_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.sqlite3_clear_bindings_func = ir.Function(
                self._cg.module,
                sqlite3_clear_bindings_ty,
                "sqlite3_clear_bindings",
            )
        return self._cg.sqlite3_clear_bindings_func

    def get_sqlite3_column_int64(self) -> ir.Function:
        """Lazy declaration of sqlite3_column_int64.

        ``sqlite3_int64 sqlite3_column_int64(sqlite3_stmt *stmt,
                                             int iCol)``
        """
        if self._cg.sqlite3_column_int64_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_column_int64_ty = ir.FunctionType(
                ir.IntType(64), [char_ptr, ir.IntType(32)]
            )
            self._cg.sqlite3_column_int64_func = ir.Function(
                self._cg.module,
                sqlite3_column_int64_ty,
                "sqlite3_column_int64",
            )
        return self._cg.sqlite3_column_int64_func

    def get_sqlite3_column_text(self) -> ir.Function:
        """Lazy declaration of sqlite3_column_text.

        ``const unsigned char *sqlite3_column_text(sqlite3_stmt *stmt,
                                                   int iCol)``
        """
        if self._cg.sqlite3_column_text_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_column_text_ty = ir.FunctionType(
                char_ptr, [char_ptr, ir.IntType(32)]
            )
            self._cg.sqlite3_column_text_func = ir.Function(
                self._cg.module,
                sqlite3_column_text_ty,
                "sqlite3_column_text",
            )
        return self._cg.sqlite3_column_text_func

    def get_sqlite3_finalize(self) -> ir.Function:
        """Lazy declaration of sqlite3_finalize.

        ``int sqlite3_finalize(sqlite3_stmt *stmt)``
        """
        if self._cg.sqlite3_finalize_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sqlite3_finalize_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.sqlite3_finalize_func = ir.Function(
                self._cg.module, sqlite3_finalize_ty, "sqlite3_finalize"
            )
        return self._cg.sqlite3_finalize_func

    def get_exp(self) -> ir.Function:
        """Lazy declaration of exp (exponential)"""
        if self._cg.exp_func is None:
            double = ir.DoubleType()
            exp_ty = ir.FunctionType(double, [double])
            self._cg.exp_func = ir.Function(self._cg.module, exp_ty, "exp")
        return self._cg.exp_func

    def get_log(self) -> ir.Function:
        """Lazy declaration of log (natural logarithm)"""
        if self._cg.log_func is None:
            double = ir.DoubleType()
            log_ty = ir.FunctionType(double, [double])
            self._cg.log_func = ir.Function(self._cg.module, log_ty, "log")
        return self._cg.log_func

    def get_sqrt(self) -> ir.Function:
        """Lazy declaration of sqrt (square root)"""
        if self._cg.sqrt_func is None:
            double = ir.DoubleType()
            sqrt_ty = ir.FunctionType(double, [double])
            self._cg.sqrt_func = ir.Function(self._cg.module, sqrt_ty, "sqrt")
        return self._cg.sqrt_func

    def get_sin(self) -> ir.Function:
        """Lazy declaration of sin"""
        if self._cg.sin_func is None:
            double = ir.DoubleType()
            sin_ty = ir.FunctionType(double, [double])
            self._cg.sin_func = ir.Function(self._cg.module, sin_ty, "sin")
        return self._cg.sin_func

    def get_cos(self) -> ir.Function:
        """Lazy declaration of cos"""
        if self._cg.cos_func is None:
            double = ir.DoubleType()
            cos_ty = ir.FunctionType(double, [double])
            self._cg.cos_func = ir.Function(self._cg.module, cos_ty, "cos")
        return self._cg.cos_func

    def get_tan(self) -> ir.Function:
        """Lazy declaration of tan"""
        if self._cg.tan_func is None:
            double = ir.DoubleType()
            tan_ty = ir.FunctionType(double, [double])
            self._cg.tan_func = ir.Function(self._cg.module, tan_ty, "tan")
        return self._cg.tan_func

    def get_tanh(self) -> ir.Function:
        """Lazy declaration of tanh (hyperbolic tangent)"""
        if self._cg.tanh_func is None:
            double = ir.DoubleType()
            tanh_ty = ir.FunctionType(double, [double])
            self._cg.tanh_func = ir.Function(self._cg.module, tanh_ty, "tanh")
        return self._cg.tanh_func

    def get_pow(self) -> ir.Function:
        """Lazy declaration of pow (power)"""
        if self._cg.pow_func is None:
            double = ir.DoubleType()
            pow_ty = ir.FunctionType(double, [double, double])
            self._cg.pow_func = ir.Function(self._cg.module, pow_ty, "pow")
        return self._cg.pow_func

    def get_floor(self) -> ir.Function:
        """Lazy declaration of floor"""
        if self._cg.floor_func is None:
            double = ir.DoubleType()
            floor_ty = ir.FunctionType(double, [double])
            self._cg.floor_func = ir.Function(self._cg.module, floor_ty, "floor")
        return self._cg.floor_func

    def get_ceil(self) -> ir.Function:
        """Lazy declaration of ceil"""
        if self._cg.ceil_func is None:
            double = ir.DoubleType()
            ceil_ty = ir.FunctionType(double, [double])
            self._cg.ceil_func = ir.Function(self._cg.module, ceil_ty, "ceil")
        return self._cg.ceil_func

    def get_fabs(self) -> ir.Function:
        """Lazy declaration of fabs (floating-point absolute value)"""
        if self._cg.fabs_func is None:
            double = ir.DoubleType()
            fabs_ty = ir.FunctionType(double, [double])
            self._cg.fabs_func = ir.Function(self._cg.module, fabs_ty, "fabs")
        return self._cg.fabs_func

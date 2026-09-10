"""Early, representation-sensitive fast paths for C call lowering."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from ast_access import arg_at
from target_info import os_from_platform
from transpiler.arithmetic_literal_proofs import int_literal_value
from transpiler.fixed_int_types import info_for_c_fixed

from .expr_gen_call_common import _runtime_i64_arg


def dispatch_surface_call(self: Any, node: A.Call) -> str | None:
    # AILang's `putc(c)` is the documented "print one character"
    # builtin (single-arg). C's libc `putc(c, FILE*)` takes two
    # args, so a literal passthrough fails to compile. Re-route to
    # `putchar(c)` which has the right shape.
    if node.name == "putc" and len(node.args) == 1:
        return f"(int64_t)__ailang_putchar_raw((int){self.expr(arg_at(node, 0))})"
    if node.name == "puts" and len(node.args) == 1:
        return f"(int64_t)__ailang_puts_raw({self.expr(arg_at(node, 0))})"
    if node.name == "target_os" and not node.args:
        return f'"{os_from_platform()}"'
    if node.name == "target_backend" and not node.args:
        return '"c"'
    if node.name == "offsetof" and node.name not in self.user_defined_funcs:
        if len(node.args) != 2:
            raise ValueError("offsetof() expects exactly 2 arguments")
        type_arg, field_arg = node.args
        if not isinstance(type_arg, A.StringLit) or not isinstance(
            field_arg, A.StringLit
        ):
            raise ValueError(
                'offsetof() expects string literals: offsetof("Type", "field")'
            )
        return self._emit_offsetof(type_arg.value, field_arg.value)
    return None


def dispatch_sql_call(self: Any, node: A.Call) -> str | None:
    # SQLite handles MUST flow through user code as int64_t (since
    # AILang has no native pointer-typed handles); the C runtime
    # functions take/return real `sqlite3 *` / `sqlite3_stmt *`.
    # Bridge in both directions at the call boundary so user code
    # never has to know the difference.
    if node.name == "sql_open" and len(node.args) >= 1:
        return f"((int64_t)(uintptr_t)sql_open({self.expr(arg_at(node, 0))}))"
    if node.name == "sql_open_readonly" and len(node.args) >= 1:
        return f"((int64_t)(uintptr_t)sql_open_readonly({self.expr(arg_at(node, 0))}))"
    if node.name == "sql_exec" and len(node.args) >= 2:
        db = self.expr(arg_at(node, 0))
        sql = self.expr(arg_at(node, 1))
        return f"sql_exec((sqlite3 *)(uintptr_t)({db}), {sql})"
    if node.name == "sql_close" and len(node.args) >= 1:
        db = self.expr(arg_at(node, 0))
        # sql_close returns void; emit the bare call so it works as a
        # statement under -Werror=unused-value. Expression-context use
        # would (correctly) fail since you can't take the value of a
        # void call.
        return f"sql_close((sqlite3 *)(uintptr_t)({db}))"
    if node.name == "sql_prepare" and len(node.args) >= 2:
        db = self.expr(arg_at(node, 0))
        sql = self.expr(arg_at(node, 1))
        return (
            f"((int64_t)(uintptr_t)sql_prepare("
            f"(sqlite3 *)(uintptr_t)({db}), {sql}))"
        )
    if node.name == "sql_step" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"sql_step((sqlite3_stmt *)(uintptr_t)({stmt}))"
    if node.name == "sql_bind_int" and len(node.args) >= 3:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        val = self.expr(arg_at(node, 2))
        return f"sql_bind_int((sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {val})"
    if node.name == "sql_bind_text" and len(node.args) >= 3:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        val = self.expr(arg_at(node, 2))
        return f"sql_bind_text((sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {val})"
    if node.name == "sql_bind_text_i64" and len(node.args) >= 4:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        prefix = self.expr(arg_at(node, 2))
        val = self.expr(node.args[3])
        return (
            "sql_bind_text_i64("
            f"(sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {prefix}, {val})"
        )
    if node.name == "sql_bind_text_i64_parts" and len(node.args) >= 5:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        prefix = self.expr(arg_at(node, 2))
        val = self.expr(node.args[3])
        suffix = self.expr(node.args[4])
        return (
            "sql_bind_text_i64_parts("
            f"(sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {prefix}, {val}, {suffix})"
        )
    if node.name == "sql_bind_null" and len(node.args) >= 2:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        return f"sql_bind_null((sqlite3_stmt *)(uintptr_t)({stmt}), {idx})"
    if node.name == "sql_clear_bindings" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"sql_clear_bindings((sqlite3_stmt *)(uintptr_t)({stmt}))"
    if node.name == "sql_finalize" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"sql_finalize((sqlite3_stmt *)(uintptr_t)({stmt}))"
    # sql_reset: rewind a prepared statement so it can be re-executed
    # without re-parsing the SQL. Critical for hot paths — the
    # alternative is sql_prepare per request, which is ~20% of CPU
    # in prepared-statement request hot paths. With cached statements +
    # sql_reset, the parser cost is paid once at startup rather than per request.
    if node.name == "sql_reset" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"((int64_t)sqlite3_reset(" f"(sqlite3_stmt *)(uintptr_t)({stmt})))"
    if node.name == "sql_column_int" and len(node.args) >= 2:
        stmt = self.expr(arg_at(node, 0))
        col = self.expr(arg_at(node, 1))
        return f"sql_column_int((sqlite3_stmt *)(uintptr_t)({stmt}), {col})"
    if node.name == "sql_column_text" and len(node.args) >= 2:
        stmt = self.expr(arg_at(node, 0))
        col = self.expr(arg_at(node, 1))
        return f"sql_column_text((sqlite3_stmt *)(uintptr_t)({stmt}), {col})"
    return None


def dispatch_array_call(self: Any, node: A.Call) -> str | None:
    # Dynamic arrays are explicitly i64-backed. Wide integers may cross this
    # boundary only through checked narrowing; otherwise values like 1<<200
    # used to be silently truncated by the generated C.
    if node.name == "array_new" and len(node.args) >= 1:
        cap_node = arg_at(node, 0)
        cap = _runtime_i64_arg(self, cap_node, self.expr(cap_node))
        return f"array_new({cap})"
    if node.name == "array_push" and len(node.args) >= 2:
        val_node = arg_at(node, 1)
        if self._class_ptr_type(val_node) is None:
            arr = self.expr(arg_at(node, 0))
            val = _runtime_i64_arg(self, val_node, self.expr(val_node))
            return f"array_push({arr}, {val})"
    if node.name == "array_set" and len(node.args) >= 3:
        val_node = arg_at(node, 2)
        if self._class_ptr_type(val_node) is None:
            arr = self.expr(arg_at(node, 0))
            idx_node = arg_at(node, 1)
            idx = _runtime_i64_arg(self, idx_node, self.expr(idx_node))
            val = _runtime_i64_arg(self, val_node, self.expr(val_node))
            return f"array_set({arr}, {idx}, {val})"
    if node.name == "array_get" and len(node.args) >= 2:
        idx_node = arg_at(node, 1)
        idx_fixed = info_for_c_fixed(self._infer_type(idx_node))
        if idx_fixed is not None and idx_fixed.bits > 64:
            arr = self.expr(arg_at(node, 0))
            idx = _runtime_i64_arg(self, idx_node, self.expr(idx_node))
            return f"array_get({arr}, {idx})"

    # Class instances are pointers; the dynamic-array runtime stores
    # int64_t. Cast the pointer via uintptr_t so we don't lose bits.
    if node.name in ("array_push", "array_set") and len(node.args) >= 2:
        val_idx = 1 if node.name == "array_push" else 2
        val_node = node.args[val_idx]
        if self._class_ptr_type(val_node) is not None:
            val_c = self.expr(val_node)
            arr_c = self.expr(arg_at(node, 0))
            if node.name == "array_push":
                return f"array_push({arr_c}, (int64_t)(uintptr_t)({val_c}))"
            idx_c = self.expr(arg_at(node, 1))
            return f"array_set({arr_c}, {idx_c}, (int64_t)(uintptr_t)({val_c}))"

    if node.name == "array_get" and len(node.args) >= 2:
        arr_node = arg_at(node, 0)
        idx_node = arg_at(node, 1)
        if isinstance(arr_node, A.FieldAccess) and isinstance(idx_node, A.Number):
            owner = None
            if isinstance(arr_node.object_expr, A.Variable):
                owner = arr_node.object_expr.name
            elif isinstance(arr_node.object_expr, A.ThisExpr):
                owner = getattr(self, "_inline_this_stack_var", None)
            values = getattr(self, "_stack_array_field_values", {}).get(
                (owner, arr_node.field_name)
            )
            if values is not None and not idx_node.is_float:
                index_value = int(idx_node.value)
                if 0 <= index_value < len(values):
                    return values[index_value]
        if isinstance(arr_node, (A.Variable, A.FieldAccess)) and isinstance(
            idx_node, A.Number
        ):
            arr_c = self.expr(arr_node)
            idx_c = self.expr(idx_node)
            return (
                f"(({idx_c} < 0 || {idx_c} >= {arr_c}.length) "
                f"? 0 : {arr_c}.data[{idx_c}])"
            )
    return None


def dispatch_string_fastpath(self: Any, node: A.Call) -> str | None:
    if node.name == "streq" and node.name not in self.user_defined_funcs:
        from transpiler.expr_string_fastpath import emit_streq_literal_fastpath

        fast_streq = emit_streq_literal_fastpath(self, node)
        if fast_streq is not None:
            return fast_streq

    if node.name in ("strlen", "len") and len(node.args) == 1:
        return self._emit_known_strlen(arg_at(node, 0))

    if node.name == "char_at" and len(node.args) >= 2:
        from transpiler.expr_string_fastpath import literal_char_at_byte_value

        literal_value = literal_char_at_byte_value(arg_at(node, 0), arg_at(node, 1))
        if literal_value is not None:
            return f"{literal_value}LL"
        if len(node.args) >= 3:
            char_index_value = int_literal_value(arg_at(node, 1))
            length_value = int_literal_value(arg_at(node, 2))
            if (
                char_index_value is not None
                and length_value is not None
                and 0 <= char_index_value < length_value
            ):
                string_expr = self.expr(arg_at(node, 0))
                index_expr = self.expr(arg_at(node, 1))
                self._record_check_decision(
                    node,
                    check_kind="bounds",
                    operation="char_at",
                    decision="elided",
                    reason="literal_length_proven",
                )
                return f"((int64_t)(unsigned char)({string_expr})[{index_expr}])"
        facts = getattr(self, "range_facts", None)
        if (
            facts is not None
            and hasattr(facts, "is_safe_char_at_call")
            and facts.is_safe_char_at_call(self.current_function, node)
        ):
            string_expr = self.expr(arg_at(node, 0))
            index_expr = self.expr(arg_at(node, 1))
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="char_at",
                decision="elided",
                reason="range_proven",
            )
            return f"((int64_t)(unsigned char)({string_expr})[{index_expr}])"
    return None

"""Extracted responsibilities for :class:`HelperScanner`."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from ast_access import arg_at
from transpiler.arithmetic_literal_proofs import (
    literal_int_arithmetic_safe,
    neutral_int_arithmetic_safe,
    positive_int_literal,
    shift_amount_literal_in_range,
)
from transpiler.strlen_assign_cache import (
    baseconv_known_integer_arg,
)
from transpiler.wide_int_types import info_for_ailang


class HelperScannerDetailMixin:
    def _scan_call(self: Any, node: A.Call) -> None:
        name = node.name
        # sizeof("wide") and friends reference a wide C typedef without a
        # typed variable declaration. Make sure the wide typedef/runtime
        # prologue is emitted for that form too.
        if name == "sizeof" and node.args:
            first = arg_at(node, 0)
            if (
                isinstance(first, A.StringLit)
                and info_for_ailang(first.value) is not None
            ):
                self._needs.wide_ints = True
        if self._scan_streq_slice_fastpath(node):
            return
        if self._cached_strlen_field_arg(node):
            self._scan_node(arg_at(node, 0))
            return
        virtual_strlen_arg = self._virtual_strlen_numeric_arg(node)
        if virtual_strlen_arg is not None:
            self._needs.helpers.add("i64_decimal_len")
            self._scan_node(virtual_strlen_arg)
            return
        baseconv_strlen_arg = None
        if name in {"len", "strlen"} and node.args:
            baseconv_strlen_arg = baseconv_known_integer_arg(self, arg_at(node, 0))
        if baseconv_strlen_arg is not None:
            _kind, arg = baseconv_strlen_arg
            self._needs.helpers.add("base_conv_len")
            self._scan_node(arg)
            return
        if self._literal_char_at_length_proven(node):
            for arg in node.args[:2]:
                self._scan_node(arg)
            return
        if name in self._CALL_HELPER_MAP:
            self._needs.helpers.add(self._CALL_HELPER_MAP[name])
            if name in ("thread_id", "num_cpus", "yield_thread", "sleep_ms"):
                self._needs.threading = True
            # `concat(...)` with owned-alloc args is rerouted through
            # ailang_strcat_n at emit time. The scanner must pre-register
            # both helpers so the runtime functions land in the prologue;
            # strcat_n lives inside the "strcat" emission block, so both
            # flags must be set or the call site references an undeclared
            # symbol.
            if (
                name == "concat"
                and len(node.args) >= 2
                and any(self._is_owned_string_alloc(a) for a in node.args)
            ):
                self._needs.helpers.add("strcat")
                self._needs.helpers.add("strcat_n")
        elif name.startswith("vec_"):
            self._needs.helpers.add("simd")
        elif name in ("spawn", "join"):
            self._needs.threading = True
        elif name.startswith("atomic_"):
            self._needs.atomics = True
        elif name.startswith("channel"):
            self._needs.channels = True
        elif name.startswith(("mutex_", "cond_", "rwlock_")):
            self._needs.sync = True
        for arg in node.args:
            self._scan_node(arg)

    def _scan_binary_op(self: Any, node: A.BinaryOp) -> None:
        can_elide = (
            self._can_elide_binary_safety is not None
            and self._can_elide_binary_safety(node, self._func_scope)
        )
        literal_elide = neutral_int_arithmetic_safe(node) is not None
        if not literal_elide:
            literal_elide = (
                literal_int_arithmetic_safe(
                    node,
                    bit_width=64,
                    is_unsigned=False,
                )
                is not None
            )
        safe_elided = can_elide or literal_elide

        if node.op in ("+", "plus"):
            if self._is_string_expr(node.left) or self._is_string_expr(node.right):
                self._needs.helpers.add("strcat")
            elif not self._scanning_unchecked and not safe_elided:
                self._needs.helpers.add("safe_add")
        if (
            node.op in ("-", "minus")
            and not self._scanning_unchecked
            and not safe_elided
        ):
            self._needs.helpers.add("safe_sub")
        if (
            node.op in ("*", "star")
            and not self._scanning_unchecked
            and not safe_elided
        ):
            self._needs.helpers.add("safe_mul")
        if node.op in ("**", "^"):
            self._needs.helpers.add("math")
        if (
            node.op in ("/", "//", "%", "slash", "mod")
            and not self._scanning_unchecked
            and not positive_int_literal(node.right)
        ):
            self._needs.helpers.add("safe_div")
        if (
            node.op in ("<<", ">>", "shl", "shr", "ushr")
            and not self._scanning_unchecked
            and not shift_amount_literal_in_range(node.right, 64)
        ):
            self._needs.helpers.add("safe_shift")
        self._scan_node(node.left)
        self._scan_node(node.right)

    def _scan_interp_string(self: Any, node: A.InterpolatedString) -> None:
        self._needs.helpers.add("strcat")
        self._needs.helpers.add("int_to_str")
        for part in node.parts:
            if not isinstance(part, str):
                self._scan_node(part)

    def _scan_if(self: Any, node: A.If) -> None:
        self._scan_node(node.cond)
        for stmt in node.then_body:
            self._scan_node(stmt)
        if node.else_body:
            for stmt in node.else_body:
                self._scan_node(stmt)
        if hasattr(node, "elsif_branches") and node.elsif_branches:
            for cond, body in node.elsif_branches:
                self._scan_node(cond)
                for stmt in body:
                    self._scan_node(stmt)

    def _scan_for(self: Any, node: A.For) -> None:
        if node.init:
            self._scan_node(node.init)
        if node.cond:
            self._scan_node(node.cond)
        if node.step:
            self._scan_node(node.step)
        for stmt in node.body:
            self._scan_node(stmt)

    def _scan_foreach(self: Any, node: A.Foreach) -> None:
        self._scan_node(node.iterable)
        if not isinstance(node.iterable, A.Range):
            self._needs.arrays = True
        for stmt in node.body:
            self._scan_node(stmt)

    def _scan_array_access(self: Any, node: A.ArrayAccess) -> None:
        self._scan_node(node.array)
        self._scan_node(node.index)
        if (
            not getattr(node, "unsafe", False)
            and isinstance(node.array, A.Variable)
            and (
                node.array.name in self._array_vars
                or node.array.name in self._dyn_array_vars
            )
            and not self._array_access_literal_proven(node)
        ):
            self._needs.helpers.add("safe_array")

    def _scan_fixed_dict_literal_assignment(
        self: Any, var_name: str, value: A.ASTNode
    ) -> bool:
        if (
            not isinstance(value, A.DictLit)
            or var_name not in self._fixed_dict_literal_slots
        ):
            return False
        for key, item_val in value.pairs:
            self._scan_node(key)
            self._scan_node(item_val)
        return True

    def _is_fixed_dict_expr(self: Any, expr: A.ASTNode) -> bool:
        return (
            isinstance(expr, A.Variable) and expr.name in self._fixed_dict_literal_slots
        )

    def _scan_dict_assign(self: Any, node: A.DictAssign) -> None:
        # Dict helper only fires when the target is a known dict variable;
        # `obj.field[idx] = val` is array-style, no dict helper needed.
        if self._is_fixed_dict_expr(node.dict_expr):
            self._scan_node(node.key_expr)
            self._scan_node(node.value_expr)
            return
        if (
            isinstance(node.dict_expr, A.Variable)
            and node.dict_expr.name in self._dict_vars
        ):
            self._needs.dicts = True
            self._needs.helpers.add("dict")
        self._scan_node(node.dict_expr)
        self._scan_node(node.key_expr)
        self._scan_node(node.value_expr)

    def _scan_match(self: Any, node: A.Match) -> None:
        self._scan_node(node.expr)
        for case_val, case_body in node.cases:
            if not isinstance(case_val, A.MatchPattern):
                self._scan_node(case_val)
            for stmt in case_body:
                self._scan_node(stmt)
        if node.default_case:
            for stmt in node.default_case:
                self._scan_node(stmt)

    def _scan_try_except(self: Any, node: A.TryExcept) -> None:
        # Without this, helpers used only inside a try block never make it
        # into the needs set and the runtime emit skips them, producing
        # `implicit declaration` errors at C compile time.
        if node.try_expr is not None:
            self._scan_node(node.try_expr)
        for stmt in node.try_body:
            self._scan_node(stmt)
        for _err_type, _var_name, body in node.catch_blocks:
            for stmt in body:
                self._scan_node(stmt)
        if node.except_block:
            _ev, except_body = node.except_block
            for stmt in except_body:
                self._scan_node(stmt)
        if node.finally_block:
            for stmt in node.finally_block:
                self._scan_node(stmt)

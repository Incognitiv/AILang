from __future__ import annotations

from parser import ast as A
from typing import Dict, List, Optional, Set, Tuple

from ast_access import arg_at

from .range_facts_types import Interval, StringInfo


class RangeFactsProofMixin:
    call_hint_params: Dict[str, Set[str]]
    array_infos: Dict[Optional[str], Dict[str, Tuple[Interval, int]]]
    dict_value_infos: Dict[Optional[str], Dict[str, Dict[str, Interval]]]
    function_return_ranges: Dict[str, Interval]
    scope_relations: Dict[Optional[str], Set[Tuple[str, str, str]]]
    unknown_reasons: Dict[Tuple[Optional[str], str], str]

    def _expr_scope_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, Interval]]:
        raise NotImplementedError

    def _expr_unknown_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, str]]:
        raise NotImplementedError

    def _expr_array_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, Tuple[Interval, int]]]:
        raise NotImplementedError

    def _expr_dict_value_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, Dict[str, Interval]]]:
        raise NotImplementedError

    def _expr_string_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, StringInfo]]:
        raise NotImplementedError

    def _expr_loop_reason_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, str]]:
        raise NotImplementedError

    def _expr_relation_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Set[Tuple[str, str, str]]]:
        raise NotImplementedError

    def _range_from_scope(
        self, _var_name: str, _scope: Optional[Dict[str, Interval]]
    ) -> Optional[Interval]:
        raise NotImplementedError

    def _unknown_from_scope(
        self, _var_name: str, _scope: Optional[Dict[str, str]]
    ) -> Optional[str]:
        raise NotImplementedError

    def get_var_range(
        self, _var_name: str, _func_scope: Optional[str]
    ) -> Optional[Interval]:
        raise NotImplementedError

    def get_array_info(
        self, _var_name: str, _func_scope: Optional[str]
    ) -> Optional[Tuple[Interval, int]]:
        raise NotImplementedError

    def get_dict_value_info(
        self, _var_name: str, _key: str, _func_scope: Optional[str]
    ) -> Optional[Interval]:
        raise NotImplementedError

    def get_string_info(
        self, _func_scope: Optional[str], _var_name: str
    ) -> Optional[StringInfo]:
        raise NotImplementedError

    def can_prove_no_overflow(
        self, node: A.BinaryOp, func_scope: Optional[str]
    ) -> bool:
        """Return True when + / - / * is provably in int64 range."""
        proven, _reason = self.explain_no_overflow_for_int(
            node, func_scope, bit_width=64, is_unsigned=False
        )
        return proven

    def can_prove_no_overflow_for_int(
        self,
        node: A.BinaryOp,
        func_scope: Optional[str],
        bit_width: int,
        is_unsigned: bool,
    ) -> bool:
        """Return True when + / - / * is provably in target integer range."""
        proven, _reason = self.explain_no_overflow_for_int(
            node,
            func_scope,
            bit_width=bit_width,
            is_unsigned=is_unsigned,
        )
        return proven

    def can_prove_safe_modulo(
        self,
        node: A.BinaryOp,
        func_scope: Optional[str],
        *,
        bit_width: int,
        is_unsigned: bool,
    ) -> bool:
        """Return True when modulo cannot hit zero-divisor or signed overflow traps."""
        if node.op not in {"%", "mod"} or bit_width <= 0:
            return False
        # Positive divisors exclude modulo-by-zero and the signed INT_MIN % -1 trap.
        return self._can_prove_positive_rhs(node, func_scope)

    def can_prove_safe_division(
        self,
        node: A.BinaryOp,
        func_scope: Optional[str],
        *,
        bit_width: int,
        is_unsigned: bool,
    ) -> bool:
        """Return True when division cannot hit zero-divisor or signed traps."""
        if node.op not in {"/", "//", "slash"} or bit_width <= 0:
            return False
        # A positive signed divisor excludes both divide-by-zero and INT_MIN / -1.
        return self._can_prove_positive_rhs(node, func_scope)

    def _can_prove_positive_rhs(
        self, node: A.BinaryOp, func_scope: Optional[str]
    ) -> bool:
        """Return True when the right-hand operand interval is strictly positive."""
        snapshot = self._expr_scope_snapshot(node, func_scope)
        if snapshot is None:
            return False
        unknowns = self._expr_unknown_snapshot(node, func_scope)
        arrays = self._expr_array_snapshot(node, func_scope)
        loop_reasons = self._expr_loop_reason_snapshot(node, func_scope)
        relations = self._expr_relation_snapshot(node, func_scope)
        right, _right_reason = self._expr_interval_with_reason(
            node.right, func_scope, snapshot, unknowns, arrays, loop_reasons, relations
        )
        if right is None:
            return False
        return right.low > 0

    def explain_no_overflow(
        self, node: A.BinaryOp, func_scope: Optional[str]
    ) -> Tuple[bool, str]:
        return self.explain_no_overflow_for_int(
            node, func_scope, bit_width=64, is_unsigned=False
        )

    def explain_no_overflow_for_int(
        self,
        node: A.BinaryOp,
        func_scope: Optional[str],
        bit_width: int,
        is_unsigned: bool,
    ) -> Tuple[bool, str]:
        """Return (proven, reason_code) for + / - / * overflow checks."""
        if node.op not in {"+", "-", "*"}:
            return False, "op_unsupported"
        if bit_width <= 0:
            return False, "width_unknown"
        snapshot = self._expr_scope_snapshot(node, func_scope)
        if snapshot is None:
            return False, "point_unknown"
        unknowns = self._expr_unknown_snapshot(node, func_scope)
        arrays = self._expr_array_snapshot(node, func_scope)
        loop_reasons = self._expr_loop_reason_snapshot(node, func_scope)
        relations = self._expr_relation_snapshot(node, func_scope)
        taint = self._overflow_unknown_taint(node, unknowns, loop_reasons)
        if taint is not None:
            return False, taint
        left, left_reason = self._expr_interval_with_reason(
            node.left, func_scope, snapshot, unknowns, arrays, loop_reasons, relations
        )
        right, right_reason = self._expr_interval_with_reason(
            node.right, func_scope, snapshot, unknowns, arrays, loop_reasons, relations
        )
        if left is None or right is None:
            if left is None and left_reason != "ok":
                return False, left_reason
            if right is None and right_reason != "ok":
                return False, right_reason
            return False, "range_unknown"
        result = self._binary_interval(node.op, left, right)
        if result is None:
            return False, "op_unsupported"
        if is_unsigned:
            min_val = 0
            max_val = (1 << bit_width) - 1
        else:
            min_val = -(1 << (bit_width - 1))
            max_val = (1 << (bit_width - 1)) - 1
        if min_val <= result.low <= result.high <= max_val:
            return True, self._classify_proven_reason(
                node=node,
                func_scope=func_scope,
                left_reason=left_reason,
                right_reason=right_reason,
                loop_reasons=loop_reasons,
            )
        return False, "result_out_of_bounds"

    def _classify_proven_reason(
        self,
        *,
        node: A.BinaryOp,
        func_scope: Optional[str],
        left_reason: str,
        right_reason: str,
        loop_reasons: Optional[Dict[str, str]],
    ) -> str:
        """Best-effort reason code for why a proven overflow check was elided."""
        preferred = (
            "protocol_parser_proven",
            "protocol_modulo_proven",
            "loop_accumulator_proven",
            "loop_counter_proven",
            "loop_guard_proven",
        )
        for reason in (left_reason, right_reason):
            if reason in preferred:
                return reason
        if loop_reasons:
            for expr in (node.left, node.right):
                if isinstance(expr, A.Variable):
                    found = loop_reasons.get(expr.name)
                    if found in preferred:
                        return str(found)
                if isinstance(expr, A.ArrayAccess) and isinstance(
                    expr.index, A.Variable
                ):
                    found = loop_reasons.get(expr.index.name)
                    if found in preferred:
                        return str(found)
        call_hint_vars = self.call_hint_params.get(func_scope or "", set())
        for expr in (node.left, node.right):
            if isinstance(expr, A.Variable) and expr.name in call_hint_vars:
                return "call_hint_proven"
        return "range_proven"

    def _overflow_unknown_taint(
        self,
        expr: A.ASTNode,
        unknowns: Optional[Dict[str, str]],
        loop_reasons: Optional[Dict[str, str]],
    ) -> Optional[str]:
        """Reject no-wrap proofs when a local refinement masks unsafe provenance."""
        if not unknowns:
            return None
        preferred_loop_reasons = {
            "protocol_parser_proven",
            "protocol_modulo_proven",
            "loop_accumulator_proven",
            "loop_counter_proven",
            "loop_guard_proven",
        }
        blocked = {
            "loop_branch_unknown",
            "loop_unknown",
            "branch_unknown",
            "side_effect_unknown",
            "value_unknown",
        }
        for child in self._iter_arithmetic_proof_vars(expr):
            reason = unknowns.get(child.name)
            if reason in blocked:
                loop_reason = (loop_reasons or {}).get(child.name)
                if loop_reason in preferred_loop_reasons:
                    continue
                return reason
        return None

    def _iter_arithmetic_proof_vars(self, expr: A.ASTNode):
        if isinstance(expr, A.Variable):
            yield expr
            return
        if isinstance(expr, A.BinaryOp):
            yield from self._iter_arithmetic_proof_vars(expr.left)
            yield from self._iter_arithmetic_proof_vars(expr.right)
            return
        if isinstance(expr, A.UnaryOp):
            yield from self._iter_arithmetic_proof_vars(expr.operand)
            return
        if isinstance(expr, A.Cast):
            yield from self._iter_arithmetic_proof_vars(expr.expr)
            return
        if isinstance(expr, A.ArrayAccess):
            yield from self._iter_arithmetic_proof_vars(expr.index)
            return

    def can_prove_index_in_bounds(
        self, array_len: int, index_expr: A.ASTNode, func_scope: Optional[str]
    ) -> bool:
        proven, _reason = self.explain_index_in_bounds(
            array_len, index_expr, func_scope
        )
        return proven

    def explain_index_in_bounds(
        self, array_len: int, index_expr: A.ASTNode, func_scope: Optional[str]
    ) -> Tuple[bool, str]:
        if array_len <= 0:
            return False, "array_len_invalid"
        snapshot = self._expr_scope_snapshot(index_expr, func_scope)
        if snapshot is None:
            return False, "point_unknown"
        unknowns = self._expr_unknown_snapshot(index_expr, func_scope)
        arrays = self._expr_array_snapshot(index_expr, func_scope)
        interval, reason = self._expr_interval_with_reason(
            index_expr, func_scope, snapshot, unknowns, arrays
        )
        if interval is None:
            return False, reason if reason != "ok" else "range_unknown"
        if interval.low < 0:
            return False, "index_negative"
        if interval.high >= array_len:
            return False, "index_out_of_bounds"
        return True, "range_proven"

    def can_prove_range_assignment(
        self,
        value_expr: A.ASTNode,
        target_range: tuple[int, int, bool],
        func_scope: Optional[str],
    ) -> bool:
        """Prove value fits declared range.

        target_range is (low, high, exclusive_high).
        """
        snapshot = self._expr_scope_snapshot(value_expr, func_scope)
        interval = self._expr_interval(value_expr, func_scope, snapshot)
        if interval is None:
            return False
        low, high, exclusive = target_range
        max_allowed = high - 1 if exclusive else high
        return interval.low >= low and interval.high <= max_allowed

    def _expr_interval(
        self,
        expr: A.ASTNode,
        func_scope: Optional[str],
        expr_scope: Optional[Dict[str, Interval]] = None,
    ) -> Optional[Interval]:
        relations = (
            self._expr_relation_snapshot(expr, func_scope)
            if expr_scope is not None
            else None
        )
        val, _reason = self._expr_interval_with_reason(
            expr, func_scope, expr_scope, relation_scope=relations
        )
        return val

    def _expr_interval_with_reason(
        self,
        expr: A.ASTNode,
        func_scope: Optional[str],
        expr_scope: Optional[Dict[str, Interval]] = None,
        unknown_scope: Optional[Dict[str, str]] = None,
        array_scope: Optional[Dict[str, Tuple[Interval, int]]] = None,
        loop_scope: Optional[Dict[str, str]] = None,
        relation_scope: Optional[Set[Tuple[str, str, str]]] = None,
    ) -> Tuple[Optional[Interval], str]:
        if relation_scope is None and expr_scope is not None:
            relation_scope = self.scope_relations.get(func_scope)
        if isinstance(expr, A.Number) and isinstance(expr.value, int):
            return Interval(expr.value, expr.value), "ok"
        if isinstance(expr, A.Variable):
            scoped = self._range_from_scope(expr.name, expr_scope)
            if scoped is not None:
                loop_reason = self._unknown_from_scope(expr.name, loop_scope)
                if loop_reason:
                    return scoped, loop_reason
                return scoped, "ok"
            # For point-in-time proofs, never fall back to final scope state.
            if expr_scope is not None:
                unknown = self._unknown_from_scope(expr.name, unknown_scope)
                if unknown:
                    return None, unknown
                return None, "range_unknown"
            known = self.get_var_range(expr.name, func_scope)
            if known is not None:
                return known, "ok"
            unknown = self._unknown_from_scope(expr.name, unknown_scope)
            if unknown:
                return None, unknown
            unknown = self.unknown_reasons.get((func_scope, expr.name))
            if unknown:
                return None, unknown
            return None, "range_unknown"
        if isinstance(expr, A.UnaryOp):
            val, reason = self._expr_interval_with_reason(
                expr.operand,
                func_scope,
                expr_scope,
                unknown_scope,
                array_scope,
                loop_scope,
                relation_scope,
            )
            if val is None:
                return None, reason
            if expr.op == "-":
                return Interval(-val.high, -val.low), "ok"
            if expr.op == "+":
                return val, "ok"
            return None, "op_unsupported"
        if isinstance(expr, A.BinaryOp):
            if expr.op in {"%", "mod"}:
                right, right_reason = self._expr_interval_with_reason(
                    expr.right,
                    func_scope,
                    expr_scope,
                    unknown_scope,
                    array_scope,
                    loop_scope,
                    relation_scope,
                )
                if right is None:
                    return None, (
                        right_reason if right_reason != "ok" else "range_unknown"
                    )
                if right.low == right.high and right.low > 0:
                    left, left_reason = self._expr_interval_with_reason(
                        expr.left,
                        func_scope,
                        expr_scope,
                        unknown_scope,
                        array_scope,
                        loop_scope,
                        relation_scope,
                    )
                    if left is not None and left.low >= 0:
                        return Interval(0, right.low - 1), left_reason
                return None, "range_unknown"
            left, left_reason = self._expr_interval_with_reason(
                expr.left,
                func_scope,
                expr_scope,
                unknown_scope,
                array_scope,
                loop_scope,
                relation_scope,
            )
            right, right_reason = self._expr_interval_with_reason(
                expr.right,
                func_scope,
                expr_scope,
                unknown_scope,
                array_scope,
                loop_scope,
                relation_scope,
            )
            if left is None or right is None:
                if left is None and left_reason != "ok":
                    return None, left_reason
                if right is None and right_reason != "ok":
                    return None, right_reason
                return None, "range_unknown"
            out = self._binary_interval(expr.op, left, right)
            if (
                out is not None
                and expr.op == "-"
                and isinstance(expr.left, A.Variable)
                and isinstance(expr.right, A.Variable)
                and self._relation_proves_ge(
                    expr.left.name, expr.right.name, relation_scope
                )
            ):
                out = Interval(max(0, out.low), out.high)
            if (
                out is not None
                and out.low < 0
                and self._additive_nonnegative_by_relation(
                    expr,
                    func_scope,
                    expr_scope,
                    unknown_scope,
                    array_scope,
                    loop_scope,
                    relation_scope,
                )
            ):
                out = Interval(0, out.high)
            if out is None:
                return None, "op_unsupported"
            for preferred in (
                "protocol_parser_proven",
                "protocol_modulo_proven",
                "loop_accumulator_proven",
                "loop_counter_proven",
                "loop_guard_proven",
            ):
                if left_reason == preferred or right_reason == preferred:
                    return out, preferred
            return out, "ok"
        if isinstance(expr, A.Cast):
            return self._expr_interval_with_reason(
                expr.expr,
                func_scope,
                expr_scope,
                unknown_scope,
                array_scope,
                loop_scope,
                relation_scope,
            )
        if isinstance(expr, A.ArrayAccess):
            if not isinstance(expr.array, A.Variable):
                return None, "array_unknown"
            if isinstance(expr.index, A.StringLit):
                dicts = (
                    self._expr_dict_value_snapshot(expr, func_scope)
                    if expr_scope is not None
                    else None
                )
                values = dicts.get(expr.array.name) if dicts is not None else None
                if values is not None and expr.index.value in values:
                    return values[expr.index.value], "ok"
                value = self.get_dict_value_info(
                    expr.array.name, expr.index.value, func_scope
                )
                if value is not None:
                    return value, "ok"
                return None, "dict_unknown"
            idx, idx_reason = self._expr_interval_with_reason(
                expr.index,
                func_scope,
                expr_scope,
                unknown_scope,
                array_scope,
                loop_scope,
                relation_scope,
            )
            if idx is None:
                return None, idx_reason if idx_reason != "ok" else "range_unknown"
            arr_meta: Optional[Tuple[Interval, int]]
            if array_scope is not None:
                arr_meta = array_scope.get(expr.array.name)
                if arr_meta is None:
                    global_arrays = self.array_infos.get(None, {})
                    arr_meta = global_arrays.get(expr.array.name)
            else:
                arr_meta = self.get_array_info(expr.array.name, func_scope)
            if arr_meta is None:
                return None, "array_unknown"
            elem_rng, arr_len = arr_meta
            if idx.low < 0:
                return None, "index_negative"
            if idx.high >= arr_len:
                return None, "index_out_of_bounds"
            if idx_reason in {
                "loop_accumulator_proven",
                "loop_counter_proven",
                "loop_guard_proven",
            }:
                return elem_rng, idx_reason
            return elem_rng, "ok"
        if isinstance(expr, A.DictAccess):
            if not isinstance(expr.dict_expr, A.Variable):
                return None, "dict_unknown"
            if not isinstance(expr.key_expr, A.StringLit):
                return None, "dict_key_unknown"
            dicts = (
                self._expr_dict_value_snapshot(expr, func_scope)
                if expr_scope is not None
                else None
            )
            values = dicts.get(expr.dict_expr.name) if dicts is not None else None
            if values is not None and expr.key_expr.value in values:
                return values[expr.key_expr.value], "ok"
            value = self.get_dict_value_info(
                expr.dict_expr.name, expr.key_expr.value, func_scope
            )
            if value is not None:
                return value, "ok"
            return None, "dict_unknown"
        if isinstance(expr, A.Call):
            if expr.name in self.function_return_ranges:
                return self.function_return_ranges[expr.name], "call_return_proven"
            if expr.name in {"char_at", "unsafe_char_at"}:
                return Interval(0, 255), "ok"
            if expr.name in {"strlen", "len"} and len(expr.args or []) == 1:
                arg = arg_at(expr, 0)
                if isinstance(arg, A.StringLit):
                    return Interval(len(arg.value), len(arg.value)), "ok"
                if isinstance(arg, A.Variable):
                    strings = (
                        self._expr_string_snapshot(expr, func_scope)
                        if expr_scope is not None
                        else None
                    )
                    info = None
                    if strings is not None:
                        info = strings.get(arg.name)
                    if info is None:
                        info = self.get_string_info(func_scope, arg.name)
                    if info is not None:
                        return Interval(info.min_len, info.max_len), "ok"
        return None, "expr_unsupported"

    @staticmethod
    def _binary_interval(
        op: str, left: Interval, right: Interval
    ) -> Optional[Interval]:
        if op == "+":
            return Interval(left.low + right.low, left.high + right.high)
        if op == "-":
            return Interval(left.low - right.high, left.high - right.low)
        if op == "*":
            products = (
                left.low * right.low,
                left.low * right.high,
                left.high * right.low,
                left.high * right.high,
            )
            return Interval(min(products), max(products))
        return None

    @staticmethod
    def _relation_proves_ge(
        left: str, right: str, relations: Optional[Set[Tuple[str, str, str]]]
    ) -> bool:
        if left == right:
            return True
        if not relations:
            return False
        return (
            (left, ">", right) in relations
            or (left, ">=", right) in relations
            or (left, "==", right) in relations
            or (right, "<", left) in relations
            or (right, "<=", left) in relations
            or (right, "==", left) in relations
        )

    def _additive_nonnegative_by_relation(
        self,
        expr: A.ASTNode,
        func_scope: Optional[str],
        expr_scope: Optional[Dict[str, Interval]],
        unknown_scope: Optional[Dict[str, str]],
        array_scope: Optional[Dict[str, Tuple[Interval, int]]],
        loop_scope: Optional[Dict[str, str]],
        relation_scope: Optional[Set[Tuple[str, str, str]]],
    ) -> bool:
        positives, negatives = self._linear_terms(expr)
        if not negatives:
            return False
        unmatched = list(positives)
        for neg in negatives:
            if not isinstance(neg, A.Variable):
                return False
            match_index: Optional[int] = None
            for idx, pos in enumerate(unmatched):
                if isinstance(pos, A.Variable) and self._relation_proves_ge(
                    pos.name, neg.name, relation_scope
                ):
                    match_index = idx
                    break
            if match_index is None:
                return False
            unmatched.pop(match_index)
        for pos in unmatched:
            if isinstance(pos, A.Number) and isinstance(pos.value, int):
                if int(pos.value) < 0:
                    return False
                continue
            interval, _reason = self._expr_interval_with_reason(
                pos,
                func_scope,
                expr_scope,
                unknown_scope,
                array_scope,
                loop_scope,
                relation_scope,
            )
            if interval is None or interval.low < 0:
                return False
        return True

    @classmethod
    def _linear_terms(cls, expr: A.ASTNode) -> Tuple[List[A.ASTNode], List[A.ASTNode]]:
        if isinstance(expr, A.BinaryOp) and expr.op == "+":
            left_pos, left_neg = cls._linear_terms(expr.left)
            right_pos, right_neg = cls._linear_terms(expr.right)
            return left_pos + right_pos, left_neg + right_neg
        if isinstance(expr, A.BinaryOp) and expr.op == "-":
            left_pos, left_neg = cls._linear_terms(expr.left)
            right_pos, right_neg = cls._linear_terms(expr.right)
            return left_pos + right_neg, left_neg + right_pos
        return [expr], []

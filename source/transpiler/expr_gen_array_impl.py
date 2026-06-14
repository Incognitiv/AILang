"""Array/slice index emission helpers for CExprEmitter."""

from __future__ import annotations

from parser import ast as A
from typing import Optional

from transpiler.arithmetic_literal_proofs import int_literal_in_range


def _expr_array_access(self, node: A.ArrayAccess) -> str:
    """Generate C code for array access with bounds checking."""
    arr = self.expr(node.array)
    idx = self.expr(node.index)
    # Check if the array is a dict
    if (
        isinstance(node.array, A.Variable)
        and hasattr(self, "_dict_vars")
        and node.array.name in self._dict_vars
    ):
        return f"dict_get({arr}, {idx})"
    is_unsafe = getattr(node, "unsafe", False)
    # Dynamic array (from list comprehension or array_new)
    if (
        isinstance(node.array, A.Variable)
        and hasattr(self, "_dyn_array_vars")
        and node.array.name in self._dyn_array_vars
    ):
        if is_unsafe:
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="[]",
                decision="elided",
                reason="unsafe_explicit",
            )
            return f"{arr}.data[{idx}]"
        can_elide, reason = self._can_elide_index_safety(node)
        if can_elide:
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="[]",
                decision="elided",
                reason=reason,
            )
            return f"{arr}.data[{idx}]"
        self._record_check_decision(
            node,
            check_kind="bounds",
            operation="[]",
            decision="inserted",
            reason=reason,
        )
        self.used_helpers.add("safe_array")
        return f"ailang_safe_array_get({arr}.data, {idx}, {arr}.length)"
    # Declared slice/view types use the same runtime shape as dyn arrays.
    if isinstance(node.array, A.Variable) and hasattr(self, "_var_types"):
        vtype = self._var_types.get(node.array.name, "")
        if isinstance(vtype, str) and (
            vtype.startswith("slice[") or vtype.startswith("view[")
        ):
            if is_unsafe:
                self._record_check_decision(
                    node,
                    check_kind="bounds",
                    operation="[]",
                    decision="elided",
                    reason="unsafe_explicit",
                )
                return f"{arr}.data[{idx}]"
            can_elide, reason = self._can_elide_index_safety(node)
            if can_elide:
                self._record_check_decision(
                    node,
                    check_kind="bounds",
                    operation="[]",
                    decision="elided",
                    reason=reason,
                )
                return f"{arr}.data[{idx}]"
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="[]",
                decision="inserted",
                reason=reason,
            )
            self.used_helpers.add("safe_array")
            return f"ailang_safe_array_get({arr}.data, {idx}, {arr}.length)"
    # ailang_array struct
    if isinstance(node.array, A.Variable) and node.array.name in self._array_vars:
        if is_unsafe:
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="[]",
                decision="elided",
                reason="unsafe_explicit",
            )
            return f"{arr}.data[{idx}]"
        can_elide, reason = self._can_elide_index_safety(node)
        if can_elide:
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="[]",
                decision="elided",
                reason=reason,
            )
            return f"{arr}.data[{idx}]"
        self._record_check_decision(
            node,
            check_kind="bounds",
            operation="[]",
            decision="inserted",
            reason=reason,
        )
        self.used_helpers.add("safe_array")
        return f"ailang_safe_array_get({arr}.data, {idx}, {arr}.length)"
    # Chained access like nums.data[i]
    if isinstance(node.array, A.FieldAccess):
        return f"{arr}[{idx}]"
    # Raw array
    return f"{arr}[{idx}]"


def _known_array_len_hint(self, array_expr: A.ASTNode) -> Optional[int]:
    if not isinstance(array_expr, A.Variable):
        return None
    var_name = array_expr.name
    hints = getattr(self, "_array_len_hints", {})
    if isinstance(hints, dict):
        scoped_key = (self.current_function, var_name)
        if scoped_key in hints:
            return int(hints[scoped_key])
        global_key = (None, var_name)
        if global_key in hints:
            return int(hints[global_key])
    if hasattr(self, "_var_types"):
        atype = self._var_types.get(var_name, "")
        if isinstance(atype, str) and hasattr(self, "_parse_fixed_array_type_spec"):
            parsed = self._parse_fixed_array_type_spec(atype)
            if parsed is not None:
                _elem_type, size = parsed
                return int(size)
    return None


def _can_elide_index_safety(self, node: A.ArrayAccess) -> tuple[bool, str]:
    array_len = self._known_array_len_hint(node.array)
    if array_len is None:
        return False, "array_len_unknown"
    if int_literal_in_range(node.index, 0, array_len):
        return True, "literal_index_in_bounds"
    facts = getattr(self, "range_facts", None)
    if facts is None:
        return False, "facts_missing"
    if hasattr(facts, "explain_index_in_bounds"):
        proven, reason = facts.explain_index_in_bounds(
            array_len, node.index, self.current_function
        )
        return bool(proven), str(reason)
    proven = facts.can_prove_index_in_bounds(
        array_len, node.index, self.current_function
    )
    return bool(proven), ("range_proven" if proven else "range_unknown")

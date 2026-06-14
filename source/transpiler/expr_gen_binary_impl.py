from __future__ import annotations

from parser import ast as A

from transpiler.arithmetic_literal_proofs import (
    literal_int_arithmetic_safe,
    neutral_int_arithmetic_safe,
    positive_int_literal,
    shift_amount_literal_in_range,
)


def _expr_binary_op(self, node: A.BinaryOp) -> str:
    """Generate C code for binary operations."""
    fused_lit_i64 = self._emit_lit_i64_concat(node)
    if fused_lit_i64 is not None:
        return fused_lit_i64
    left = self.expr(node.left)
    right = self.expr(node.right)
    op = node.op
    # Logical operators
    if op == "and":
        return f"({left} && {right})"
    if op == "or":
        return f"({left} || {right})"
    # Bitwise operators
    if op == "AND":
        return f"({left} & {right})"
    if op == "OR":
        return f"({left} | {right})"
    if op == "XOR":
        return f"({left} ^ {right})"
    if op == "NAND":
        return f"(~({left} & {right}))"
    if op == "NOR":
        return f"(~({left} | {right}))"
    if op == "XNOR":
        return f"(~({left} ^ {right}))"
    # Shift operators
    if op in ("<<", "shl"):
        if not self._unchecked_mode:
            if shift_amount_literal_in_range(node.right, 64):
                return f"({left} << {right})"
            self.used_helpers.add("safe_shift")
            return f"ailang_safe_shl({left}, {right})"
        return f"({left} << {right})"
    if op in (">>", "shr"):
        if not self._unchecked_mode:
            if shift_amount_literal_in_range(node.right, 64):
                return f"({left} >> {right})"
            self.used_helpers.add("safe_shift")
            return f"ailang_safe_shr({left}, {right})"
        return f"({left} >> {right})"
    if op == "ushr":
        if not self._unchecked_mode:
            if shift_amount_literal_in_range(node.right, 64):
                return f"((int64_t)((uint64_t){left} >> {right}))"
            self.used_helpers.add("safe_shift")
            return f"((int64_t)((uint64_t)ailang_safe_shr({left}, {right})))"
        return f"((int64_t)((uint64_t){left} >> {right}))"
    # Division
    if op in ("/", "//"):
        if not self._unchecked_mode:
            if positive_int_literal(node.right):
                self._record_check_decision(
                    node,
                    check_kind="division",
                    operation=op,
                    decision="elided",
                    reason="positive_literal_divisor",
                )
                return f"({left} / {right})"
            self._record_check_decision(
                node,
                check_kind="division",
                operation=op,
                decision="inserted",
                reason="division_safety_unknown",
            )
            self.used_helpers.add("safe_div")
            return f"ailang_safe_div({left}, {right})"
        return f"({left} / {right})"
    # Power
    if op in ("**", "^"):
        self.used_helpers.add("math")
        return f"pow((double)({left}), (double)({right}))"
    # String concatenation. For `+`-chains of length 3+, emit a
    # single ailang_strcat_n call that does ONE allocation and one
    # strlen per operand instead of nested O(nÂ²) strcat. For pairs
    # we keep the consuming-strcat path (compiler is smart enough
    # to inline). Massive win in adapt_serve's response builders.
    if op == "+" and (
        self._might_be_string(node.left) or self._might_be_string(node.right)
    ):
        chain = self._flatten_string_concat(node)
        if chain is not None and len(chain) >= 3:
            return self._emit_strcat_n(chain)
        left_owned = self._is_owned_string_alloc(node.left)
        right_owned = self._is_owned_string_alloc(node.right)
        if left_owned or right_owned:
            return (
                f"ailang_strcat_consuming({left}, {right}, "
                f"{1 if left_owned else 0}, {1 if right_owned else 0})"
            )
        return f"ailang_strcat({left}, {right})"
    # Safe integer arithmetic (skip in unchecked mode)
    if not self._unchecked_mode:
        can_elide, reason = self._binary_safety_decision(node, self.current_function)
        literal_reason = neutral_int_arithmetic_safe(node)
        if literal_reason is None:
            literal_reason = literal_int_arithmetic_safe(
                node,
                bit_width=64,
                is_unsigned=False,
            )
        if literal_reason is not None:
            can_elide = True
            reason = literal_reason
        if op == "+" and not self._might_be_string(node.left):
            if can_elide:
                self._record_check_decision(
                    node,
                    check_kind="overflow",
                    operation="+",
                    decision="elided",
                    reason=reason,
                )
                return f"({left} + {right})"
            self._record_check_decision(
                node,
                check_kind="overflow",
                operation="+",
                decision="inserted",
                reason=reason,
            )
            self.used_helpers.add("safe_add")
            return f"ailang_safe_add({left}, {right})"
        if op == "-":
            if can_elide:
                self._record_check_decision(
                    node,
                    check_kind="overflow",
                    operation="-",
                    decision="elided",
                    reason=reason,
                )
                return f"({left} - {right})"
            self._record_check_decision(
                node,
                check_kind="overflow",
                operation="-",
                decision="inserted",
                reason=reason,
            )
            self.used_helpers.add("safe_sub")
            return f"ailang_safe_sub({left}, {right})"
        if op == "*":
            if can_elide:
                self._record_check_decision(
                    node,
                    check_kind="overflow",
                    operation="*",
                    decision="elided",
                    reason=reason,
                )
                return f"({left} * {right})"
            self._record_check_decision(
                node,
                check_kind="overflow",
                operation="*",
                decision="inserted",
                reason=reason,
            )
            self.used_helpers.add("safe_mul")
            return f"ailang_safe_mul({left}, {right})"
        if op == "%":
            if positive_int_literal(node.right):
                self._record_check_decision(
                    node,
                    check_kind="modulo",
                    operation="%",
                    decision="elided",
                    reason="positive_literal_divisor",
                )
                return f"({left} % {right})"
            can_elide_mod, mod_reason = self._modulo_safety_decision(
                node, self.current_function
            )
            if can_elide_mod:
                self._record_check_decision(
                    node,
                    check_kind="modulo",
                    operation="%",
                    decision="elided",
                    reason=mod_reason,
                )
                return f"({left} % {right})"
            self._record_check_decision(
                node,
                check_kind="modulo",
                operation="%",
                decision="inserted",
                reason=mod_reason,
            )
            self.used_helpers.add("safe_div")
            return f"ailang_safe_mod({left}, {right})"
    # String comparison: use strcmp instead of pointer comparison
    if op in ("==", "!=", "<", ">", "<=", ">=") and (
        self._might_be_string(node.left) or self._might_be_string(node.right)
    ):
        self.used_helpers.add("string")
        return f"(strcmp({left}, {right}) {op} 0)"
    return f"({left} {op} {right})"

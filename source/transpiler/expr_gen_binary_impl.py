from __future__ import annotations

from parser import ast as A

from transpiler.arithmetic_literal_proofs import (
    literal_int_arithmetic_safe,
    neutral_int_arithmetic_safe,
    positive_int_literal,
    shift_amount_literal_in_range,
)
from transpiler.codegen_int_ranges import (
    expr_int_range,
    range_fits_int64,
    range_fits_signed_width,
    range_is_positive,
)
from transpiler.wide_int_types import info_for_c, promoted_info
from transpiler.c_bigint import expr_is_unbounded, owned_bigint_expr
from transpiler.fixed_int_types import (
    c_name_for_fixed,
    info_for_c_fixed,
    promoted_fixed_info,
)


def _literal_fits_fixed(node: A.ASTNode, info) -> bool:
    if not isinstance(node, A.Number) or isinstance(node.value, float):
        return False
    value = int(node.value)
    if info.unsigned:
        return 0 <= value <= (1 << info.bits) - 1
    return -(1 << (info.bits - 1)) <= value <= (1 << (info.bits - 1)) - 1


def _fixed_binary_info(self, node: A.BinaryOp):
    li = info_for_c_fixed(self._infer_type(node.left))
    ri = info_for_c_fixed(self._infer_type(node.right))
    if li is not None and _literal_fits_fixed(node.right, li):
        return li
    if ri is not None and _literal_fits_fixed(node.left, ri):
        return ri
    return promoted_fixed_info(li, ri)


def _fixed_arithmetic_is_proven(self, node: A.BinaryOp, info) -> bool:
    """True when fixed-width + / - / * cannot overflow its real type."""
    if node.op not in {"+", "-", "*"}:
        return False
    facts = getattr(self, "range_facts", None)
    if facts is not None and facts.can_prove_no_overflow_for_int(
        node,
        self.current_function,
        bit_width=info.bits,
        is_unsigned=info.unsigned,
    ):
        return True
    rng = expr_int_range(self, node)
    if rng is None:
        return False
    if info.unsigned:
        return 0 <= rng[0] and rng[1] <= (1 << info.bits) - 1
    return range_fits_signed_width(rng, info.bits)


def _fixed_binary_expr(self, node: A.BinaryOp, left: str, right: str):
    """Lower <=128-bit fixed integer expressions with their real C type.

    The historical generic path coerced everything through signed int64_t,
    turning valid u64 values above INT64_MAX into failures.
    """
    op = node.op
    li = info_for_c_fixed(self._infer_type(node.left))
    info = _fixed_binary_info(self, node)
    if info is None or info.bits > 128:
        return None

    # Shifts and integer power preserve the base/left fixed type; the right
    # operand is a count, not a value participating in numeric promotion.
    if op in ("<<", "shl", ">>", "shr", "ushr", "**", "^") and li is not None:
        info = li
    ctype = c_name_for_fixed(info)
    suffix = info.canonical
    l = f"(({ctype})({left}))"
    r = f"(({ctype})({right}))"

    if op == "and":
        return f"(({l}) && ({r}))"
    if op == "or":
        return f"(({l}) || ({r}))"
    if op in ("AND", "OR", "XOR", "NAND", "NOR", "XNOR", "&", "|", "bxor"):
        mapped = {"AND":"&", "OR":"|", "XOR":"^", "&":"&", "|":"|", "bxor":"^"}
        c_op = mapped.get(op)
        if c_op is not None:
            return f"({l} {c_op} {r})"
        inner = "&" if op == "NAND" else ("|" if op == "NOR" else "^")
        return f"(~({l} {inner} {r}))"
    if op in ("==", "!=", "<", ">", "<=", ">="):
        return f"({l} {op} {r})"
    if op in ("<<", "shl"):
        return f"({l} << (int64_t)({right}))" if self._unchecked_mode else f"ailang_safe_shl_{suffix}({l}, (int64_t)({right}))"
    if op in (">>", "shr", "ushr"):
        if op == "ushr" and not info.unsigned:
            uctype = c_name_for_fixed(type(info)(info.bits, True, f"u{info.bits}"))
            shifted = f"(({uctype})({l}) >> (int64_t)({right}))" if self._unchecked_mode else f"ailang_safe_shr_u{info.bits}(({uctype})({l}), (int64_t)({right}))"
            return f"(({ctype})({shifted}))"
        return f"({l} >> (int64_t)({right}))" if self._unchecked_mode else f"ailang_safe_shr_{suffix}({l}, (int64_t)({right}))"
    fixed_proven = _fixed_arithmetic_is_proven(self, node, info)
    if op in ("+", "plus"):
        return f"({l} + {r})" if self._unchecked_mode or fixed_proven else f"ailang_safe_add_{suffix}({l}, {r})"
    if op in ("-", "minus"):
        return f"({l} - {r})" if self._unchecked_mode or fixed_proven else f"ailang_safe_sub_{suffix}({l}, {r})"
    if op in ("*", "star"):
        return f"({l} * {r})" if self._unchecked_mode or fixed_proven else f"ailang_safe_mul_{suffix}({l}, {r})"
    if op in ("/", "//"):
        return f"({l} / {r})" if self._unchecked_mode else f"ailang_safe_div_{suffix}({l}, {r})"
    if op == "%":
        return f"({l} % {r})" if self._unchecked_mode else f"ailang_safe_mod_{suffix}({l}, {r})"
    if op in ("**", "^"):
        return f"ailang_safe_pow_{suffix}({l}, (int64_t)({right}))"
    return None


def _wide_binary_expr(self, node: A.BinaryOp, left: str, right: str):
    li = info_for_c(self._infer_type(node.left))
    ri = info_for_c(self._infer_type(node.right))
    info = promoted_info(li, ri)
    if info is None:
        return None

    ctype = info.c_name
    suffix = info.suffix
    l = f"(({ctype})({left}))"
    r = f"(({ctype})({right}))"
    op = node.op

    if op == "and":
        return f"(({l}) && ({r}))"
    if op == "or":
        return f"(({l}) || ({r}))"
    if op in ("AND", "OR", "XOR", "NAND", "NOR", "XNOR"):
        c_op = {"AND":"&", "OR":"|", "XOR":"^"}.get(op)
        if c_op is not None:
            return f"({l} {c_op} {r})"
        inner = "&" if op == "NAND" else ("|" if op == "NOR" else "^")
        return f"(~({l} {inner} {r}))"
    if op in ("==", "!=", "<", ">", "<=", ">="):
        return f"({l} {op} {r})"
    if op in ("<<", "shl"):
        if self._unchecked_mode:
            return f"({l} << ({right}))"
        return f"ailang_safe_shl_{suffix}({l}, (int64_t)({right}))"
    if op in (">>", "shr", "ushr"):
        if op == "ushr" and not info.unsigned:
            unsigned_suffix = "u" + str(info.bits)
            unsigned_type = "ailang_" + unsigned_suffix
            shifted = (
                f"(({unsigned_type})({l}) >> (int64_t)({right}))"
                if self._unchecked_mode
                else f"ailang_safe_shr_{unsigned_suffix}(({unsigned_type})({l}), (int64_t)({right}))"
            )
            return f"(({ctype})({shifted}))"
        if self._unchecked_mode:
            return f"({l} >> ({right}))"
        return f"ailang_safe_shr_{suffix}({l}, (int64_t)({right}))"
    if op in ("+", "plus"):
        return f"({l} + {r})" if self._unchecked_mode else f"ailang_safe_add_{suffix}({l}, {r})"
    if op in ("-", "minus"):
        return f"({l} - {r})" if self._unchecked_mode else f"ailang_safe_sub_{suffix}({l}, {r})"
    if op in ("*", "star"):
        return f"({l} * {r})" if self._unchecked_mode else f"ailang_safe_mul_{suffix}({l}, {r})"
    if op in ("/", "//"):
        return f"({l} / {r})" if self._unchecked_mode else f"ailang_safe_div_{suffix}({l}, {r})"
    if op == "%":
        return f"({l} % {r})" if self._unchecked_mode else f"ailang_safe_mod_{suffix}({l}, {r})"
    if op in ("**", "^"):
        if self._unchecked_mode:
            return f"ailang_safe_pow_{suffix}({l}, (int64_t)({right}))"
        return f"ailang_safe_pow_{suffix}({l}, (int64_t)({right}))"
    return None


def _bigint_binary_expr(self, node: A.BinaryOp) -> str:
    op = node.op
    if op == "ushr":
        raise ValueError("logical right shift 'ushr' is undefined for unbounded integers; use >>")
    left = owned_bigint_expr(self, node.left)
    if op in ("**", "^"):
        exponent = owned_bigint_expr(self, node.right)
        return f"ailang_bigint_pow_unbounded_take({left}, {exponent})"
    if op in ("<<", "shl", ">>", "shr"):
        # Shift counts are intentionally machine-bounded. Materialize as BigInt
        # first and narrow through the checked helper so no wide value truncates.
        count = f"ailang_bigint_count_take({owned_bigint_expr(self, node.right)})"
        fn = "shl" if op in ("<<", "shl") else "shr"
        return f"ailang_bigint_{fn}_take({left}, {count})"
    right = owned_bigint_expr(self, node.right)
    if op in ("+", "plus", "-", "minus", "*", "star", "/", "//", "%"):
        fn = {"+":"add", "plus":"add", "-":"sub", "minus":"sub", "*":"mul", "star":"mul", "/":"div", "//":"div", "%":"mod"}[op]
        return f"ailang_bigint_{fn}_take({left}, {right})"
    if op in ("AND", "&", "band", "OR", "|", "bor", "XOR", "bxor"):
        fn = {"AND":"and", "&":"and", "band":"and", "OR":"or", "|":"or", "bor":"or", "XOR":"xor", "bxor":"xor"}[op]
        return f"ailang_bigint_{fn}_take({left}, {right})"
    if op in ("NAND", "NOR", "XNOR"):
        fn = {"NAND":"and", "NOR":"or", "XNOR":"xor"}[op]
        return f"ailang_bigint_not_take(ailang_bigint_{fn}_take({left}, {right}))"
    if op in ("==", "!=", "<", ">", "<=", ">="):
        cmp = f"ailang_bigint_cmp_take({left}, {right})"
        return f"({cmp} {op} 0)"
    raise ValueError(f"operator {op!r} is not supported for unbounded integers")

def _expr_binary_op(self, node: A.BinaryOp) -> str:
    """Generate C code for binary operations."""
    if expr_is_unbounded(self, node.left) or expr_is_unbounded(self, node.right):
        return _bigint_binary_expr(self, node)
    fused_lit_i64 = self._emit_lit_i64_concat(node)
    if fused_lit_i64 is not None:
        return fused_lit_i64
    left = self.expr(node.left)
    right = self.expr(node.right)
    op = node.op
    wide_expr = _wide_binary_expr(self, node, left, right)
    if wide_expr is not None:
        return wide_expr
    fixed_expr = _fixed_binary_expr(self, node, left, right)
    if fixed_expr is not None:
        return fixed_expr
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
            can_elide_div, div_reason = self._division_safety_decision(
                node, self.current_function
            )
            if can_elide_div:
                self._record_check_decision(
                    node,
                    check_kind="division",
                    operation=op,
                    decision="elided",
                    reason=div_reason,
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
        if not can_elide and range_fits_int64(expr_int_range(self, node)):
            can_elide = True
            reason = "codegen_range_proven"
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
            if range_is_positive(expr_int_range(self, node.right)):
                self._record_check_decision(
                    node,
                    check_kind="modulo",
                    operation="%",
                    decision="elided",
                    reason="codegen_positive_divisor",
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
        return f"(__ailang_strcmp_raw({left}, {right}) {op} 0)"
    return f"({left} {op} {right})"

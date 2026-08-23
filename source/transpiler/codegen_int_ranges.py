from __future__ import annotations

from dataclasses import dataclass
from parser import ast as A
from typing import Dict, Iterable, List, Optional, Set, Tuple

from transpiler.array_literal_hints import get_array_literal_values

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
IntRange = Tuple[int, int]
FieldKey = Tuple[str, str]


@dataclass(frozen=True)
class RangeSnapshot:
    int_ranges: Dict[str, IntRange]
    field_ranges: Dict[FieldKey, IntRange]
    fixed_dict_ranges: Dict[str, Dict[str, IntRange]]
    string_length_ranges: Dict[str, IntRange]


def _literal_key(node: A.ASTNode) -> Optional[str]:
    if isinstance(node, A.StringLit):
        return node.value
    return None


def _range_add(left: IntRange, right: IntRange) -> IntRange:
    return (left[0] + right[0], left[1] + right[1])


def _range_sub(left: IntRange, right: IntRange) -> IntRange:
    return (left[0] - right[1], left[1] - right[0])


def _range_mul(left: IntRange, right: IntRange) -> IntRange:
    vals = (
        left[0] * right[0],
        left[0] * right[1],
        left[1] * right[0],
        left[1] * right[1],
    )
    return (min(vals), max(vals))


def range_fits_int64(rng: Optional[IntRange]) -> bool:
    return rng is not None and INT64_MIN <= rng[0] and rng[1] <= INT64_MAX


def range_fits_signed_width(rng: Optional[IntRange], width: int) -> bool:
    if rng is None or width <= 0:
        return False
    low = -(1 << (width - 1))
    high = (1 << (width - 1)) - 1
    return low <= rng[0] and rng[1] <= high


def range_is_positive(rng: Optional[IntRange]) -> bool:
    return rng is not None and rng[0] > 0


def clear_codegen_int_proofs(self) -> None:
    getattr(self, "_codegen_int_ranges", {}).clear()
    getattr(self, "_codegen_field_int_ranges", {}).clear()
    getattr(self, "_codegen_string_length_ranges", {}).clear()


def expr_contains_call(expr: A.ASTNode) -> bool:
    if isinstance(expr, A.Call):
        return True
    if not isinstance(expr, A.ASTNode):
        return False
    return any(_value_contains_call(value) for value in vars(expr).values())


def call_preserves_local_int_proofs(call: A.Call) -> bool:
    """Return true for statement calls that cannot mutate local integer slots."""

    # These calls have observable I/O effects, but their arguments are passed by
    # value and they do not mutate AILang locals. Keeping integer range proofs
    # across them is safe and avoids pessimizing tight print/debug loops.
    return call.name in {
        "bin",
        "hex",
        "len",
        "oct",
        "print",
        "println",
        "putc",
        "str",
        "strlen",
    }


def expr_invalidates_local_int_proofs(expr: A.ASTNode) -> bool:
    if isinstance(expr, A.Call):
        return not call_preserves_local_int_proofs(expr)
    if not isinstance(expr, A.ASTNode):
        return False
    return any(
        _value_invalidates_local_int_proofs(value) for value in vars(expr).values()
    )


def _value_invalidates_local_int_proofs(value: object) -> bool:
    if isinstance(value, A.Call):
        return not call_preserves_local_int_proofs(value)
    if isinstance(value, A.ASTNode):
        return expr_invalidates_local_int_proofs(value)
    if isinstance(value, (list, tuple)):
        return any(_value_invalidates_local_int_proofs(item) for item in value)
    if isinstance(value, dict):
        return any(
            _value_invalidates_local_int_proofs(item)
            for pair in value.items()
            for item in pair
        )
    return False


def _value_contains_call(value: object) -> bool:
    if isinstance(value, A.Call):
        return True
    if isinstance(value, A.ASTNode):
        return expr_contains_call(value)
    if isinstance(value, (list, tuple)):
        return any(_value_contains_call(item) for item in value)
    if isinstance(value, dict):
        return any(
            _value_contains_call(item) for pair in value.items() for item in pair
        )
    return False


def _parse_c_int_literal(text: str) -> Optional[int]:
    value = text.strip()
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    if value.endswith(("LL", "ll")):
        value = value[:-2]
    try:
        return int(value, 10)
    except ValueError:
        return None


def _decimal_len_range(rng: Optional[IntRange]) -> Optional[IntRange]:
    if rng is None:
        return None
    low, high = rng
    if low > high:
        return None
    if low >= 0:
        return (len(str(low)), len(str(high)))
    max_abs = max(abs(low), abs(high))
    return (1, len(str(max_abs)) + 1)


def _single_call_arg(call: A.Call) -> Optional[A.ASTNode]:
    if len(call.args) != 1:
        return None
    for arg in call.args:
        return arg
    return None


def expr_string_length_range(self, expr: A.ASTNode) -> Optional[IntRange]:
    if isinstance(expr, A.Variable):
        return getattr(self, "_codegen_string_length_ranges", {}).get(expr.name)
    if isinstance(expr, A.StringLit):
        length = len(expr.value.encode("utf-8"))
        return (length, length)
    if isinstance(expr, A.Call):
        only_arg = _single_call_arg(expr)
        if only_arg is not None and expr.name == "str":
            return _decimal_len_range(expr_int_range(self, only_arg))
        if only_arg is not None and expr.name in {"len", "strlen"}:
            return expr_string_length_range(self, only_arg)
    if isinstance(expr, A.InterpolatedString):
        total: IntRange = (0, 0)
        for part in expr.parts:
            part_range: Optional[IntRange]
            if isinstance(part, str):
                length = len(part.encode("utf-8"))
                part_range = (length, length)
            elif isinstance(part, A.ASTNode):
                part_range = expr_string_length_range(self, part)
                if part_range is None:
                    part_range = _decimal_len_range(expr_int_range(self, part))
            else:
                return None
            if part_range is None:
                return None
            total = _range_add(total, part_range)
        return total
    if isinstance(expr, A.BinaryOp) and expr.op == "+":
        left = expr_string_length_range(self, expr.left)
        right = expr_string_length_range(self, expr.right)
        if left is not None and right is not None:
            return _range_add(left, right)
    return None


def expr_int_range(self, expr: A.ASTNode) -> Optional[IntRange]:
    if isinstance(expr, A.Number) and isinstance(expr.value, int):
        return (int(expr.value), int(expr.value))
    if isinstance(expr, A.Variable):
        return getattr(self, "_codegen_int_ranges", {}).get(expr.name)
    if isinstance(expr, A.FieldAccess):
        field_key = _field_key(expr)
        if field_key is not None:
            return getattr(self, "_codegen_field_int_ranges", {}).get(field_key)
    if isinstance(expr, A.ArrayAccess):
        if isinstance(expr.array, A.Variable):
            dict_key = _literal_key(expr.index)
            if dict_key is not None:
                return _fixed_dict_range(self, expr.array.name, dict_key)
            return _array_access_range(self, expr.array.name, expr.index)
    if isinstance(expr, A.DictAccess):
        if isinstance(expr.dict_expr, A.Variable):
            dict_key = _literal_key(expr.key_expr)
            return _fixed_dict_range(self, expr.dict_expr.name, dict_key)
    if isinstance(expr, A.Call):
        only_arg = _single_call_arg(expr)
        if only_arg is not None and expr.name in {"len", "strlen"}:
            return expr_string_length_range(self, only_arg)
    if isinstance(expr, A.BinaryOp):
        left = expr_int_range(self, expr.left)
        right = expr_int_range(self, expr.right)
        if left is None or right is None:
            return None
        if expr.op == "+":
            return _range_add(left, right)
        if expr.op == "-":
            return _range_sub(left, right)
        if expr.op == "*":
            return _range_mul(left, right)
        if expr.op in {"%", "mod"} and right[0] > 0 and left[0] >= 0:
            return (0, max(0, right[1] - 1))
    return None


def range_assignment_proven(
    self, expr: A.ASTNode, low_code: str, high_code: str, exclusive: bool
) -> bool:
    low = _parse_c_int_literal(low_code)
    high = _parse_c_int_literal(high_code)
    if low is None or high is None:
        return False
    high_limit = high - 1 if exclusive else high
    rng = expr_int_range(self, expr)
    return rng is not None and low <= rng[0] and rng[1] <= high_limit


def _fixed_dict_range(self, var_name: str, key: Optional[str]) -> Optional[IntRange]:
    if key is None:
        return None
    ranges = getattr(self, "_fixed_dict_value_ranges", {})
    return ranges.get(var_name, {}).get(key)


def _array_access_range(
    self, array_name: str, index_expr: A.ASTNode
) -> Optional[IntRange]:
    index_rng = expr_int_range(self, index_expr)
    values = get_array_literal_values(self, array_name)
    if (
        values is not None
        and index_rng is not None
        and index_rng[0] >= 0
        and index_rng[1] < len(values)
    ):
        selected = values[index_rng[0] : index_rng[1] + 1]
        if selected:
            return min(selected), max(selected)
    facts = getattr(self, "range_facts", None)
    if facts is None or not hasattr(facts, "get_array_info"):
        return None
    func_scope = getattr(self, "current_function", None) or getattr(
        self, "_current_function_name", None
    )
    array_info = facts.get_array_info(array_name, func_scope)
    if array_info is None:
        return None
    elem_interval, array_len = array_info
    if index_rng is None and hasattr(facts, "_expr_interval"):
        interval = facts._expr_interval(
            index_expr,
            func_scope,
            dict(getattr(self, "_codegen_int_ranges", {})),
        )
        index_rng = _interval_to_range(interval)
    if index_rng is None or index_rng[0] < 0 or index_rng[1] >= array_len:
        return None
    if values is not None and index_rng[1] < len(values):
        selected = values[index_rng[0] : index_rng[1] + 1]
        if selected:
            return min(selected), max(selected)
    return _interval_to_range(elem_interval)


def _interval_to_range(interval: object) -> Optional[IntRange]:
    low = getattr(interval, "low", None)
    high = getattr(interval, "high", None)
    if low is None or high is None:
        return None
    return int(low), int(high)


def remember_assign_range(self, var_name: str, expr: A.ASTNode) -> None:
    remember_string_length_range(self, var_name, expr)
    ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
    rng = expr_int_range(self, expr)
    if rng is None:
        ranges.pop(var_name, None)
    else:
        ranges[var_name] = rng
    self._codegen_int_ranges = ranges
    _clear_field_ranges_for_var(self, var_name)
    _remember_record_init_ranges(self, var_name, expr)


def remember_string_length_range(self, var_name: str, expr: A.ASTNode | None) -> None:
    ranges: Dict[str, IntRange] = getattr(self, "_codegen_string_length_ranges", {})
    rng = expr_string_length_range(self, expr) if expr is not None else None
    if rng is None:
        ranges.pop(var_name, None)
    else:
        ranges[var_name] = rng
    self._codegen_string_length_ranges = ranges


def remember_field_assign_range(
    self, object_expr: A.ASTNode, field_name: str, expr: A.ASTNode
) -> None:
    if not isinstance(object_expr, A.Variable):
        return
    ranges: Dict[FieldKey, IntRange] = getattr(self, "_codegen_field_int_ranges", {})
    key = (object_expr.name, field_name)
    rng = expr_int_range(self, expr)
    if rng is None:
        ranges.pop(key, None)
    else:
        ranges[key] = rng
    self._codegen_field_int_ranges = ranges


def remember_fixed_dict_range(self, dict_name: str, key: str, expr: A.ASTNode) -> None:
    rng = expr_int_range(self, expr)
    ranges = getattr(self, "_fixed_dict_value_ranges", {})
    dict_ranges = dict(ranges.get(dict_name, {}))
    if rng is None:
        dict_ranges.pop(key, None)
    else:
        dict_ranges[key] = rng
    if dict_ranges:
        ranges[dict_name] = dict_ranges
    else:
        ranges.pop(dict_name, None)
    self._fixed_dict_value_ranges = ranges


def clear_loop_variant_ranges(self, body: List[A.ASTNode]) -> None:
    _clear_loop_variant_ranges(self, body, set())


def prepare_while_loop_ranges(self, node: A.While) -> None:
    from transpiler.codegen_loop_range_proofs import derive_counted_loop_ranges

    preserved = derive_counted_loop_ranges(self, node)
    _clear_loop_variant_ranges(self, node.body, preserved)


def snapshot_codegen_ranges(self) -> RangeSnapshot:
    fixed = getattr(self, "_fixed_dict_value_ranges", {})
    return RangeSnapshot(
        int_ranges=dict(getattr(self, "_codegen_int_ranges", {})),
        field_ranges=dict(getattr(self, "_codegen_field_int_ranges", {})),
        fixed_dict_ranges={name: dict(ranges) for name, ranges in fixed.items()},
        string_length_ranges=dict(getattr(self, "_codegen_string_length_ranges", {})),
    )


def restore_codegen_ranges(self, snapshot: RangeSnapshot) -> None:
    self._codegen_int_ranges = dict(snapshot.int_ranges)
    self._codegen_field_int_ranges = dict(snapshot.field_ranges)
    self._fixed_dict_value_ranges = {
        name: dict(ranges) for name, ranges in snapshot.fixed_dict_ranges.items()
    }
    self._codegen_string_length_ranges = dict(snapshot.string_length_ranges)


def merge_codegen_ranges(
    self, left: RangeSnapshot, right: RangeSnapshot, *, source_if: Optional[A.If] = None
) -> None:
    self._codegen_int_ranges = _join_range_map(left.int_ranges, right.int_ranges)
    self._codegen_field_int_ranges = _join_range_map(
        left.field_ranges, right.field_ranges
    )
    self._fixed_dict_value_ranges = _join_fixed_dict_ranges(
        left.fixed_dict_ranges, right.fixed_dict_ranges
    )
    self._codegen_string_length_ranges = _join_range_map(
        left.string_length_ranges, right.string_length_ranges
    )
    if source_if is not None:
        _apply_post_if_refinements(self, source_if, left)


def _join_range_map(left: Dict, right: Dict) -> Dict:
    joined = {}
    for key, left_range in left.items():
        right_range = right.get(key)
        if right_range is None:
            continue
        joined[key] = (
            min(left_range[0], right_range[0]),
            max(left_range[1], right_range[1]),
        )
    return joined


def _join_fixed_dict_ranges(
    left: Dict[str, Dict[str, IntRange]], right: Dict[str, Dict[str, IntRange]]
) -> Dict[str, Dict[str, IntRange]]:
    joined: Dict[str, Dict[str, IntRange]] = {}
    for dict_name, left_values in left.items():
        right_values = right.get(dict_name)
        if right_values is None:
            continue
        value_ranges = _join_range_map(left_values, right_values)
        if value_ranges:
            joined[dict_name] = value_ranges
    return joined


def _apply_post_if_refinements(
    self, node: A.If, before_snapshot: RangeSnapshot
) -> None:
    clamp = _clamp_if_pattern(node)
    if clamp is not None:
        var_name, limit = clamp
        before_range = before_snapshot.int_ranges.get(var_name)
        if (
            before_range is not None
            and before_range[0] >= 0
            and before_range[1] <= (limit * 2)
        ):
            ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
            ranges[var_name] = (0, limit)
            self._codegen_int_ranges = ranges
    _apply_exiting_guard_refinement(self, node, before_snapshot)


def _apply_exiting_guard_refinement(
    self, node: A.If, before_snapshot: RangeSnapshot
) -> None:
    if node.else_body or getattr(node, "elsif_branches", None):
        return
    if not _body_exits_current_path(node.then_body):
        return
    refinement = _false_eq_refinement(self, node.cond, before_snapshot.int_ranges)
    if refinement is None:
        return
    var_name, refined = refinement
    ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
    ranges[var_name] = refined
    self._codegen_int_ranges = ranges


def _body_exits_current_path(body: List[A.ASTNode]) -> bool:
    if not body:
        return False
    return isinstance(body[-1], (A.Break, A.Continue, A.Return, A.Throw))


def _false_eq_refinement(
    self, cond: A.ASTNode, before_ranges: Dict[str, IntRange]
) -> Optional[Tuple[str, IntRange]]:
    if not isinstance(cond, A.BinaryOp) or cond.op != "==":
        return None
    left = cond.left
    right = cond.right
    if isinstance(left, A.Variable) and isinstance(right, A.Number):
        var_name = left.name
        value = right.value
    elif isinstance(right, A.Variable) and isinstance(left, A.Number):
        var_name = right.name
        value = left.value
    else:
        return None
    if not isinstance(value, int):
        return None
    current = before_ranges.get(var_name)
    if current is None:
        current = _declared_range_var_bounds(self, var_name)
    if current is None:
        return None
    low, high = current
    if value == high and low <= value:
        return var_name, (low, high - 1)
    if value == low and value <= high:
        return var_name, (low + 1, high)
    return None


def _declared_range_var_bounds(self, var_name: str) -> Optional[IntRange]:
    range_vars = getattr(self, "_range_vars", {})
    target = range_vars.get(var_name)
    if target is None:
        return None
    low_code, high_code, exclusive = target
    low = _parse_c_int_literal(low_code)
    high = _parse_c_int_literal(high_code)
    if low is None or high is None:
        return None
    return (low, high - 1 if exclusive else high)


def _clamp_if_pattern(node: A.ASTNode) -> Optional[Tuple[str, int]]:
    if not isinstance(node, A.If) or node.else_body:
        return None
    if getattr(node, "elsif_branches", None):
        return None
    if len(node.then_body) != 1 or not isinstance(node.then_body[0], A.Assign):
        return None
    cond = node.cond
    assign = node.then_body[0]
    value = assign.value
    if not (
        isinstance(cond, A.BinaryOp)
        and cond.op == ">"
        and isinstance(cond.left, A.Variable)
        and isinstance(cond.right, A.Number)
        and isinstance(cond.right.value, int)
    ):
        return None
    if assign.var_name != cond.left.name:
        return None
    if not (
        isinstance(value, A.BinaryOp)
        and value.op == "-"
        and isinstance(value.left, A.Variable)
        and value.left.name == assign.var_name
        and isinstance(value.right, A.Number)
        and isinstance(value.right.value, int)
    ):
        return None
    limit = int(cond.right.value)
    if limit <= 0 or int(value.right.value) != limit:
        return None
    return assign.var_name, limit


def _clear_loop_variant_ranges(
    self, body: List[A.ASTNode], preserved: Set[str]
) -> None:
    assigned = _assigned_vars(body)
    ranges = getattr(self, "_codegen_int_ranges", {})
    for name in assigned:
        if name in preserved:
            continue
        ranges.pop(name, None)
    self._codegen_int_ranges = ranges
    string_lengths = getattr(self, "_codegen_string_length_ranges", {})
    for name in assigned:
        string_lengths.pop(name, None)
    self._codegen_string_length_ranges = string_lengths
    _merge_loop_field_ranges(self, body)


def _assigned_vars(body: Iterable[A.ASTNode]) -> Set[str]:
    assigned: Set[str] = set()

    def walk(node: A.ASTNode) -> None:
        if node is None:
            return
        if isinstance(node, (A.Assign, A.VarDecl)):
            assigned.add(node.var_name)
        for attr in ("body", "then_body", "else_body", "try_body", "finally_body"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                for child in sub:
                    walk(child)
        if isinstance(node, A.TryExcept):
            for _err_type, _var_name, catch_body in node.catch_blocks:
                for child in catch_body:
                    walk(child)
            if node.except_block:
                _err_var, except_body = node.except_block
                for child in except_body:
                    walk(child)
        elsif = getattr(node, "elsif_branches", None)
        if elsif:
            for _cond, branch in elsif:
                if isinstance(branch, list):
                    for child in branch:
                        walk(child)

    for stmt in body:
        walk(stmt)
    return assigned


def _field_key(node: A.FieldAccess) -> Optional[FieldKey]:
    if isinstance(node.object_expr, A.Variable):
        return (node.object_expr.name, node.field_name)
    return None


def _clear_field_ranges_for_var(self, var_name: str) -> None:
    ranges: Dict[FieldKey, IntRange] = getattr(self, "_codegen_field_int_ranges", {})
    if ranges:
        self._codegen_field_int_ranges = {
            key: rng for key, rng in ranges.items() if key[0] != var_name
        }


def _remember_record_init_ranges(self, var_name: str, expr: A.ASTNode) -> None:
    if not isinstance(expr, A.NewExpr):
        return
    fields = getattr(self, "record_fields", {}).get(expr.type_name)
    if not fields:
        return
    ranges: Dict[FieldKey, IntRange] = getattr(self, "_codegen_field_int_ranges", {})
    for field_info, arg in zip(fields, expr.args):
        field_name = str(field_info[0])
        rng = expr_int_range(self, arg)
        if rng is not None:
            ranges[(var_name, field_name)] = rng
    self._codegen_field_int_ranges = ranges


def _merge_loop_field_ranges(self, body: Iterable[A.ASTNode]) -> None:
    field_assigns = _assigned_field_exprs(body)
    if not field_assigns:
        return
    ranges: Dict[FieldKey, IntRange] = getattr(self, "_codegen_field_int_ranges", {})
    for key, exprs in field_assigns.items():
        if key not in ranges:
            continue
        joined = ranges[key]
        known = True
        for expr in exprs:
            rng = expr_int_range(self, expr)
            if rng is None:
                known = False
                break
            joined = (min(joined[0], rng[0]), max(joined[1], rng[1]))
        if known:
            ranges[key] = joined
        else:
            ranges.pop(key, None)
    self._codegen_field_int_ranges = ranges


def _assigned_field_exprs(body: Iterable[A.ASTNode]) -> Dict[FieldKey, List[A.ASTNode]]:
    assigned: Dict[FieldKey, List[A.ASTNode]] = {}

    def walk(node: A.ASTNode) -> None:
        if node is None:
            return
        if isinstance(node, A.FieldAssign) and isinstance(node.object_expr, A.Variable):
            key = (node.object_expr.name, node.field_name)
            assigned.setdefault(key, []).append(node.value)
        for attr in ("body", "then_body", "else_body", "try_body", "finally_body"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                for child in sub:
                    walk(child)
        if isinstance(node, A.TryExcept):
            for _err_type, _var_name, catch_body in node.catch_blocks:
                for child in catch_body:
                    walk(child)
            if node.except_block:
                _err_var, except_body = node.except_block
                for child in except_body:
                    walk(child)
        elsif = getattr(node, "elsif_branches", None)
        if elsif:
            for _cond, branch in elsif:
                if isinstance(branch, list):
                    for child in branch:
                        walk(child)

    for stmt in body:
        walk(stmt)
    return assigned

from __future__ import annotations

from dataclasses import dataclass, field
from parser import ast as A
from typing import Dict, List, Optional, Set, Tuple

from .range_facts_proofs import RangeFactsProofMixin
from .range_facts_scan import scan_function as _scan_function_impl
from .range_facts_scan import scan_if as _scan_if_impl
from .range_facts_scan import scan_loop as _scan_loop_impl
from .range_facts_scan import scan_node as _scan_node_impl
from .range_facts_types import Interval, StringInfo
from .range_facts_utils import capture_expr_tree as _capture_expr_tree_impl
from .range_facts_utils import collect_assigned_vars as _collect_assigned_vars_impl
from .range_facts_utils import (
    contains_unknown_side_effect_call as _contains_unknown_side_effect_call_impl,
)
from .range_facts_utils import (
    expr_contains_unknown_side_effect as _expr_contains_unknown_side_effect_impl,
)
from .range_facts_utils import infer_array_info as _infer_array_info_impl
from .range_facts_utils import (
    invalidate_for_side_effect as _invalidate_for_side_effect_impl,
)
from .range_facts_utils import (
    invalidate_non_locked_scope as _invalidate_non_locked_scope_impl,
)
from .range_facts_utils import iter_child_nodes as _iter_child_nodes_impl
from .range_facts_utils import (
    node_is_unknown_side_effect_barrier as _node_is_unknown_side_effect_barrier_impl,
)
from .range_facts_utils import observe_calls_in_expr as _observe_calls_in_expr_impl
from .range_facts_utils import walk_ast as _walk_ast_impl


@dataclass(slots=True)
class RangeFacts(RangeFactsProofMixin):
    """Holds inferred range facts and proof helpers."""

    type_alias_ranges: Dict[str, Interval] = field(default_factory=dict)
    scope_ranges: Dict[Optional[str], Dict[str, Interval]] = field(default_factory=dict)
    expr_scope_ranges: Dict[Tuple[Optional[str], int], Dict[str, Interval]] = field(
        default_factory=dict
    )
    expr_unknown_reasons: Dict[Tuple[Optional[str], int], Dict[str, str]] = field(
        default_factory=dict
    )
    expr_loop_reasons: Dict[Tuple[Optional[str], int], Dict[str, str]] = field(
        default_factory=dict
    )
    array_infos: Dict[Optional[str], Dict[str, Tuple[Interval, int]]] = field(
        default_factory=dict
    )
    dict_value_infos: Dict[Optional[str], Dict[str, Dict[str, Interval]]] = field(
        default_factory=dict
    )
    expr_array_infos: Dict[
        Tuple[Optional[str], int], Dict[str, Tuple[Interval, int]]
    ] = field(default_factory=dict)
    expr_dict_value_infos: Dict[
        Tuple[Optional[str], int], Dict[str, Dict[str, Interval]]
    ] = field(default_factory=dict)
    expr_string_infos: Dict[Tuple[Optional[str], int], Dict[str, StringInfo]] = field(
        default_factory=dict
    )
    scope_relations: Dict[Optional[str], Set[Tuple[str, str, str]]] = field(
        default_factory=dict
    )
    expr_relations: Dict[Tuple[Optional[str], int], Set[Tuple[str, str, str]]] = field(
        default_factory=dict
    )
    locked_ranges: Dict[Optional[str], Set[str]] = field(default_factory=dict)
    unknown_reasons: Dict[Tuple[Optional[str], str], str] = field(default_factory=dict)
    loop_reasons: Dict[Tuple[Optional[str], str], str] = field(default_factory=dict)
    call_arg_ranges: Dict[str, Dict[int, Interval]] = field(default_factory=dict)
    call_arg_string_infos: Dict[str, Dict[int, StringInfo]] = field(
        default_factory=dict
    )
    call_hint_params: Dict[str, Set[str]] = field(default_factory=dict)
    function_return_ranges: Dict[str, Interval] = field(default_factory=dict)
    string_len_vars: Dict[Optional[str], Dict[str, str]] = field(default_factory=dict)
    string_infos: Dict[Optional[str], Dict[str, StringInfo]] = field(
        default_factory=dict
    )
    safe_char_at_calls: Set[Tuple[Optional[str], int]] = field(default_factory=set)
    nonnegative_vars: Set[Tuple[Optional[str], str]] = field(default_factory=set)

    def has_expr_scope_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> bool:
        return (func_scope, id(expr)) in self.expr_scope_ranges

    def get_var_range(
        self, var_name: str, func_scope: Optional[str]
    ) -> Optional[Interval]:
        scoped = self.scope_ranges.get(func_scope)
        if scoped is not None and var_name in scoped:
            return scoped[var_name]
        global_scope = self.scope_ranges.get(None)
        if global_scope is not None:
            return global_scope.get(var_name)
        return None

    def set_var_range(
        self, var_name: str, interval: Interval, func_scope: Optional[str]
    ) -> None:
        if func_scope not in self.scope_ranges:
            self.scope_ranges[func_scope] = {}
        self.scope_ranges[func_scope][var_name] = interval

    def clear_var_range(self, var_name: str, func_scope: Optional[str]) -> None:
        scoped = self.scope_ranges.get(func_scope)
        if scoped is not None:
            scoped.pop(var_name, None)

    def lock_var_range(self, var_name: str, func_scope: Optional[str]) -> None:
        self.locked_ranges.setdefault(func_scope, set()).add(var_name)

    def set_unknown_reason(
        self, var_name: str, func_scope: Optional[str], reason: str
    ) -> None:
        self.unknown_reasons[(func_scope, var_name)] = reason

    def clear_unknown_reason(self, var_name: str, func_scope: Optional[str]) -> None:
        self.unknown_reasons.pop((func_scope, var_name), None)

    def set_loop_reason(
        self, var_name: str, func_scope: Optional[str], reason: str
    ) -> None:
        self.loop_reasons[(func_scope, var_name)] = reason

    def clear_loop_reason(self, var_name: str, func_scope: Optional[str]) -> None:
        self.loop_reasons.pop((func_scope, var_name), None)

    def get_array_info(
        self, var_name: str, func_scope: Optional[str]
    ) -> Optional[Tuple[Interval, int]]:
        scoped = self.array_infos.get(func_scope)
        if scoped is not None and var_name in scoped:
            return scoped[var_name]
        global_scope = self.array_infos.get(None)
        if global_scope is not None:
            return global_scope.get(var_name)
        return None

    def set_array_info(
        self,
        var_name: str,
        elem_interval: Interval,
        array_len: int,
        func_scope: Optional[str],
    ) -> None:
        self.array_infos.setdefault(func_scope, {})[var_name] = (
            elem_interval,
            array_len,
        )

    def clear_array_info(self, var_name: str, func_scope: Optional[str]) -> None:
        scoped = self.array_infos.get(func_scope)
        if scoped is not None:
            scoped.pop(var_name, None)

    def get_dict_value_info(
        self, var_name: str, key: str, func_scope: Optional[str]
    ) -> Optional[Interval]:
        scoped = self.dict_value_infos.get(func_scope)
        if scoped is not None:
            values = scoped.get(var_name)
            if values is not None and key in values:
                return values[key]
        global_scope = self.dict_value_infos.get(None)
        if global_scope is not None:
            values = global_scope.get(var_name)
            if values is not None:
                return values.get(key)
        return None

    def set_dict_value_info(
        self, var_name: str, key: str, interval: Interval, func_scope: Optional[str]
    ) -> None:
        self.dict_value_infos.setdefault(func_scope, {}).setdefault(var_name, {})[
            key
        ] = interval

    def set_dict_value_infos(
        self, var_name: str, values: Dict[str, Interval], func_scope: Optional[str]
    ) -> None:
        self.dict_value_infos.setdefault(func_scope, {})[var_name] = dict(values)

    def clear_dict_value_info(
        self, var_name: str, func_scope: Optional[str], key: Optional[str] = None
    ) -> None:
        scoped = self.dict_value_infos.get(func_scope)
        if scoped is None:
            return
        if key is None:
            scoped.pop(var_name, None)
            return
        values = scoped.get(var_name)
        if values is not None:
            values.pop(key, None)

    def observe_call_arg_interval(
        self, func_name: str, arg_index: int, interval: Interval
    ) -> None:
        slots = self.call_arg_ranges.setdefault(func_name, {})
        existing = slots.get(arg_index)
        slots[arg_index] = interval if existing is None else existing.union(interval)

    def observe_call_arg_string_info(
        self, func_name: str, arg_index: int, info: StringInfo
    ) -> None:
        slots = self.call_arg_string_infos.setdefault(func_name, {})
        existing = slots.get(arg_index)
        slots[arg_index] = info if existing is None else existing.union(info)

    def observe_function_return_interval(
        self, func_name: str, interval: Interval
    ) -> None:
        existing = self.function_return_ranges.get(func_name)
        self.function_return_ranges[func_name] = (
            interval if existing is None else existing.union(interval)
        )

    def set_string_info(
        self, func_scope: Optional[str], var_name: str, info: StringInfo
    ) -> None:
        self.string_infos.setdefault(func_scope, {})[var_name] = info

    def get_string_info(
        self, func_scope: Optional[str], var_name: str
    ) -> Optional[StringInfo]:
        scoped = self.string_infos.get(func_scope)
        if scoped is not None and var_name in scoped:
            return scoped[var_name]
        global_scope = self.string_infos.get(None)
        if global_scope is not None:
            return global_scope.get(var_name)
        return None

    def clear_string_info(self, func_scope: Optional[str], var_name: str) -> None:
        scoped = self.string_infos.get(func_scope)
        if scoped is not None:
            scoped.pop(var_name, None)

    def set_string_len_var(
        self, func_scope: Optional[str], len_var: str, string_var: str
    ) -> None:
        self.string_len_vars.setdefault(func_scope, {})[len_var] = string_var

    def clear_string_len_var(self, func_scope: Optional[str], var_name: str) -> None:
        scoped = self.string_len_vars.get(func_scope)
        if not scoped:
            return
        scoped.pop(var_name, None)
        stale = [name for name, source in scoped.items() if source == var_name]
        for name in stale:
            scoped.pop(name, None)

    def mark_safe_char_at_call(
        self, func_scope: Optional[str], call_node: A.Call
    ) -> None:
        self.safe_char_at_calls.add((func_scope, id(call_node)))

    def is_safe_char_at_call(
        self, func_scope: Optional[str], call_node: A.Call
    ) -> bool:
        return (func_scope, id(call_node)) in self.safe_char_at_calls

    def mark_nonnegative_var(self, func_scope: Optional[str], var_name: str) -> None:
        self.nonnegative_vars.add((func_scope, var_name))

    def clear_nonnegative_var(self, func_scope: Optional[str], var_name: str) -> None:
        self.nonnegative_vars.discard((func_scope, var_name))

    def is_nonnegative_var(self, func_scope: Optional[str], var_name: str) -> bool:
        return (func_scope, var_name) in self.nonnegative_vars

    def capture_expr_scope(
        self, expr: A.ASTNode, func_scope: Optional[str], scope: Dict[str, Interval]
    ) -> None:
        """Store a per-expression scope snapshot for point-in-time proofs."""
        self.expr_scope_ranges[(func_scope, id(expr))] = dict(scope)
        scoped_unknown = {
            name: reason
            for (scope_name, name), reason in self.unknown_reasons.items()
            if scope_name == func_scope
        }
        self.expr_unknown_reasons[(func_scope, id(expr))] = scoped_unknown
        scoped_arrays = self.array_infos.get(func_scope, {})
        self.expr_array_infos[(func_scope, id(expr))] = dict(scoped_arrays)
        scoped_dicts = self.dict_value_infos.get(func_scope, {})
        self.expr_dict_value_infos[(func_scope, id(expr))] = {
            name: dict(values) for name, values in scoped_dicts.items()
        }
        scoped_strings = self.string_infos.get(func_scope, {})
        self.expr_string_infos[(func_scope, id(expr))] = dict(scoped_strings)
        scoped_loop_reasons = {
            name: reason
            for (scope_name, name), reason in self.loop_reasons.items()
            if scope_name == func_scope
        }
        self.expr_loop_reasons[(func_scope, id(expr))] = scoped_loop_reasons
        self.expr_relations[(func_scope, id(expr))] = set(
            self.scope_relations.get(func_scope, set())
        )

    def _expr_scope_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, Interval]]:
        return self.expr_scope_ranges.get((func_scope, id(expr)))

    def _expr_unknown_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, str]]:
        return self.expr_unknown_reasons.get((func_scope, id(expr)))

    def _expr_array_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, Tuple[Interval, int]]]:
        return self.expr_array_infos.get((func_scope, id(expr)))

    def _expr_dict_value_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, Dict[str, Interval]]]:
        return self.expr_dict_value_infos.get((func_scope, id(expr)))

    def _expr_string_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, StringInfo]]:
        return self.expr_string_infos.get((func_scope, id(expr)))

    def _expr_loop_reason_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Dict[str, str]]:
        return self.expr_loop_reasons.get((func_scope, id(expr)))

    def _expr_relation_snapshot(
        self, expr: A.ASTNode, func_scope: Optional[str]
    ) -> Optional[Set[Tuple[str, str, str]]]:
        return self.expr_relations.get((func_scope, id(expr)))

    @staticmethod
    def _range_from_scope(
        var_name: str, scope: Optional[Dict[str, Interval]]
    ) -> Optional[Interval]:
        if scope is None:
            return None
        return scope.get(var_name)

    @staticmethod
    def _unknown_from_scope(
        var_name: str, scope: Optional[Dict[str, str]]
    ) -> Optional[str]:
        if scope is None:
            return None
        return scope.get(var_name)


class RangeFactsAnalyzer:
    _PURE_CALL_NAMES = {
        "abs",
        "min",
        "max",
        "sqrt",
        "pow",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "log",
        "log10",
        "exp",
        "floor",
        "ceil",
        "round",
        "trunc",
        "clamp",
        "str",
        "chr",
        "hex",
        "bin",
        "oct",
        "len",
        "strlen",
        "char_at",
        "unsafe_char_at",
        "typeof",
        "sizeof",
    }

    def __init__(self) -> None:
        self._known_function_names: Set[str] = set()
        self._known_function_names_lower: Set[str] = set()

    def run(self, nodes: List[A.ASTNode]) -> RangeFacts:
        self._known_function_names = {
            node.name for node in nodes if isinstance(node, A.Function)
        }
        self._known_function_names_lower = {
            name.lower() for name in self._known_function_names
        }
        prior_calls: Dict[str, Dict[int, Interval]] = {}
        prior_string_calls: Dict[str, Dict[int, StringInfo]] = {}
        prior_returns: Dict[str, Interval] = {}
        facts = RangeFacts()
        for _ in range(3):
            facts = RangeFacts(
                call_arg_ranges={
                    name: {idx: Interval(r.low, r.high) for idx, r in ranges.items()}
                    for name, ranges in prior_calls.items()
                },
                call_arg_string_infos={
                    name: {
                        idx: StringInfo(info.min_len, info.max_len, info.max_digit_run)
                        for idx, info in infos.items()
                    }
                    for name, infos in prior_string_calls.items()
                },
                function_return_ranges={
                    name: Interval(r.low, r.high) for name, r in prior_returns.items()
                },
            )
            self._collect_type_alias_ranges(nodes, facts)
            self._scan_nodes(nodes, None, facts)
            next_calls = {
                name: {idx: Interval(r.low, r.high) for idx, r in ranges.items()}
                for name, ranges in facts.call_arg_ranges.items()
            }
            next_string_calls = {
                name: {
                    idx: StringInfo(info.min_len, info.max_len, info.max_digit_run)
                    for idx, info in infos.items()
                }
                for name, infos in facts.call_arg_string_infos.items()
            }
            next_returns = {
                name: Interval(r.low, r.high)
                for name, r in facts.function_return_ranges.items()
            }
            if (
                next_calls == prior_calls
                and next_string_calls == prior_string_calls
                and next_returns == prior_returns
            ):
                break
            prior_calls = next_calls
            prior_string_calls = next_string_calls
            prior_returns = next_returns
        return facts

    def _collect_type_alias_ranges(
        self, nodes: List[A.ASTNode], facts: RangeFacts
    ) -> None:
        for node in nodes:
            if isinstance(node, A.TypeAlias):
                rng = self._range_from_range_type(node.target_type, facts, None)
                if rng is not None:
                    facts.type_alias_ranges[node.name] = rng

    def _scan_nodes(
        self,
        nodes: List[A.ASTNode],
        func_scope: Optional[str],
        facts: RangeFacts,
    ) -> None:
        if func_scope not in facts.scope_ranges:
            facts.scope_ranges[func_scope] = {}
        for node in nodes:
            self._scan_node(node, func_scope, facts)

    def _scan_node(
        self, node: A.ASTNode, func_scope: Optional[str], facts: RangeFacts
    ) -> None:
        _scan_node_impl(self, node, func_scope, facts, interval_ctor=Interval)

    _scan_function = _scan_function_impl

    _scan_if = _scan_if_impl

    _scan_loop = _scan_loop_impl

    _collect_assigned_vars = _collect_assigned_vars_impl
    _capture_expr_tree = _capture_expr_tree_impl
    _walk_ast = _walk_ast_impl
    _iter_child_nodes = _iter_child_nodes_impl
    _invalidate_non_locked_scope = _invalidate_non_locked_scope_impl
    _invalidate_for_side_effect = _invalidate_for_side_effect_impl
    _node_is_unknown_side_effect_barrier = _node_is_unknown_side_effect_barrier_impl
    _expr_contains_unknown_side_effect = _expr_contains_unknown_side_effect_impl
    _contains_unknown_side_effect_call = _contains_unknown_side_effect_call_impl

    def _infer_array_info(
        self,
        expr: Optional[A.ASTNode],
        func_scope: Optional[str],
        facts: RangeFacts,
        scope: Dict[str, Interval],
    ) -> Optional[Tuple[Interval, int]]:
        out = _infer_array_info_impl(
            self,
            expr,
            func_scope,
            facts,
            scope,
            interval_ctor=Interval,
        )
        if out is None:
            return None
        elem_rng, arr_len = out
        return elem_rng, arr_len

    _observe_calls_in_expr = _observe_calls_in_expr_impl

    def _range_from_type_name(
        self, type_name: object, facts: RangeFacts
    ) -> Optional[Interval]:
        name = str(type_name)
        return facts.type_alias_ranges.get(name)

    def _range_from_range_type(
        self,
        range_node: object,
        facts: RangeFacts,
        func_scope: Optional[str],
    ) -> Optional[Interval]:
        if not isinstance(range_node, A.RangeType):
            return None
        low = facts._expr_interval(range_node.low, func_scope)
        high = facts._expr_interval(range_node.high, func_scope)
        if low is None or high is None:
            return None
        if low.low != low.high or high.low != high.high:
            return None
        low_val = low.low
        high_val = high.low
        if range_node.exclusive:
            high_val -= 1
        if high_val < low_val:
            return None
        return Interval(low_val, high_val)

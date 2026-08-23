"""Statement Generation - orchestrator and responsibility-named bindings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from llvmlite import ir

from .control_loop_utils import (
    close_streams_if_outer_loop as _m__close_streams_if_outer_loop,
)
from .emit_statements_basic import _emit_range_check as _m__emit_range_check
from .emit_statements_basic import _evaluate_comptime as _m__evaluate_comptime
from .emit_statements_basic import visit_Assert as _m_visit_Assert
from .emit_statements_basic import visit_Assign as _m_visit_Assign
from .emit_statements_basic import visit_BlockCall as _m_visit_BlockCall
from .emit_statements_basic import visit_Break as _m_visit_Break
from .emit_statements_basic import visit_Call as _m_visit_Call
from .emit_statements_basic import visit_ComptimeBlock as _m_visit_ComptimeBlock
from .emit_statements_basic import visit_ComptimeExpr as _m_visit_ComptimeExpr
from .emit_statements_basic import visit_ComptimeIf as _m_visit_ComptimeIf
from .emit_statements_basic import visit_Continue as _m_visit_Continue
from .emit_statements_basic import visit_InlineAsm as _m_visit_InlineAsm
from .emit_statements_basic import visit_RangeVarDecl as _m_visit_RangeVarDecl
from .emit_statements_basic import visit_Return as _m_visit_Return
from .emit_statements_basic import visit_StaticAssert as _m_visit_StaticAssert
from .emit_statements_basic import visit_TupleAssign as _m_visit_TupleAssign
from .emit_statements_basic import visit_TypeAlias as _m_visit_TypeAlias
from .emit_statements_basic import visit_VarDecl as _m_visit_VarDecl
from .emit_statements_block_each import _block_each as _m__block_each
from .emit_statements_control_data import _block_times as _m__block_times
from .emit_statements_control_data import visit_DictAssign as _m_visit_DictAssign
from .emit_statements_control_data import visit_DoWhile as _m_visit_DoWhile
from .emit_statements_control_data import visit_FieldAssign as _m_visit_FieldAssign
from .emit_statements_control_data import visit_For as _m_visit_For
from .emit_statements_control_data import visit_Foreach as _m_visit_Foreach
from .emit_statements_control_data import visit_If as _m_visit_If
from .emit_statements_control_data import visit_Loop as _m_visit_Loop
from .emit_statements_control_data import visit_Repeat as _m_visit_Repeat
from .emit_statements_control_data import visit_While as _m_visit_While
from .emit_statements_match_exceptions import _can_use_switch as _m__can_use_switch
from .emit_statements_match_exceptions import _default_value as _m__default_value
from .emit_statements_match_exceptions import (
    _extract_pattern_bindings as _m__extract_pattern_bindings,
)
from .emit_statements_match_exceptions import (
    _generate_pattern_match as _m__generate_pattern_match,
)
from .emit_statements_match_exceptions import (
    _generate_sequential_match as _m__generate_sequential_match,
)
from .emit_statements_match_exceptions import _generate_switch as _m__generate_switch
from .emit_statements_match_exceptions import (
    _is_string_pointer as _m__is_string_pointer,
)
from .emit_statements_match_exceptions import _values_equal as _m__values_equal
from .emit_statements_match_exceptions import (
    _visit_foreach_range as _m__visit_foreach_range,
)
from .emit_statements_match_exceptions import visit_Match as _m_visit_Match
from .emit_statements_match_exceptions import visit_Throw as _m_visit_Throw
from .emit_statements_match_exceptions import visit_TryExcept as _m_visit_TryExcept

if TYPE_CHECKING:
    from codegen.codegen import CodeGen


class StmtGenerator:
    def __init__(self, codegen: CodeGen):
        self.codegen = codegen
        # Cache of {NodeClass: bound visitor method}. Lazy-populated on first
        # encounter of each node type so we skip the f-string + getattr per
        # call thereafter.
        self._visit_cache: dict[type, Callable[[Any], Any]] = {}

    @property
    def builder(self) -> ir.IRBuilder:
        return self.codegen.current_builder

    @property
    def func(self) -> ir.Function:
        return self.codegen.current_function

    def generate_stmt(self, node):
        if self.builder.block.is_terminated:
            return None
        node_line = getattr(node, "line", 0)
        if node_line > 0:
            di_loc = self.codegen.di_location_for_line(node_line)
            if di_loc is not None:
                self.builder.debug_metadata = di_loc
        cls = type(node)
        visitor = self._visit_cache.get(cls)
        if visitor is None:
            visitor = getattr(self, f"visit_{cls.__name__}", self.generic_visit)
            self._visit_cache[cls] = visitor
        return visitor(node)

    def generic_visit(self, node):
        return self.codegen.generate_expr(node)

    visit_Return = _m_visit_Return
    visit_Break = _m_visit_Break
    visit_Continue = _m_visit_Continue
    visit_Assert = _m_visit_Assert
    visit_InlineAsm = _m_visit_InlineAsm
    visit_ComptimeExpr = _m_visit_ComptimeExpr
    visit_ComptimeBlock = _m_visit_ComptimeBlock
    visit_ComptimeIf = _m_visit_ComptimeIf
    visit_StaticAssert = _m_visit_StaticAssert
    _evaluate_comptime = _m__evaluate_comptime
    visit_VarDecl = _m_visit_VarDecl
    visit_RangeVarDecl = _m_visit_RangeVarDecl
    _emit_range_check = _m__emit_range_check
    visit_TypeAlias = _m_visit_TypeAlias
    visit_Assign = _m_visit_Assign
    visit_TupleAssign = _m_visit_TupleAssign
    visit_BlockCall = _m_visit_BlockCall
    visit_Call = _m_visit_Call
    _block_each = _m__block_each
    _block_times = _m__block_times
    visit_FieldAssign = _m_visit_FieldAssign
    visit_DictAssign = _m_visit_DictAssign
    visit_If = _m_visit_If
    _close_streams_if_outer_loop = _m__close_streams_if_outer_loop
    visit_While = _m_visit_While
    visit_DoWhile = _m_visit_DoWhile
    visit_For = _m_visit_For
    visit_Loop = _m_visit_Loop
    visit_Repeat = _m_visit_Repeat
    visit_Foreach = _m_visit_Foreach
    visit_Match = _m_visit_Match
    _generate_pattern_match = _m__generate_pattern_match
    _extract_pattern_bindings = _m__extract_pattern_bindings
    _can_use_switch = _m__can_use_switch
    _generate_switch = _m__generate_switch
    _generate_sequential_match = _m__generate_sequential_match
    visit_TryExcept = _m_visit_TryExcept
    visit_Throw = _m_visit_Throw
    _default_value = _m__default_value
    _values_equal = _m__values_equal
    _is_string_pointer = _m__is_string_pointer
    _visit_foreach_range = _m__visit_foreach_range

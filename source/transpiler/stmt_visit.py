"""Statement visitors for the C transpiler."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .stmt_visit_calls import _emit_dealloc_arg as _m_emit_dealloc_arg
from .stmt_visit_calls import _emit_method_call_text as _m_emit_method_call_text
from .stmt_visit_calls import _emit_print_call as _m_emit_print_call
from .stmt_visit_calls import _get_printf_arg as _m_get_printf_arg
from .stmt_visit_calls import _get_printf_spec as _m_get_printf_spec
from .stmt_visit_calls import _resolve_method_class as _m_resolve_method_class
from .stmt_visit_calls import visit_AtomicOp as _m_visit_AtomicOp
from .stmt_visit_calls import visit_Call as _m_visit_Call
from .stmt_visit_calls import visit_ChannelClose as _m_visit_ChannelClose
from .stmt_visit_calls import visit_ChannelCreate as _m_visit_ChannelCreate
from .stmt_visit_calls import visit_ChannelRecv as _m_visit_ChannelRecv
from .stmt_visit_calls import visit_ChannelSend as _m_visit_ChannelSend
from .stmt_visit_calls import visit_ChannelTryRecv as _m_visit_ChannelTryRecv
from .stmt_visit_calls import visit_ChannelTrySend as _m_visit_ChannelTrySend
from .stmt_visit_calls import visit_Join as _m_visit_Join
from .stmt_visit_calls import visit_MethodCall as _m_visit_MethodCall
from .stmt_visit_calls import visit_Spawn as _m_visit_Spawn
from .stmt_visit_class import _class_new_signature as _m_class_new_signature
from .stmt_visit_class import _emit_class_new_wrapper as _m_emit_class_new_wrapper
from .stmt_visit_class import _evaluate_comptime as _m_evaluate_comptime
from .stmt_visit_class import _sanitize_method_name as _m_sanitize_method_name
from .stmt_visit_class import visit_CInclude as _m_visit_CInclude
from .stmt_visit_class import visit_ClassDef as _m_visit_ClassDef
from .stmt_visit_class import visit_ComptimeBlock as _m_visit_ComptimeBlock
from .stmt_visit_class import visit_ComptimeExpr as _m_visit_ComptimeExpr
from .stmt_visit_class import visit_ComptimeIf as _m_visit_ComptimeIf
from .stmt_visit_class import visit_EnumDef as _m_visit_EnumDef
from .stmt_visit_class import visit_ExternFn as _m_visit_ExternFn
from .stmt_visit_class import visit_ExternRecordDef as _m_visit_ExternRecordDef
from .stmt_visit_class import visit_ExternVar as _m_visit_ExternVar
from .stmt_visit_class import visit_Function as _m_visit_Function
from .stmt_visit_class import visit_GenericClass as _m_visit_GenericClass
from .stmt_visit_class import visit_GenericFunction as _m_visit_GenericFunction
from .stmt_visit_class import visit_GenericRecord as _m_visit_GenericRecord
from .stmt_visit_class import visit_LinkDirective as _m_visit_LinkDirective
from .stmt_visit_class import visit_RecordDef as _m_visit_RecordDef
from .stmt_visit_class import visit_ReinterpretCast as _m_visit_ReinterpretCast
from .stmt_visit_class import visit_StaticAssert as _m_visit_StaticAssert
from .stmt_visit_class import visit_TemplateBlock as _m_visit_TemplateBlock
from .stmt_visit_class import visit_UnionDef as _m_visit_UnionDef
from .stmt_visit_class_method import _generate_class_method as _m_generate_class_method
from .stmt_visit_control import _error_type_hash as _m_error_type_hash
from .stmt_visit_control import visit_Block as _m_visit_Block
from .stmt_visit_control import visit_Break as _m_visit_Break
from .stmt_visit_control import visit_Continue as _m_visit_Continue
from .stmt_visit_control import visit_DoWhile as _m_visit_DoWhile
from .stmt_visit_control import visit_For as _m_visit_For
from .stmt_visit_control import visit_Foreach as _m_visit_Foreach
from .stmt_visit_control import visit_If as _m_visit_If
from .stmt_visit_control import visit_InlineAsm as _m_visit_InlineAsm
from .stmt_visit_control import visit_Loop as _m_visit_Loop
from .stmt_visit_control import visit_Match as _m_visit_Match
from .stmt_visit_control import visit_Repeat as _m_visit_Repeat
from .stmt_visit_control import visit_Return as _m_visit_Return
from .stmt_visit_control import visit_Throw as _m_visit_Throw
from .stmt_visit_control import visit_TryExcept as _m_visit_TryExcept
from .stmt_visit_control import visit_While as _m_visit_While
from .stmt_visit_data import _infer_ailang_type as _m_infer_ailang_type
from .stmt_visit_data import _type_name_to_ailang as _m_type_name_to_ailang
from .stmt_visit_data import visit_Assert as _m_visit_Assert
from .stmt_visit_data import visit_Assign as _m_visit_Assign
from .stmt_visit_data import visit_RangeVarDecl as _m_visit_RangeVarDecl
from .stmt_visit_data import visit_TupleAssign as _m_visit_TupleAssign
from .stmt_visit_data import visit_TypeAlias as _m_visit_TypeAlias
from .stmt_visit_data import visit_VarDecl as _m_visit_VarDecl
from .stmt_visit_data_fields import visit_DictAssign as _m_visit_DictAssign
from .stmt_visit_data_fields import visit_FieldAssign as _m_visit_FieldAssign
from .stmt_visit_list_comprehension import (
    _generate_list_comprehension as _m_generate_list_comprehension,
)


class CStmtEmitter:
    """Statement-emit service with transpiler back-reference."""

    output: List[str]
    indent: int
    declared_vars: Set[str]
    current_function: Optional[str]
    profile_enabled: bool
    user_defined_funcs: Set[str]
    _current_class: Optional[str]
    _current_ret_type: str
    _synchronized_mutex_name: Optional[str]
    _guard_active: bool
    _loop_depth: int
    _bound_counter: int
    _current_bound_var: str
    _unchecked_mode: bool
    _profile_func_index: Dict[str, int]
    _class_locals_for_cleanup: List[Tuple[str, str]]
    _string_locals_for_cleanup: List[str]
    _str_array_locals_for_cleanup: List[str]
    _int_array_locals_for_cleanup: List[str]
    _dyn_array_locals_for_cleanup: List[str]
    _lc_str_array_locals_for_cleanup: List[str]
    _dict_locals_for_cleanup: List[str]
    _mixed_ownership_cleanup: List[str]
    _local_cleanup_lines: List[str]
    _needs_stream_cleanup: bool

    def __init__(self, transpiler: object) -> None:
        object.__setattr__(self, "_t", transpiler)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._t, name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(self._t, name, value)

    visit_Function = _m_visit_Function
    visit_If = _m_visit_If
    visit_TryExcept = _m_visit_TryExcept
    visit_Throw = _m_visit_Throw
    _error_type_hash = _m_error_type_hash
    visit_While = _m_visit_While
    visit_DoWhile = _m_visit_DoWhile
    visit_For = _m_visit_For
    visit_Foreach = _m_visit_Foreach
    visit_Loop = _m_visit_Loop
    visit_Repeat = _m_visit_Repeat
    visit_Return = _m_visit_Return
    visit_Break = _m_visit_Break
    visit_Continue = _m_visit_Continue
    visit_InlineAsm = _m_visit_InlineAsm
    visit_Assign = _m_visit_Assign
    _infer_ailang_type = _m_infer_ailang_type
    _generate_list_comprehension = _m_generate_list_comprehension
    visit_TupleAssign = _m_visit_TupleAssign
    visit_VarDecl = _m_visit_VarDecl
    visit_RangeVarDecl = _m_visit_RangeVarDecl
    visit_TypeAlias = _m_visit_TypeAlias
    _type_name_to_ailang = _m_type_name_to_ailang
    visit_RecordDef = _m_visit_RecordDef
    visit_GenericRecord = _m_visit_GenericRecord
    visit_GenericClass = _m_visit_GenericClass
    visit_GenericFunction = _m_visit_GenericFunction
    visit_EnumDef = _m_visit_EnumDef
    visit_TemplateBlock = _m_visit_TemplateBlock
    visit_CInclude = _m_visit_CInclude
    visit_LinkDirective = _m_visit_LinkDirective
    visit_ExternFn = _m_visit_ExternFn
    visit_ExternRecordDef = _m_visit_ExternRecordDef
    visit_ExternVar = _m_visit_ExternVar
    visit_UnionDef = _m_visit_UnionDef
    visit_ReinterpretCast = _m_visit_ReinterpretCast
    visit_ComptimeExpr = _m_visit_ComptimeExpr
    visit_ComptimeBlock = _m_visit_ComptimeBlock
    visit_ComptimeIf = _m_visit_ComptimeIf
    visit_StaticAssert = _m_visit_StaticAssert
    _evaluate_comptime = _m_evaluate_comptime
    visit_ClassDef = _m_visit_ClassDef
    _class_new_signature = _m_class_new_signature
    _emit_class_new_wrapper = _m_emit_class_new_wrapper
    _sanitize_method_name = _m_sanitize_method_name
    _generate_class_method = _m_generate_class_method
    visit_Assert = _m_visit_Assert
    visit_FieldAssign = _m_visit_FieldAssign
    visit_DictAssign = _m_visit_DictAssign
    visit_Block = _m_visit_Block
    visit_Match = _m_visit_Match
    _get_printf_spec = _m_get_printf_spec
    _get_printf_arg = _m_get_printf_arg
    _emit_print_call = _m_emit_print_call
    _emit_dealloc_arg = _m_emit_dealloc_arg
    visit_Call = _m_visit_Call
    _resolve_method_class = _m_resolve_method_class
    _emit_method_call_text = _m_emit_method_call_text
    visit_MethodCall = _m_visit_MethodCall
    visit_Spawn = _m_visit_Spawn
    visit_Join = _m_visit_Join
    visit_AtomicOp = _m_visit_AtomicOp
    visit_ChannelSend = _m_visit_ChannelSend
    visit_ChannelClose = _m_visit_ChannelClose
    visit_ChannelTrySend = _m_visit_ChannelTrySend
    visit_ChannelTryRecv = _m_visit_ChannelTryRecv
    visit_ChannelRecv = _m_visit_ChannelRecv
    visit_ChannelCreate = _m_visit_ChannelCreate

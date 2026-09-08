"""Walk an AST and produce a RuntimeNeeds snapshot.

The scanner owns helper-category detection for CTranspiler. It receives its
inputs explicitly, returns a RuntimeNeeds value, and avoids mutating caller
state directly.
"""

from __future__ import annotations

from collections.abc import Callable
from parser import ast as A
from typing import Any, ClassVar

from transpiler.class_field_ownership import auto_owned_field_kind
from transpiler.dict_specialization import fixed_dict_literal_slots
from transpiler.helper_scanner_concurrency import _scan_channel as _m_scan_channel
from transpiler.helper_scanner_concurrency import (
    _scan_concurrency as _m_scan_concurrency,
)
from transpiler.helper_scanner_names import (
    _FD_HELPER_NAMES,
    _PROCESS_HELPER_NAMES,
    _WIN32_HELPER_NAMES,
)
from transpiler.helper_scanner_string_array import (
    _array_access_literal_proven,
    _cached_strlen_field_arg,
    _class_field_is_string,
    _field_assign_targets_string,
    _is_integer_type_name,
    _known_array_len_hint,
    _literal_char_at_length_proven,
    _scan_methodcall_hidden_string_lengths,
    _scan_newexpr_hidden_string_lengths,
    _scan_streq_slice_fastpath,
    _str_arg_is_known_integer,
    _virtual_concat_numeric_arg,
    _virtual_strlen_numeric_arg,
)
from transpiler.runtime_needs import RuntimeNeeds
from transpiler.strlen_assign_cache import (
    baseconv_known_integer_arg,
    collect_length_only_string_locals,
    interpolation_known_length,
    str_known_integer_arg,
)
from transpiler.wide_int_types import info_for_ailang

from .helper_scanner_detail_mixin import HelperScannerDetailMixin


class HelperScanner(HelperScannerDetailMixin):
    """Walks an AST and produces a ``RuntimeNeeds`` describing which
    runtime-helper categories the program references.

    Mirror of the legacy ``_HelperScanMixin`` behavior: same scan tree,
    same outputs. The diff is structural -- state ownership is now
    explicit. Constructed once per compile.
    """

    # Function-name -> helper-category mapping. The scanner is the only
    # consumer of this table; it lived on CTranspiler when the scanner
    # was a mixin. Moving it here removes a piece of CTranspiler's
    # surface area.
    _CALL_HELPER_MAP: ClassVar[dict[str, str]] = {
        "strlen": "strlen",
        "len": "strlen",
        "char_at": "char_at",
        "unsafe_char_at": "unsafe_char_at",
        "time_ns": "time_ns",
        "clock_ns": "time_ns",
        "time_ms": "time_ms",
        "rdtsc": "rdtsc",
        "print": "print",
        "pow": "math",
        "abs": "abs",
        "str": "int_to_str",
        "chr": "chr",
        "substr": "substr",
        "concat": "concat",
        "read_file": "file_io",
        "write_file": "file_io",
        "file_size": "file_io",
        "read_bytes": "file_io",
        "write_bytes": "file_io",
        "file_exists": "file_io",
        "file_can_execute": "file_io",
        "file_is_regular": "file_io",
        "file_is_symlink": "file_io",
        "file_is_block": "file_io",
        "file_is_char": "file_io",
        "file_is_fifo": "file_io",
        "file_is_socket": "file_io",
        "file_is_setuid": "file_io",
        "file_is_setgid": "file_io",
        "file_mtime": "file_io",
        "file_same": "file_io",
        "fd_is_tty": "file_io",
        "current_dir": "file_io",
        "change_dir": "file_io",
        "list_dir": "file_io",
        "input": "input",
        "read_stdin": "input",
        "index_of": "index_of",
        "index_of_from": "index_of",
        "startswith": "startswith",
        "endswith": "endswith",
        "str_replace": "str_replace",
        "str_escape_json": "str_escape_json",
        "streq": "streq_lit",
        "hex": "base_conv",
        "bin": "base_conv",
        "oct": "base_conv",
        "array_new": "dynamic_array",
        "array_push": "dynamic_array",
        "array_pop": "dynamic_array",
        "array_get": "dynamic_array",
        "array_set": "dynamic_array",
        "array_len": "dynamic_array",
        "sql_open": "sqlite",
        "sql_open_readonly": "sqlite",
        "sql_last_open_status": "sqlite",
        "sql_exec": "sqlite",
        "sql_close": "sqlite",
        "sql_prepare": "sqlite",
        "sql_step": "sqlite",
        "sql_bind_int": "sqlite",
        "sql_bind_text": "sqlite",
        "sql_bind_text_i64": "sqlite",
        "sql_bind_text_i64_parts": "sqlite",
        "sql_bind_null": "sqlite",
        "sql_clear_bindings": "sqlite",
        "sql_reset": "sqlite",
        "sql_column_int": "sqlite",
        "sql_column_text": "sqlite",
        "sql_finalize": "sqlite",
        # File ops (file_exists/file_can_execute already map above to file_io;
        # access is the POSIX-named helper. mkdir is the POSIX alias for make_dir.)
        "access": "file_io",
        "make_dir": "fileops",
        "mkdir": "fileops",
        "delete_file": "fileops",
        "unlink": "fileops",
        "move_file": "fileops",
        "rename": "fileops",
        **{name: "fd" for name in _FD_HELPER_NAMES},
        "str_array_new": "str_array",
        "str_array_push": "str_array",
        "str_array_get": "str_array",
        "str_array_set": "str_array",
        "str_array_pop": "str_array",
        "str_array_len": "str_array",
        "str_array_join": "str_array",
        "dict_new": "dict",
        "split": "split",
        "split_ints": "split_ints",
        "parse_int": "parse_int",
        "argc": "cmdline",
        "argv": "cmdline",
        "getenv": "cmdline",
        "arena_create": "arena",
        "arena_alloc": "arena",
        "arena_reset": "arena",
        "arena_destroy": "arena",
        "arena_used": "arena",
        "arena_remaining": "arena",
        "arena_use": "arena",
        "mem_used": "memory",
        "thread_id": "threading_utils",
        "num_cpus": "threading_utils",
        "yield_thread": "threading_utils",
        "sleep_ms": "threading_utils",
        "split_len": "split_ints",
        "split_get": "split_ints",
        "split_str_get": "split",
        "system": "system",
        "process_capture": "system",
        "syscall": "syscall",
        **{name: "process" for name in _PROCESS_HELPER_NAMES},
        "errno_get": "status",
        "errno_clear": "status",
        "errno_set": "status",
        "tcp_connect": "sockets",
        "tcp_listen": "sockets",
        "tcp_accept": "sockets",
        "tcp_recv": "sockets",
        "tcp_send": "sockets",
        "tcp_close": "sockets",
        **{name: "win32" for name in _WIN32_HELPER_NAMES},
    }

    # Large proof helpers live in helper_scanner_string_array.py.
    _array_access_literal_proven = _array_access_literal_proven
    _cached_strlen_field_arg = _cached_strlen_field_arg
    _class_field_is_string = _class_field_is_string
    _field_assign_targets_string = _field_assign_targets_string
    _is_integer_type_name = _is_integer_type_name
    _known_array_len_hint = _known_array_len_hint
    _literal_char_at_length_proven = _literal_char_at_length_proven
    _scan_methodcall_hidden_string_lengths = _scan_methodcall_hidden_string_lengths
    _scan_newexpr_hidden_string_lengths = _scan_newexpr_hidden_string_lengths
    _scan_streq_slice_fastpath = _scan_streq_slice_fastpath
    _str_arg_is_known_integer = _str_arg_is_known_integer
    _virtual_concat_numeric_arg = _virtual_concat_numeric_arg
    _virtual_strlen_numeric_arg = _virtual_strlen_numeric_arg

    def __init__(
        self,
        functions: dict[str, tuple[list[str], str]],
        array_vars: set[str],
        dict_vars: set[str],
        dyn_array_vars: set[str],
        classes: dict[str, Any],
        is_owned_string_alloc: Callable[[A.ASTNode], bool],
        is_string_expr: Callable[[A.ASTNode], bool],
        can_elide_binary_safety: Callable[[A.BinaryOp, str | None], bool] | None = None,
    ) -> None:
        self._functions = functions
        self._array_vars = array_vars
        self._dict_vars = dict_vars
        self._dyn_array_vars = dyn_array_vars
        self.classes = classes
        self._is_owned_string_alloc = is_owned_string_alloc
        self._is_string_expr = is_string_expr
        self._can_elide_binary_safety = can_elide_binary_safety
        # Output state, populated during run().
        self._needs = RuntimeNeeds()
        # Tracks @unchecked decorator scope across nested function walks.
        self._scanning_unchecked = False
        self._func_scope: str | None = None
        self._current_class: str | None = None
        self._local_types: dict[str, str] = {}
        self._length_only_string_locals: set[str] = set()
        self._array_len_hints: dict[tuple[str | None, str], int] = {}
        self._fixed_dict_literal_slots: dict[str, dict[str, int]] = {}

    def run(self, nodes: list[A.ASTNode]) -> RuntimeNeeds:
        """Walk every top-level node and return the populated RuntimeNeeds."""
        for node in nodes:
            self._scan_node(node)
        return self._needs

    # ==================== node-type dispatch ====================

    def _scan_node(self, node: A.ASTNode) -> None:
        """Recursively scan one AST node, recording any helpers it implies."""
        if isinstance(node, A.Call):
            self._scan_call(node)
            return
        if isinstance(node, A.BinaryOp):
            self._scan_binary_op(node)
            return
        if isinstance(node, A.InterpolatedString):
            self._scan_interp_string(node)
            return
        if isinstance(node, A.Function):
            decorators = [
                d.lstrip("@").lower() for d in getattr(node, "decorators", [])
            ]
            was_unchecked = self._scanning_unchecked
            was_scope = self._func_scope
            was_local_types = self._local_types.copy()
            was_length_only = self._length_only_string_locals.copy()
            was_fixed_dicts = dict(self._fixed_dict_literal_slots)
            self._scanning_unchecked = "unchecked" in decorators
            self._func_scope = node.name
            self._local_types = {}
            return_type_text = A.parsed_type_to_str(node.return_type)
            if info_for_ailang(return_type_text) is not None:
                self._needs.wide_ints = True
            if return_type_text == "unbounded":
                self._needs.helpers.add("bigint")
                self._needs.wide_ints = True
            for param in node.params or []:
                if isinstance(param, tuple) and len(param) >= 2:
                    ptype = A.parsed_type_to_str(param[1])
                    self._local_types[str(param[0])] = ptype
                    if info_for_ailang(ptype) is not None:
                        self._needs.wide_ints = True
                    if ptype == "unbounded":
                        self._needs.helpers.add("bigint")
                        self._needs.wide_ints = True
            self._collect_local_type_hints(node.body)
            self._length_only_string_locals = collect_length_only_string_locals(
                self, node.body, self._local_types
            )
            self._fixed_dict_literal_slots = fixed_dict_literal_slots(
                node.body, self._dict_vars
            )
            for stmt in node.body:
                self._scan_node(stmt)
            self._func_scope = was_scope
            self._local_types = was_local_types
            self._length_only_string_locals = was_length_only
            self._fixed_dict_literal_slots = was_fixed_dicts
            self._scanning_unchecked = was_unchecked
            return
        if isinstance(node, A.If):
            self._scan_if(node)
            return
        if isinstance(node, A.While):
            self._scan_node(node.cond)
            for stmt in node.body:
                self._scan_node(stmt)
            return
        if isinstance(node, A.For):
            self._scan_for(node)
            return
        if isinstance(node, A.Foreach):
            self._scan_foreach(node)
            return
        if isinstance(node, A.Loop):
            for stmt in node.body:
                self._scan_node(stmt)
            return
        if isinstance(node, A.Repeat):
            self._scan_node(node.count)
            for stmt in node.body:
                self._scan_node(stmt)
            return
        if isinstance(node, A.DoWhile):
            for stmt in node.body:
                self._scan_node(stmt)
            self._scan_node(node.cond)
            return
        if isinstance(node, A.TryExcept):
            self._scan_try_except(node)
            return
        if isinstance(node, A.Return):
            if node.value:
                if isinstance(node.value, A.Call) and node.value.name in {
                    "split_str_get",
                    "str_array_get",
                }:
                    # C return lowering may need to materialize a borrowed
                    # element before dropping its local owner.
                    self._needs.helpers.add("strcat")
                self._scan_node(node.value)
            return
        if isinstance(node, A.Assign):
            if self._scan_length_only_string_assignment(node.var_name, node.value):
                return
            if self._scan_fixed_dict_literal_assignment(node.var_name, node.value):
                return
            self._scan_node(node.value)
            if isinstance(node.value, A.Call) and node.value.name == "str":
                self._needs.helpers.add("i64_decimal_len")
            if isinstance(node.value, A.ArrayLit):
                self._needs.arrays = True
                self._array_len_hints[(self._func_scope, node.var_name)] = len(
                    node.value.elements
                )
            else:
                self._array_len_hints.pop((self._func_scope, node.var_name), None)
            return
        if isinstance(node, A.VarDecl):
            declared_type = A.parsed_type_to_str(node.type_name)
            self._local_types[node.var_name] = declared_type
            if info_for_ailang(declared_type) is not None:
                self._needs.wide_ints = True
            if declared_type == "unbounded":
                self._needs.helpers.add("bigint")
                self._needs.wide_ints = True
            if node.init_value:
                if self._scan_length_only_string_assignment(
                    node.var_name, node.init_value
                ):
                    return
                if self._scan_fixed_dict_literal_assignment(
                    node.var_name, node.init_value
                ):
                    return
                self._scan_node(node.init_value)
                if (
                    isinstance(node.init_value, A.Call)
                    and node.init_value.name == "str"
                ):
                    self._needs.helpers.add("i64_decimal_len")
                if isinstance(node.init_value, A.ArrayLit):
                    self._needs.arrays = True
                    self._array_len_hints[(self._func_scope, node.var_name)] = len(
                        node.init_value.elements
                    )
                else:
                    self._array_len_hints.pop((self._func_scope, node.var_name), None)
            return
        if isinstance(node, A.ArrayLit):
            self._needs.arrays = True
            for elem in node.elements:
                self._scan_node(elem)
            return
        if isinstance(node, A.StringSlice):
            self._needs.helpers.add("substr")
            self._scan_node(node.target)
            self._scan_node(node.start)
            if node.end:
                self._scan_node(node.end)
            return
        if isinstance(node, A.UnaryOp):
            self._scan_node(node.operand)
            return
        if isinstance(node, A.TernaryOp):
            self._scan_node(node.cond)
            self._scan_node(node.true_expr)
            self._scan_node(node.false_expr)
            return
        if isinstance(node, A.ArrayAccess):
            self._scan_array_access(node)
            return
        if isinstance(node, A.FieldAccess):
            self._scan_node(node.object_expr)
            return
        if isinstance(node, A.FieldAssign):
            self._scan_node(node.object_expr)
            self._scan_node(node.value)
            if self._field_assign_targets_string(node):
                self._needs.helpers.add("strlen")
                if self._virtual_concat_numeric_arg(node.value) is not None:
                    self._needs.helpers.add("i64_decimal_len")
            return
        if isinstance(node, A.NewExpr):
            if node.type_name in self.classes:
                self._scan_newexpr_hidden_string_lengths(node)
            for arg in node.args:
                self._scan_node(arg)
            return
        if isinstance(node, A.Cast):
            if info_for_ailang(A.parsed_type_to_str(node.target_type)) is not None:
                self._needs.wide_ints = True
            self._scan_node(node.expr)
            return
        if isinstance(node, A.Assert):
            self._scan_node(node.condition)
            if node.message:
                self._scan_node(node.message)
            return
        if isinstance(node, A.DictLit):
            self._needs.dicts = True
            self._needs.helpers.add("dict")
            for key, val in node.pairs:
                self._scan_node(key)
                self._scan_node(val)
            return
        if isinstance(node, A.DictAccess):
            if self._is_fixed_dict_expr(node.dict_expr):
                self._scan_node(node.key_expr)
                return
            self._needs.dicts = True
            self._needs.helpers.add("dict")
            self._scan_node(node.dict_expr)
            self._scan_node(node.key_expr)
            return
        if isinstance(node, A.DictAssign):
            self._scan_dict_assign(node)
            return
        if isinstance(node, A.Match):
            self._scan_match(node)
            return
        if isinstance(node, A.ListComprehension):
            self._needs.dynamic_arrays = True
            self._needs.helpers.add("dynamic_array")
            self._scan_node(node.expr)
            self._scan_node(node.iterable)
            if node.condition:
                self._scan_node(node.condition)
            return
        if isinstance(node, A.ComptimeExpr):
            self._scan_node(node.expr)
            return
        if isinstance(node, A.ComptimeBlock):
            for stmt in node.body:
                self._scan_node(stmt)
            return
        if isinstance(node, A.ComptimeIf):
            self._scan_node(node.cond)
            for stmt in node.then_body:
                self._scan_node(stmt)
            if node.else_body:
                for stmt in node.else_body:
                    self._scan_node(stmt)
            return
        if isinstance(node, A.MethodCall):
            self._scan_methodcall_hidden_string_lengths(node)
            self._scan_node(node.object_expr)
            for arg in node.args:
                self._scan_node(arg)
            return
        if isinstance(node, A.ClassDef):
            for field in node.fields:
                if not isinstance(field, tuple) or len(field) < 3:
                    continue
                _visibility, _field_name, field_type = field[:3]
                if info_for_ailang(A.parsed_type_to_str(field_type)) is not None:
                    self._needs.wide_ints = True
                kind = auto_owned_field_kind(field_type, self.classes)
                if kind == "dict":
                    self._needs.dicts = True
                    self._needs.helpers.add("dict")
            was_class = self._current_class
            self._current_class = node.name
            for method in node.methods:
                self._scan_node(method)
            self._current_class = was_class
            return
        # Threading, atomics, channels, inline asm fall through here.
        self._scan_concurrency(node)

    # ==================== specific node families ====================

    def _collect_local_type_hints(self, body: list[A.ASTNode]) -> None:
        def infer_expr_type(node: Any) -> str | None:
            if isinstance(node, A.Number):
                return None if node.is_float else "int64_t"
            if isinstance(node, A.StringLit):
                return "string"
            if isinstance(node, A.Call):
                if node.name in {"str", "hex", "bin", "oct"}:
                    return "string"
                signature = self._functions.get(node.name)
                if signature is not None:
                    _params, ret_type = signature
                    return ret_type
                return None
            if isinstance(node, A.UnaryOp):
                return infer_expr_type(node.operand)
            if isinstance(node, A.BinaryOp):
                left = infer_expr_type(node.left)
                right = infer_expr_type(node.right)
                if (
                    left is not None
                    and right is not None
                    and (
                        self._is_integer_type_name(left)
                        and self._is_integer_type_name(right)
                    )
                ):
                    return "int64_t"
            return None

        def nested_bodies(node: Any) -> list[list[A.ASTNode]]:
            out: list[list[A.ASTNode]] = []
            for attr in ("body", "then_body", "else_body", "try_body", "finally_block"):
                value = getattr(node, attr, None)
                if isinstance(value, list):
                    out.append(value)
            for _cond, branch in getattr(node, "elsif_branches", []) or []:
                if isinstance(branch, list):
                    out.append(branch)
            for item in getattr(node, "cases", []) or []:
                if isinstance(item, tuple) and len(item) >= 2:
                    _case_label, case_branch, *_rest = item
                    if isinstance(case_branch, list):
                        out.append(case_branch)
            return out

        def visit(nodes: list[A.ASTNode]) -> None:
            for item in nodes:
                if isinstance(item, A.VarDecl):
                    self._local_types[item.var_name] = A.parsed_type_to_str(
                        item.type_name
                    )
                elif (
                    isinstance(item, A.Assign)
                    and item.var_name not in self._local_types
                ):
                    inferred = infer_expr_type(item.value)
                    if inferred is not None:
                        self._local_types[item.var_name] = inferred
                for nested in nested_bodies(item):
                    visit(nested)

        visit(body)

    def _scan_length_only_string_assignment(
        self, var_name: str, value: A.ASTNode
    ) -> bool:
        if var_name not in self._length_only_string_locals:
            return False
        str_arg = str_known_integer_arg(self, value, self._local_types)
        if str_arg is not None:
            self._needs.helpers.add("i64_decimal_len")
            self._scan_node(str_arg)
            return True
        base_arg = baseconv_known_integer_arg(self, value, self._local_types)
        if base_arg is not None:
            _kind, arg = base_arg
            self._needs.helpers.add("base_conv_len")
            self._scan_node(arg)
            return True
        if interpolation_known_length(self, value, self._local_types):
            for part in getattr(value, "parts", []):
                if isinstance(part, str):
                    continue
                str_part = str_known_integer_arg(self, part, self._local_types)
                if str_part is not None:
                    self._needs.helpers.add("i64_decimal_len")
                    self._scan_node(str_part)
                    continue
                base_part = baseconv_known_integer_arg(self, part, self._local_types)
                if base_part is not None:
                    _kind, arg = base_part
                    self._needs.helpers.add("base_conv_len")
                    self._scan_node(arg)
                    continue
                if isinstance(part, A.Variable):
                    kind = self._local_types.get(part.name)
                    if kind is not None and self._is_integer_type_name(kind):
                        self._needs.helpers.add("i64_decimal_len")
                        self._scan_node(part)
            return True
        return False

    _scan_concurrency = _m_scan_concurrency
    _scan_channel = _m_scan_channel

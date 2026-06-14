"""Walk an AST and produce a RuntimeNeeds snapshot.

The scanner owns helper-category detection for CTranspiler. It receives its
inputs explicitly, returns a RuntimeNeeds value, and avoids mutating caller
state directly.
"""

from __future__ import annotations

from parser import ast as A
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple

from ast_access import arg_at
from transpiler.arithmetic_literal_proofs import (
    literal_int_arithmetic_safe,
    neutral_int_arithmetic_safe,
    positive_int_literal,
    shift_amount_literal_in_range,
)
from transpiler.class_field_ownership import auto_owned_field_kind
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


class HelperScanner:
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
    _CALL_HELPER_MAP: ClassVar[Dict[str, str]] = {
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
        functions: Dict[str, Tuple[List[str], str]],
        array_vars: Set[str],
        dict_vars: Set[str],
        dyn_array_vars: Set[str],
        classes: Dict[str, Any],
        is_owned_string_alloc: Callable[[A.ASTNode], bool],
        is_string_expr: Callable[[A.ASTNode], bool],
        can_elide_binary_safety: Optional[
            Callable[[A.BinaryOp, Optional[str]], bool]
        ] = None,
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
        self._func_scope: Optional[str] = None
        self._current_class: Optional[str] = None
        self._local_types: Dict[str, str] = {}
        self._array_len_hints: Dict[Tuple[Optional[str], str], int] = {}

    def run(self, nodes: List[A.ASTNode]) -> RuntimeNeeds:
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
            self._scanning_unchecked = "unchecked" in decorators
            self._func_scope = node.name
            self._local_types = {}
            for param in node.params or []:
                if isinstance(param, tuple) and len(param) >= 2:
                    self._local_types[str(param[0])] = A.parsed_type_to_str(param[1])
            for stmt in node.body:
                self._scan_node(stmt)
            self._func_scope = was_scope
            self._local_types = was_local_types
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
                self._scan_node(node.value)
            return
        if isinstance(node, A.Assign):
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
            self._local_types[node.var_name] = A.parsed_type_to_str(node.type_name)
            if node.init_value:
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

    def _scan_call(self, node: A.Call) -> None:
        name = node.name
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

    def _scan_binary_op(self, node: A.BinaryOp) -> None:
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

    def _scan_interp_string(self, node: A.InterpolatedString) -> None:
        self._needs.helpers.add("strcat")
        self._needs.helpers.add("int_to_str")
        for part in node.parts:
            if not isinstance(part, str):
                self._scan_node(part)

    def _scan_if(self, node: A.If) -> None:
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

    def _scan_for(self, node: A.For) -> None:
        if node.init:
            self._scan_node(node.init)
        if node.cond:
            self._scan_node(node.cond)
        if node.step:
            self._scan_node(node.step)
        for stmt in node.body:
            self._scan_node(stmt)

    def _scan_foreach(self, node: A.Foreach) -> None:
        self._scan_node(node.iterable)
        if not isinstance(node.iterable, A.Range):
            self._needs.arrays = True
        for stmt in node.body:
            self._scan_node(stmt)

    def _scan_array_access(self, node: A.ArrayAccess) -> None:
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

    def _scan_dict_assign(self, node: A.DictAssign) -> None:
        # Dict helper only fires when the target is a known dict variable;
        # `obj.field[idx] = val` is array-style, no dict helper needed.
        if (
            isinstance(node.dict_expr, A.Variable)
            and node.dict_expr.name in self._dict_vars
        ):
            self._needs.dicts = True
            self._needs.helpers.add("dict")
        self._scan_node(node.dict_expr)
        self._scan_node(node.key_expr)
        self._scan_node(node.value_expr)

    def _scan_match(self, node: A.Match) -> None:
        self._scan_node(node.expr)
        for case_val, case_body in node.cases:
            if not isinstance(case_val, A.MatchPattern):
                self._scan_node(case_val)
            for stmt in case_body:
                self._scan_node(stmt)
        if node.default_case:
            for stmt in node.default_case:
                self._scan_node(stmt)

    def _scan_try_except(self, node: A.TryExcept) -> None:
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

    _scan_concurrency = _m_scan_concurrency
    _scan_channel = _m_scan_channel

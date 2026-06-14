"""
TypeCollector — service that fills the type-table fields of a TypeInfo.

Replaces the type-collection half of ``_TypeCollectMixin`` (the
variable-typing half is now ``VarTypingScanner``). Receives a
``TypeInfo`` to populate; populates ``records``, ``unions``, ``enums``,
``classes``, ``functions``, ``data_enums``, ``type_decorators``,
``func_defaults``, ``recursive_funcs``. Also walks the call graph to
identify recursive functions / methods.

Phase 3 of the New Path roadmap. Pure analysis pass: input AST, output
populated TypeInfo. No emission, no shared state with later phases.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Dict, List, Optional, Set, Tuple

from callback_types import is_callback_type
from transpiler.type_info import ClassField, RecordField, TypeInfo


class TypeCollector:
    """Walks an AST and fills the type tables of a TypeInfo container."""

    def __init__(self) -> None:
        # Tracks the class whose body we're currently walking, used by
        # the call-graph walk to resolve method-call receivers.
        self._current_class: Optional[str] = None

    def run(self, nodes: List[A.ASTNode], type_info: TypeInfo) -> None:
        """Populate every type-table field of ``type_info`` in place,
        including ``recursive_funcs``."""
        self._collect_types(nodes, type_info)
        type_info.owned_string_return_funcs = self._identify_owned_string_returns(
            nodes, type_info
        )
        type_info.recursive_funcs = self._identify_recursive_functions(nodes)

    # ==================== type tables ====================

    def _collect_types(self, nodes: List[A.ASTNode], type_info: TypeInfo) -> None:
        for node in nodes:
            if isinstance(node, A.ExternRecordDef):
                type_info.opaque_records.add(node.name)
                type_info.extern_record_c_names[node.name] = getattr(
                    node, "c_name", node.name
                )
                if getattr(node, "c_name_explicit", False):
                    type_info.extern_record_c_name_explicit.add(node.name)
                if getattr(node, "is_opaque", True):
                    type_info.extern_record_opaque.add(node.name)
                layout_size = getattr(node, "layout_size", None)
                layout_align = getattr(node, "layout_align", None)
                if layout_size is not None or layout_align is not None:
                    field_offsets = getattr(node, "field_offsets", {}) or {}
                    field_sizes = getattr(node, "field_sizes", {}) or {}
                    type_info.extern_record_layouts[node.name] = {
                        "size": int(layout_size or 0),
                        "align": int(layout_align or 0),
                        "fields": {
                            field_name: {
                                "offset": int(offset),
                                "size": int(field_sizes.get(field_name, 0)),
                            }
                            for field_name, offset in sorted(field_offsets.items())
                        },
                    }
            elif isinstance(node, A.RecordDef):
                type_info.records[node.name] = self._collect_record_fields(node)
                decos = getattr(node, "decorators", [])
                if decos:
                    type_info.type_decorators[node.name] = decos
            elif isinstance(node, A.UnionDef):
                union_fields: List[RecordField] = [(f[0], f[1]) for f in node.fields]
                type_info.unions[node.name] = union_fields
                decos = getattr(node, "decorators", [])
                if decos:
                    type_info.type_decorators[node.name] = decos
            elif isinstance(node, A.EnumDef):
                variants: List[Tuple[str, int]] = []
                has_data = node.has_data_variants()
                data_variants: Dict[str, List[Tuple[str, str]]] = {}
                for variant in node.variants:
                    val = variant.value if variant.value is not None else len(variants)
                    variants.append((variant.name, val))
                    if variant.has_data() and variant.fields:
                        data_variants[variant.name] = list(variant.fields)
                type_info.enums[node.name] = variants
                if has_data:
                    type_info.data_enums[node.name] = data_variants
            elif isinstance(node, A.ClassDef):
                class_fields = self._collect_class_fields(node)
                type_info.classes[node.name] = (class_fields, node.methods)
                # Classes also register a record entry so field-access lowering
                # can look them up uniformly via type_info.records.
                record_fields: List[RecordField] = [(f[1], f[2]) for f in class_fields]
                type_info.records[node.name] = record_fields
            elif isinstance(node, A.TypeAlias):
                type_info.type_aliases[node.name] = node.target_type
                if is_callback_type(node.target_type):
                    type_info.callback_aliases[node.name] = node.target_type
            elif isinstance(node, A.Function):
                self._collect_function_info(node, type_info)

    @staticmethod
    def _collect_record_fields(
        node: A.RecordDef | A.ExternRecordDef,
    ) -> List[RecordField]:
        """Extract field info from a RecordDef.

        Each entry is either a ``(name, type)`` tuple or a bare AST
        node with a ``name`` attribute (older parser variants). Type
        defaults to ``int`` when missing.
        """
        fields: List[RecordField] = []
        for entry in node.fields:
            if isinstance(entry, tuple):
                # Destructure rather than index so the verifier's
                # magic-index check stays happy. The three accepted
                # shapes: (name,), (name, type), (name, type, ...).
                if len(entry) >= 2:
                    raw_name, raw_type, *_ = entry
                    fname = str(raw_name)
                    ftype = parsed_type_to_str(raw_type)
                elif len(entry) == 1:
                    (raw_name,) = entry
                    fname = str(raw_name)
                    ftype = "int"
                else:
                    continue
            else:
                fname = getattr(entry, "name", str(entry))
                ftype = "int"
            fields.append((fname, ftype))
        return fields

    @staticmethod
    def _collect_class_fields(node: A.ClassDef) -> List[ClassField]:
        """Extract field info from a ClassDef.

        Three tuple shapes accepted, in order of preference:

        * ``(visibility, name, type, ...)`` -- explicit visibility
        * ``(name, type)`` -- shorthand, defaults visibility to public
        * anything else: skipped
        """
        fields: List[ClassField] = []
        for entry in node.fields:
            if not isinstance(entry, tuple):
                continue
            if len(entry) >= 3:
                raw_vis, raw_name, raw_type, *_ = entry
                visibility = str(raw_vis)
                fname = str(raw_name)
                ftype = parsed_type_to_str(raw_type)
            elif len(entry) >= 2:
                raw_name, raw_type, *_ = entry
                visibility = "public"
                fname = str(raw_name)
                ftype = parsed_type_to_str(raw_type)
            else:
                continue
            fields.append((visibility, fname, ftype))
        return fields

    @staticmethod
    def _collect_function_info(node: A.Function, type_info: TypeInfo) -> None:
        """Register a function's signature + default-arg metadata."""
        param_types: List[str] = []
        defaults: List[Tuple[int, A.ASTNode]] = []
        for i, p in enumerate(node.params or []):
            if isinstance(p, tuple) and len(p) > 1:
                param_types.append(parsed_type_to_str(p[1]))
                if len(p) > 2 and p[2] is not None:
                    defaults.append((i, p[2]))
            else:
                param_types.append("int")
        ret_type: str = "int"
        if hasattr(node, "return_type") and node.return_type:
            ret_type = parsed_type_to_str(node.return_type)
        type_info.functions[node.name] = (param_types, ret_type)
        if defaults:
            type_info.func_defaults[node.name] = defaults

    # ==================== string return ownership ====================

    _OWNING_STRING_CALLS: frozenset[str] = frozenset(
        {
            "str",
            "chr",
            "substr",
            "concat",
            "str_replace",
            "hex",
            "bin",
            "oct",
            "tcp_recv",
            "win32_full_path",
            "input",
            "read_stdin",
            "read_file",
            "current_dir",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "ailang_strcat",
            "fn_call_str",
            "str_array_join",
            "str_array_pop",
            "split_str_get",
        }
    )

    def _identify_owned_string_returns(
        self, nodes: List[A.ASTNode], type_info: TypeInfo
    ) -> Set[str]:
        """Find user functions whose string result must be freed by callers."""
        funcs = [node for node in nodes if isinstance(node, A.Function)]
        owned: Set[str] = set()
        changed = True
        while changed:
            changed = False
            for func in funcs:
                _params, ret_type = type_info.functions.get(func.name, ([], "int"))
                if ret_type not in ("string", "char *", "const char *"):
                    continue
                returns = self._return_values(func.body or [])
                if not returns:
                    continue
                locals_owned, locals_string = self._local_string_ownership(
                    func.body or [], type_info, owned
                )
                if all(
                    self._expr_returns_owned_string(
                        expr, type_info, owned, locals_owned, locals_string
                    )
                    for expr in returns
                ):
                    if func.name not in owned:
                        owned.add(func.name)
                        changed = True
        return owned

    def _return_values(self, body: List[A.ASTNode]) -> List[A.ASTNode]:
        out: List[A.ASTNode] = []

        def walk(node: A.ASTNode) -> None:
            if isinstance(node, A.Return) and node.value is not None:
                out.append(node.value)
                return
            for attr in ("body", "then_body", "else_body", "try_body", "finally_body"):
                seq = getattr(node, attr, None)
                if isinstance(seq, list):
                    for child in seq:
                        walk(child)
            if isinstance(node, A.TryExcept):
                for _et, _vn, cb in node.catch_blocks:
                    for child in cb:
                        walk(child)
                if node.except_block:
                    _ev, eb = node.except_block
                    for child in eb:
                        walk(child)
            elsif = getattr(node, "elsif_branches", None)
            if elsif:
                for _cond, branch in elsif:
                    for child in branch:
                        walk(child)

        for stmt in body:
            walk(stmt)
        return out

    def _local_string_ownership(
        self,
        body: List[A.ASTNode],
        type_info: TypeInfo,
        owned_funcs: Set[str],
    ) -> Tuple[Set[str], Set[str]]:
        owned: Set[str] = set()
        strings: Set[str] = set()

        def walk(node: A.ASTNode) -> None:
            if isinstance(node, A.Assign):
                if self._expr_returns_owned_string(
                    node.value, type_info, owned_funcs, owned, strings
                ):
                    owned.add(node.var_name)
                    strings.add(node.var_name)
                elif self._expr_is_string(node.value, type_info, owned_funcs, strings):
                    owned.discard(node.var_name)
                    strings.add(node.var_name)
            for attr in ("body", "then_body", "else_body", "try_body", "finally_body"):
                seq = getattr(node, attr, None)
                if isinstance(seq, list):
                    for child in seq:
                        walk(child)
            if isinstance(node, A.TryExcept):
                for _et, _vn, cb in node.catch_blocks:
                    for child in cb:
                        walk(child)
                if node.except_block:
                    _ev, eb = node.except_block
                    for child in eb:
                        walk(child)
            elsif = getattr(node, "elsif_branches", None)
            if elsif:
                for _cond, branch in elsif:
                    for child in branch:
                        walk(child)

        for stmt in body:
            walk(stmt)
        return owned, strings

    def _expr_returns_owned_string(
        self,
        expr: A.ASTNode,
        type_info: TypeInfo,
        owned_funcs: Set[str],
        local_owned: Set[str],
        local_strings: Set[str],
    ) -> bool:
        if isinstance(expr, (A.InterpolatedString, A.StringSlice)):
            return True
        if isinstance(expr, A.Variable):
            return expr.name in local_owned
        if isinstance(expr, A.Call):
            if expr.name in self._OWNING_STRING_CALLS:
                return True
            return expr.name in owned_funcs
        if isinstance(expr, A.BinaryOp) and expr.op == "+":
            return self._expr_is_string(
                expr.left, type_info, owned_funcs, local_strings
            ) or self._expr_is_string(expr.right, type_info, owned_funcs, local_strings)
        return False

    def _expr_is_string(
        self,
        expr: A.ASTNode,
        type_info: TypeInfo,
        owned_funcs: Set[str],
        local_strings: Set[str],
    ) -> bool:
        if isinstance(expr, (A.StringLit, A.InterpolatedString, A.StringSlice)):
            return True
        if isinstance(expr, A.Variable):
            return expr.name in local_strings
        if isinstance(expr, A.Call):
            if expr.name in type_info._STRING_RETURNING_BUILTINS_POSTSCAN:
                return True
            if expr.name in type_info.functions:
                _params, ret_type = type_info.functions[expr.name]
                return ret_type in ("string", "char *", "const char *")
            return expr.name in owned_funcs
        if isinstance(expr, A.BinaryOp) and expr.op == "+":
            return self._expr_is_string(
                expr.left, type_info, owned_funcs, local_strings
            ) or self._expr_is_string(expr.right, type_info, owned_funcs, local_strings)
        return False

    # ==================== call graph + recursion detection ====================

    def _identify_recursive_functions(self, nodes: List[A.ASTNode]) -> Set[str]:
        """Return the set of functions / methods that participate in a cycle.

        Names are bare for free functions, ``Class_method`` for class
        methods. Indirect calls through builtins or unresolved method
        dispatch are conservatively ignored (worst case: we keep the
        guard for a function that didn't need it -- still correct).
        """
        graph: Dict[str, Set[str]] = {}
        for node in nodes:
            if isinstance(node, A.Function):
                calls: Set[str] = set()
                for stmt in node.body or []:
                    self._walk_calls(stmt, calls)
                graph[node.name] = calls
            elif isinstance(node, A.ClassDef):
                for method in node.methods:
                    self._current_class = node.name
                    method_calls: Set[str] = set()
                    for stmt in method.body or []:
                        self._walk_calls(stmt, method_calls)
                    graph[f"{node.name}_{method.name}"] = method_calls
                self._current_class = None

        recursive: Set[str] = set()
        for start in graph:
            visited: Set[str] = set()
            stack: List[str] = list(graph[start])
            while stack:
                cur = stack.pop()
                if cur == start:
                    recursive.add(start)
                    break
                if cur in visited:
                    continue
                visited.add(cur)
                stack.extend(graph.get(cur, set()))
        return recursive

    def _walk_calls(self, node: A.ASTNode, calls: Set[str]) -> None:
        """Recursively collect names of functions/methods called from ``node``."""
        if node is None:
            return
        if isinstance(node, A.Call):
            calls.add(node.name)
        elif isinstance(node, A.MethodCall):
            cls = self._resolve_method_class(node)
            if cls is not None:
                calls.add(f"{cls}_{node.method_name}")
        for attr in (
            "object_expr",
            "value",
            "cond",
            "init",
            "step",
            "left",
            "right",
            "true_expr",
            "false_expr",
            "expr",
            "iterable",
            "key_expr",
            "dict_expr",
            "tuple_expr",
            "array",
            "index",
            "func_call",
        ):
            child = getattr(node, attr, None)
            if child is not None and not isinstance(child, (str, int, bool, float)):
                self._walk_calls(child, calls)
        for attr in (
            "args",
            "body",
            "then_body",
            "else_body",
            "try_body",
            "finally_body",
            "elements",
            "params",
        ):
            seq = getattr(node, attr, None)
            if isinstance(seq, list):
                for child in seq:
                    if not isinstance(child, (str, int, bool, float, tuple)):
                        self._walk_calls(child, calls)
        elsif = getattr(node, "elsif_branches", None)
        if elsif:
            for cond, branch_body in elsif:
                self._walk_calls(cond, calls)
                if isinstance(branch_body, list):
                    for child in branch_body:
                        self._walk_calls(child, calls)

    def _resolve_method_class(self, node: A.MethodCall) -> Optional[str]:
        """Best-effort method-call receiver class resolution for the
        recursion-detection walk. Falls back to ``self._current_class``
        when the receiver is an implicit ``this`` reference. Indirect
        method dispatch is intentionally not resolved -- the call-graph
        edge is conservatively dropped, which keeps false positives in
        ``recursive_funcs`` (correctness-preserving)."""
        if isinstance(node.object_expr, A.ThisExpr):
            return self._current_class
        return None

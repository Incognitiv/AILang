"""CTranspiler C type lowering, declarations, and usage collection."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any, Dict, List, Optional, Set, Tuple

from callback_types import resolve_callback_alias
from transpiler.fixed_array_types import parse_fixed_array_type_spec
from transpiler.strlen_assign_cache import collect_strlen_cache_var

ClassField = Tuple[str, str, str]
RecordField = Tuple[str, str]


class _CTranspilerTypeLoweringMixin:
    def _resolve_type_alias_spec(self: Any, atype: str) -> str:
        """Resolve chained user type aliases to a canonical spec string.

        Range aliases remain runtime-checked integers on the C side.
        """
        spec = parsed_type_to_str(atype).strip()
        if not spec:
            return spec
        aliases = getattr(self, "_type_aliases", None)
        if not isinstance(aliases, dict):
            return spec
        seen: Set[str] = set()
        current = spec
        while isinstance(current, str) and current in aliases and current not in seen:
            seen.add(current)
            target = aliases[current]
            if resolve_callback_alias(current, aliases) is not None:
                return current
            # Range aliases lower to integer storage; constraints are
            # enforced by runtime checks emitted at assignment sites.
            if isinstance(target, A.RangeType):
                return "int"
            current = parsed_type_to_str(target).strip()
            if not current:
                break
        return current or spec

    def _looks_like_c_type(self: Any, atype: str) -> bool:
        spec = atype.strip()
        if not spec:
            return False
        if spec.startswith("slice[") or spec.startswith("view["):
            return False
        if spec.startswith("[") and spec.endswith("]"):
            return False
        if "*" in spec:
            return True
        if spec.startswith(("const ", "unsigned ", "signed ", "struct ", "union ")):
            return True
        if spec.startswith("ailang_"):
            return True
        if spec.endswith("_t"):
            return True
        return spec in {"void", "bool", "char", "float", "double", "long double"}

    def _parse_fixed_array_type_spec(
        self: Any, atype: str
    ) -> Optional[Tuple[str, int]]:
        """Parse canonical fixed-array type string: ``[elem;N]``."""
        return parse_fixed_array_type_spec(atype)

    def _format_c_declaration(self: Any, atype: str, name: str) -> str:
        """Format ``type name`` with C declarator-aware fixed-array support."""
        atype_str = self._resolve_type_alias_spec(atype)
        if self._looks_like_c_type(atype_str):
            return f"{atype_str} {name}"
        fixed = self._parse_fixed_array_type_spec(atype_str)
        if fixed is not None:
            elem_type, size = fixed
            c_elem = self._ailang_type_to_c(elem_type)
            return f"{c_elem} {name}[{size}]"
        return f"{self._ailang_type_to_c(atype_str)} {name}"

    def _format_c_param_declaration(
        self: Any,
        atype: str,
        name: str,
        use_restrict: bool = False,
    ) -> str:
        """Format parameter declarations.

        Fixed arrays and slice/view types decay to pointer params.
        """
        atype_str = self._resolve_type_alias_spec(atype)
        if self._looks_like_c_type(atype_str):
            ctype = atype_str
            if use_restrict and "*" in ctype:
                ctype = ctype.replace("*", "* restrict ")
            return f"{ctype} {name}"
        fixed = self._parse_fixed_array_type_spec(atype_str)
        if fixed is not None:
            elem_type, _size = fixed
            ctype = f"{self._ailang_type_to_c(elem_type)} *"
        else:
            ctype = self._ailang_type_to_c(atype_str)
        if use_restrict and "*" in ctype:
            ctype = ctype.replace("*", "* restrict ")
        return f"{ctype} {name}"

    def _ailang_type_to_c(self: Any, atype: str) -> str:
        """Convert AILang type to C type."""
        spec = self._resolve_type_alias_spec(atype)
        if self._looks_like_c_type(spec):
            return spec
        type_lower = spec.lower()
        fixed = self._parse_fixed_array_type_spec(spec)
        if fixed is not None:
            # In non-declarator positions, fixed arrays behave as pointers.
            elem_type, _size = fixed
            return f"{self._ailang_type_to_c(elem_type)} *"
        if (type_lower.startswith("slice[") and type_lower.endswith("]")) or (
            type_lower.startswith("view[") and type_lower.endswith("]")
        ):
            # Conservative representation for now: same runtime shape as array.
            return "ailang_dyn_array"
        # Complete type ladder mapping (8-bit to 8192-bit)
        type_map = {
            # 8-bit
            "tiny": "int8_t",
            "i8": "int8_t",
            "byte": "uint8_t",
            "u8": "uint8_t",
            # 16-bit
            "small": "int16_t",
            "i16": "int16_t",
            "usmall": "uint16_t",
            "u16": "uint16_t",
            # 32-bit
            "short": "int32_t",
            "i32": "int32_t",
            "ushort": "uint32_t",
            "u32": "uint32_t",
            # 64-bit
            "int": "int64_t",
            "i64": "int64_t",
            "uint": "uint64_t",
            "u64": "uint64_t",
            # 128-bit (use __int128 on supported platforms)
            "long": "__int128",
            "i128": "__int128",
            "ulong": "unsigned __int128",
            "u128": "unsigned __int128",
            # 256-bit and above: use int64_t as fallback (C doesn't have native support)
            # For real bigint support, would need a library
            "wide": "int64_t",
            "i256": "int64_t",
            "uwide": "uint64_t",
            "u256": "uint64_t",
            "vast": "int64_t",
            "i512": "int64_t",
            "uvast": "uint64_t",
            "u512": "uint64_t",
            "grand": "int64_t",
            "i1024": "int64_t",
            "ugrand": "uint64_t",
            "u1024": "uint64_t",
            "giant": "int64_t",
            "i2048": "int64_t",
            "ugiant": "uint64_t",
            "u2048": "uint64_t",
            "titan": "int64_t",
            "i4096": "int64_t",
            "utitan": "uint64_t",
            "u4096": "uint64_t",
            "colos": "int64_t",
            "i8192": "int64_t",
            "ucolos": "uint64_t",
            "u8192": "uint64_t",
            # Floating point
            "float": "float",
            "f32": "float",
            "double": "double",
            "f64": "double",
            "quad": "long double",
            "f128": "long double",
            # Other types
            "bool": "bool",
            "string": "const char *",
            "str": "const char *",
            "void": "void",
            "ptr": "void *",
            "ptrptr": "void **",
            "charpp": "char **",
            "fileptr": "FILE *",
            "size_tp": "size_t *",
            "any": "void *",
            # Dynamic collection types -- these are the C-side struct
            # names emitted by the runtime helpers in _emit_runtime_*.
            # Without these mappings, fields declared as `array` or
            # `str_array` etc. defaulted to `int64_t` and clashed with
            # the actual struct returned by array_new / str_array_new
            # at the assignment site.
            "array": "ailang_dyn_array",
            "str_array": "ailang_str_array",
            "dict": "ailang_dict *",
            # SIMD runtime typedefs (from runtime_emit_simd.py).
            "vec128": "vec128",
            "vec256": "vec256",
            "vec512": "vec512",
            # split()/split_ints() runtime structs.
            "stringarray": "StringArray",
            "intarray": "IntArray",
        }
        if type_lower in type_map:
            return type_map[type_lower]
        if resolve_callback_alias(spec, getattr(self, "_type_aliases", {})) is not None:
            return spec
        # Classes are reference types (heap-allocated, pointer semantics —
        # matches expr_generator.visit_NewExpr in the LLVM backend). Emit
        # them as pointer-to-struct so params, locals, fields and returns
        # all carry the pointer through consistently.
        if spec in self.classes:
            return f"{spec} *"
        if spec in self.type_info.opaque_records:
            return f"{spec} *"
        if spec in self.records or spec in self.unions or spec in self.enums:
            return spec
        if spec.startswith("[") and spec.endswith("]"):
            inner = spec[1:-1]
            return f"{self._ailang_type_to_c(inner)} *"
        return "int64_t"

    def _function_body_has_value_return(self: Any, body: List[A.ASTNode]) -> bool:
        """Check if any Return node in the body (recursively) has a value."""
        for node in body:
            if isinstance(node, A.Return) and node.value is not None:
                return True
            # Search nested blocks
            if isinstance(node, A.If):
                if self._function_body_has_value_return(node.then_body):
                    return True
                if node.else_body and self._function_body_has_value_return(
                    node.else_body
                ):
                    return True
                if hasattr(node, "elsif_branches") and node.elsif_branches:
                    for _, branch_body in node.elsif_branches:
                        if self._function_body_has_value_return(branch_body):
                            return True
            elif isinstance(node, (A.While, A.For, A.Foreach)):
                if self._function_body_has_value_return(node.body):
                    return True
            elif isinstance(node, A.TryExcept):
                if hasattr(node, "try_body") and self._function_body_has_value_return(
                    node.try_body
                ):
                    return True
                if hasattr(node, "handlers"):
                    for handler in node.handlers:
                        handler_body = (
                            handler.body
                            if hasattr(handler, "body")
                            else (
                                handler[2]
                                if isinstance(handler, tuple) and len(handler) > 2
                                else []
                            )
                        )
                        if handler_body and self._function_body_has_value_return(
                            handler_body
                        ):
                            return True
                if (
                    hasattr(node, "finally_body") and node.finally_body
                ) and self._function_body_has_value_return(node.finally_body):
                    return True
            elif (
                hasattr(node, "body") and isinstance(node.body, list)
            ) and self._function_body_has_value_return(node.body):
                return True
        return False

    def _get_return_type(self: Any, func: A.Function) -> str:
        """Get C return type for a function.
        Uses void for functions that have no explicit return type annotation
        and never return a value in their body.
        """
        if func.name == "main":
            return "int"
        if hasattr(func, "return_type") and func.return_type:
            return self._ailang_type_to_c(parsed_type_to_str(func.return_type))
        # No annotation — infer from body: void if no valued return
        if not self._function_body_has_value_return(func.body):
            return "void"
        return "int64_t"

    def _format_params(
        self: Any, params: Optional[List], use_restrict: bool = False
    ) -> str:
        """Format function parameters for C.
        Args:
            params: List of (name, type) tuples
            use_restrict: If True, add 'restrict' to pointer params (Fortran-style)
        """
        if not params:
            return "void"
        c_params = []
        for p in params:
            if isinstance(p, tuple):
                pname = p[0]
                ptype = p[1] if len(p) > 1 else "int"
            else:
                pname = str(p)
                ptype = "int"
            ptype_str = parsed_type_to_str(ptype)
            c_params.append(
                self._format_c_param_declaration(ptype_str, pname, use_restrict)
            )
        return ", ".join(c_params)

    def visit(self: Any, node: A.ASTNode) -> Optional[str]:
        """Visit an AST node and generate C code. Dispatches to the
        statement-emit service. The ``visit_X`` methods used to live
        on a mixin spliced into this class's MRO; they now live on
        ``CStmtEmitter`` instead and we look them up there."""
        method_name = f"visit_{type(node).__name__}"
        method = getattr(self.stmt_emitter, method_name, None)
        if method is not None:
            return method(node)
        self.emit(f"/* Unhandled AST node: {type(node).__name__} */")
        return None

    def expr(self: Any, node: A.ASTNode) -> str:
        """Generate a C expression string. Dispatches to the
        expression-emit service. ``expr()`` and the ``_expr_X`` family
        used to live on a mixin spliced into this class; they now
        live on ``CExprEmitter`` instead."""
        return self.expr_emitter.expr(node)

    def _infer_assign_type(self: Any, value: A.ASTNode) -> str:
        """Infer the C type for an assignment value."""
        if isinstance(value, A.ArrayLit):
            return "ailang_dyn_array"
        if isinstance(value, A.DictLit):
            return "ailang_dict *"
        if isinstance(value, A.ListComprehension):
            return "ailang_dyn_array"
        return self._infer_type(value)

    def _collect_var_in_stmt(
        self: Any, stmt: A.ASTNode, vars_found: Dict[str, str]
    ) -> None:
        """Collect variables from a single statement."""
        if isinstance(stmt, A.Assign):
            # Don't create local for static globals — they use module scope
            if (
                stmt.var_name in self._static_global_names
                or stmt.var_name in self.extern_vars
            ):
                return
            if stmt.var_name not in vars_found:
                # Prefer function-local inferred type when assigning
                # from another local variable. This prevents cross-function
                # `_var_types` pollution from forcing an unrelated pointer
                # type here (for example `lookups = n` becoming `MemoryNode *`
                # when another function previously used `n` as a class ptr).
                if isinstance(stmt.value, A.Variable) and stmt.value.name in vars_found:
                    vars_found[stmt.var_name] = vars_found[stmt.value.name]
                else:
                    vars_found[stmt.var_name] = self._infer_assign_type(stmt.value)
            collect_strlen_cache_var(self, stmt.var_name, stmt.value, vars_found)
            return
        if isinstance(stmt, A.TupleAssign):
            for var_name in stmt.var_names:
                if var_name not in vars_found:
                    vars_found[var_name] = "int64_t"
            return
        if isinstance(stmt, A.VarDecl):
            if stmt.var_name not in vars_found:
                if stmt.type_name:
                    vars_found[stmt.var_name] = self._resolve_type_alias_spec(
                        parsed_type_to_str(stmt.type_name)
                    )
                else:
                    vars_found[stmt.var_name] = "int64_t"
            if stmt.init_value is not None:
                collect_strlen_cache_var(
                    self, stmt.var_name, stmt.init_value, vars_found
                )
            return
        if isinstance(stmt, A.RangeVarDecl):
            # Range-constrained variables are int64_t with runtime checks
            if stmt.var_name not in vars_found:
                vars_found[stmt.var_name] = "int64_t"
            return
        if isinstance(stmt, A.If):
            self._collect_vars_in_body(stmt.then_body, vars_found)
            if stmt.else_body:
                self._collect_vars_in_body(stmt.else_body, vars_found)
            if hasattr(stmt, "elsif_branches") and stmt.elsif_branches:
                for _, elsif_body in stmt.elsif_branches:
                    self._collect_vars_in_body(elsif_body, vars_found)
            return
        if isinstance(stmt, A.Match):
            for _case_expr, case_body in stmt.cases:
                self._collect_vars_in_body(case_body, vars_found)
            if stmt.default_case:
                self._collect_vars_in_body(stmt.default_case, vars_found)
            return
        if isinstance(stmt, A.For):
            if stmt.init:
                self._collect_vars_in_body([stmt.init], vars_found)
            if stmt.step:
                self._collect_vars_in_body([stmt.step], vars_found)
            self._collect_vars_in_body(stmt.body, vars_found)
            return
        if isinstance(stmt, A.Foreach):
            if stmt.var_name not in vars_found:
                vars_found[stmt.var_name] = "int64_t"
            self._collect_vars_in_body(stmt.body, vars_found)
            return
        if isinstance(stmt, (A.While, A.Loop, A.Repeat, A.DoWhile, A.Block)):
            self._collect_vars_in_body(stmt.body, vars_found)
            return
        # Try/catch/finally: vars assigned inside any branch must be
        # declared at function scope, otherwise the auto-cleanup
        # pre-pass hoists `s = NULL;` to the function top but the
        # only declaration is inside a branch → "s undeclared" build
        # error. Walk every sub-block.
        if isinstance(stmt, A.TryExcept):
            self._collect_vars_in_body(stmt.try_body, vars_found)
            for _err_type, _var_name, body in stmt.catch_blocks:
                self._collect_vars_in_body(body, vars_found)
            if stmt.except_block:
                _ev, except_body = stmt.except_block
                self._collect_vars_in_body(except_body, vars_found)
            if stmt.finally_block:
                self._collect_vars_in_body(stmt.finally_block, vars_found)
            return

    def _collect_vars_in_body(
        self, body: List[A.ASTNode], vars_found: Dict[str, str]
    ) -> None:
        """Recursively collect all variables assigned in a function body."""
        for stmt in body:
            self._collect_var_in_stmt(stmt, vars_found)

    def _collect_globally_used_names(self: Any, nodes: List[A.ASTNode]) -> Set[str]:
        """Collect all names referenced across the entire module.
        Used to detect unused global constants and tag them with
        AILANG_UNUSED to prevent -Wunused-const-variable warnings.
        """
        used: Set[str] = set()
        for node in nodes:
            if isinstance(node, A.Function):
                # Scan function body for references
                self._collect_used_names_in_nodes(node.body or [], used)
                # Also scan default argument values
                for p in node.params or []:
                    if isinstance(p, tuple) and len(p) >= 3 and p[2]:
                        self._collect_used_names_in_node(p[2], used)
            elif isinstance(node, A.VarDecl) and node.init_value:
                # Global var/const initializers can reference other constants
                self._collect_used_names_in_node(node.init_value, used)
            elif isinstance(node, A.ClassDef):
                for method in getattr(node, "methods", []):
                    if hasattr(method, "body"):
                        self._collect_used_names_in_nodes(method.body or [], used)
            elif isinstance(node, A.TemplateBlock):
                # Template blocks may reference AILang constants by name
                # (simple text scan since it's raw C)
                pass  # Can't reliably parse C for AILang names
        return used

    def _collect_used_names_in_body(self: Any, body: List[A.ASTNode]) -> Set[str]:
        """Collect all variable/parameter names used in a function body."""
        used: Set[str] = set()
        self._collect_used_names_in_nodes(body, used)
        return used

    def _collect_used_names_in_nodes(
        self, nodes: List[A.ASTNode], used: Set[str]
    ) -> None:
        """Recursively collect used names from AST nodes."""
        for node in nodes:
            self._collect_used_names_in_node(node, used)

    def _collect_used_names_in_node(self: Any, node: A.ASTNode, used: Set[str]) -> None:
        """Collect used names from a single AST node."""
        if node is None:
            return
        if isinstance(node, A.Variable):
            used.add(node.name)
        elif isinstance(node, A.Assign):
            self._collect_used_names_in_node(node.value, used)
        elif isinstance(node, A.VarDecl):
            if node.init_value:
                self._collect_used_names_in_node(node.init_value, used)
        elif isinstance(node, A.BinaryOp):
            self._collect_used_names_in_node(node.left, used)
            self._collect_used_names_in_node(node.right, used)
        elif isinstance(node, A.UnaryOp):
            self._collect_used_names_in_node(node.operand, used)
        elif isinstance(node, A.Call):
            for arg in node.args:
                self._collect_used_names_in_node(arg, used)
        elif isinstance(node, A.If):
            self._collect_used_names_in_node(node.cond, used)
            self._collect_used_names_in_nodes(node.then_body, used)
            if node.else_body:
                self._collect_used_names_in_nodes(node.else_body, used)
            if hasattr(node, "elsif_branches") and node.elsif_branches:
                for cond, body in node.elsif_branches:
                    self._collect_used_names_in_node(cond, used)
                    self._collect_used_names_in_nodes(body, used)
        elif isinstance(node, A.Match):
            self._collect_used_names_in_node(node.expr, used)
            for case_expr, case_body in node.cases:
                self._collect_used_names_in_node(case_expr, used)
                self._collect_used_names_in_nodes(case_body, used)
            if node.default_case:
                self._collect_used_names_in_nodes(node.default_case, used)
        elif isinstance(node, A.While):
            self._collect_used_names_in_node(node.cond, used)
            self._collect_used_names_in_nodes(node.body, used)
        elif isinstance(node, A.For):
            if node.init:
                self._collect_used_names_in_node(node.init, used)
            self._collect_used_names_in_node(node.cond, used)
            if node.step:
                self._collect_used_names_in_node(node.step, used)
            self._collect_used_names_in_nodes(node.body, used)
        elif isinstance(node, A.Foreach):
            self._collect_used_names_in_node(node.iterable, used)
            self._collect_used_names_in_nodes(node.body, used)
        elif isinstance(node, A.Return):
            if node.value:
                self._collect_used_names_in_node(node.value, used)
        elif isinstance(node, A.ArrayAccess):
            self._collect_used_names_in_node(node.array, used)
            self._collect_used_names_in_node(node.index, used)
        elif isinstance(node, A.FieldAccess):
            self._collect_used_names_in_node(node.object_expr, used)
        elif isinstance(node, A.MethodCall):
            self._collect_used_names_in_node(node.object_expr, used)
            for arg in node.args:
                self._collect_used_names_in_node(arg, used)
        elif isinstance(node, A.TernaryOp):
            self._collect_used_names_in_node(node.cond, used)
            self._collect_used_names_in_node(node.true_expr, used)
            self._collect_used_names_in_node(node.false_expr, used)
        elif hasattr(node, "body") and isinstance(node.body, list):
            self._collect_used_names_in_nodes(node.body, used)

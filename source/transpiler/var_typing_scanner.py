"""
VarTypingScanner — service that fills the variable-typing fields of a TypeInfo.

Runs after ``TypeCollector`` (needs the ``classes`` / ``enums`` /
``functions`` tables already populated) and before any emit phase.
Walks the AST three times (fixed-point on vector types) recording
which locals are strings, vec256s, vec512s, dicts, dynamic arrays,
arrays, or enum bindings. Per-function scope is preserved by keying
the dicts on function name (``None`` == global).

Phase 4 of the New Path roadmap. Companion to ``TypeCollector``;
together they fully populate ``TypeInfo`` for the emit phases to
consult. The mixin queries ``_might_be_string`` / ``_is_string_expr_*``
moved to ``TypeInfo`` itself; this class only does the scan.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import List, Optional

from transpiler.type_info import TypeInfo


class VarTypingScanner:
    """Three-pass scan over the AST that fills the var-typing fields of
    a ``TypeInfo``. Three passes because vector-type inference is
    transitive (``v = vec_op(other_vec, ...)`` only knows ``v`` is a
    vector once ``other_vec`` has been classified) and we don't want
    to be order-sensitive.
    """

    # Number of fixed-point iterations. Three matches the legacy
    # ``_collect_string_vars`` budget; both vector tables stop changing
    # well before the third pass on every test case we have.
    _MAX_ITERATIONS = 3

    def run(self, nodes: List[A.ASTNode], type_info: TypeInfo) -> None:
        """Populate the var-typing fields of ``type_info`` in place.

        The five top-level mutable sets / dicts (``string_vars``,
        ``vec256_vars``, ``vec512_vars``, ``array_vars``, ``dict_vars``,
        ``dyn_array_vars``, ``enum_vars``) are cleared first so the
        method is idempotent; calling it twice is a no-op on the second
        pass given no AST changes."""
        type_info.string_vars.clear()
        type_info.array_vars.clear()
        type_info.vec256_vars.clear()
        type_info.vec512_vars.clear()
        type_info.dict_vars.clear()
        type_info.dyn_array_vars.clear()
        type_info.enum_vars.clear()

        for _ in range(self._MAX_ITERATIONS):
            prev_vec256 = sum(len(v) for v in type_info.vec256_vars.values())
            prev_vec512 = sum(len(v) for v in type_info.vec512_vars.values())
            for node in nodes:
                self._scan_assigns(node, None, type_info)
            new_vec256 = sum(len(v) for v in type_info.vec256_vars.values())
            new_vec512 = sum(len(v) for v in type_info.vec512_vars.values())
            if new_vec256 == prev_vec256 and new_vec512 == prev_vec512:
                break

    # ==================== walk ====================

    def _scan_assigns(
        self,
        node: A.ASTNode,
        func_scope: Optional[str],
        type_info: TypeInfo,
    ) -> None:
        """Recurse into the AST recording type info at bindings."""
        if isinstance(node, A.VarDecl):
            self._process_var_decl(node, func_scope, type_info)
            return
        if isinstance(node, A.Assign):
            self._process_assign(node, func_scope, type_info)
            return
        if isinstance(node, A.Function):
            self._process_function(node, type_info)
            return
        if isinstance(node, A.If):
            for stmt in node.then_body:
                self._scan_assigns(stmt, func_scope, type_info)
            if node.else_body:
                for stmt in node.else_body:
                    self._scan_assigns(stmt, func_scope, type_info)
            return
        if isinstance(node, A.Match):
            for _case_expr, case_body in node.cases:
                for stmt in case_body:
                    self._scan_assigns(stmt, func_scope, type_info)
            if node.default_case:
                for stmt in node.default_case:
                    self._scan_assigns(stmt, func_scope, type_info)
            return
        if isinstance(node, (A.While, A.For)):
            for stmt in node.body:
                self._scan_assigns(stmt, func_scope, type_info)

    def _process_function(self, node: A.Function, type_info: TypeInfo) -> None:
        """A function body is scanned with its own name as the scope so
        same-named locals in different functions don't collide."""
        if node.params:
            for param in node.params:
                if isinstance(param, tuple) and len(param) >= 2:
                    pname, ptype = param[0], param[1]
                    if parsed_type_to_str(ptype).lower() == "string":
                        self._add_string_var(pname, node.name, type_info)
        for stmt in node.body:
            self._scan_assigns(stmt, node.name, type_info)

    def _process_assign(
        self,
        node: A.Assign,
        func_scope: Optional[str],
        type_info: TypeInfo,
    ) -> None:
        """Classify a single Assign's RHS and record the LHS into the
        matching var-set on ``type_info``."""
        # Enum-variant binding: `x = SomeEnum.Variant`.
        if isinstance(node.value, A.FieldAccess):
            if (
                isinstance(node.value.object_expr, A.Variable)
                and node.value.object_expr.name in type_info.enums
            ):
                type_info.enum_vars.add(node.var_name)
        elif type_info.might_be_string_static(node.value, func_scope):
            self._add_string_var(node.var_name, func_scope, type_info)

        # Vector classification needs the vec_call helper because the
        # RHS itself isn't a literal vec type -- we infer from the type
        # of `vec_*(...)` arguments / the explicit vec_type string arg.
        if isinstance(node.value, A.Call):
            vec_type = self._get_vec_type_from_call(node.value, func_scope, type_info)
            if vec_type == "256":
                self._add_vec256_var(node.var_name, func_scope, type_info)
            elif vec_type == "512":
                self._add_vec512_var(node.var_name, func_scope, type_info)
        if isinstance(node.value, A.ArrayLit):
            type_info.array_vars.add(node.var_name)
        if isinstance(node.value, A.DictLit):
            type_info.dict_vars.add(node.var_name)
        if isinstance(node.value, A.ListComprehension):
            type_info.dyn_array_vars.add(node.var_name)
        if isinstance(node.value, A.Call) and node.value.name == "array_new":
            type_info.dyn_array_vars.add(node.var_name)

    def _process_var_decl(
        self,
        node: A.VarDecl,
        func_scope: Optional[str],
        type_info: TypeInfo,
    ) -> None:
        """Classify declaration bindings before C emission.

        Assignment scanning alone misses declarations such as
        `const string PREFIX = "/System/"`. If those names are not in
        `string_vars`, `PREFIX + cmd` can fall through to integer
        addition in the C backend.
        """
        declared = parsed_type_to_str(node.type_name).lower() if node.type_name else ""
        if declared in ("string", "str", "char *", "const char *"):
            type_info.var_types[node.var_name] = "string"
            self._add_string_var(node.var_name, func_scope, type_info)
        elif node.init_value is not None and type_info.might_be_string_static(
            node.init_value, func_scope
        ):
            type_info.var_types[node.var_name] = "string"
            self._add_string_var(node.var_name, func_scope, type_info)

        if isinstance(node.init_value, A.ArrayLit):
            type_info.array_vars.add(node.var_name)
        if isinstance(node.init_value, A.DictLit):
            type_info.dict_vars.add(node.var_name)
        if isinstance(node.init_value, A.ListComprehension):
            type_info.dyn_array_vars.add(node.var_name)
        if isinstance(node.init_value, A.Call) and node.init_value.name == "array_new":
            type_info.dyn_array_vars.add(node.var_name)

    # ==================== helpers ====================

    @staticmethod
    def _add_string_var(
        var_name: str, func_scope: Optional[str], type_info: TypeInfo
    ) -> None:
        if func_scope not in type_info.string_vars:
            type_info.string_vars[func_scope] = set()
        type_info.string_vars[func_scope].add(var_name)

    @staticmethod
    def _add_vec256_var(
        var_name: str, func_scope: Optional[str], type_info: TypeInfo
    ) -> None:
        if func_scope not in type_info.vec256_vars:
            type_info.vec256_vars[func_scope] = set()
        type_info.vec256_vars[func_scope].add(var_name)

    @staticmethod
    def _add_vec512_var(
        var_name: str, func_scope: Optional[str], type_info: TypeInfo
    ) -> None:
        if func_scope not in type_info.vec512_vars:
            type_info.vec512_vars[func_scope] = set()
        type_info.vec512_vars[func_scope].add(var_name)

    @staticmethod
    def _get_vec_type_from_call(
        call_node: A.Call,
        func_scope: Optional[str],
        type_info: TypeInfo,
    ) -> Optional[str]:
        """Infer the vector lane width of a ``vec_*(...)`` call.

        Two information sources: an explicit ``vec_type`` string
        argument (last arg, `"vec32b"`/`"vec64b"`), and the type of any
        ``A.Variable`` argument we've already classified as a vec256 /
        vec512 in this scope. Returns "256" / "512" / None."""
        if not call_node.name.startswith("vec_"):
            return None
        if not call_node.args:
            return None
        last_arg = call_node.args[-1]
        if isinstance(last_arg, A.StringLit):
            if last_arg.value in ("vec32b", "vec256", "vec4l"):
                return "256"
            if last_arg.value in ("vec64b", "vec512", "vec8l"):
                return "512"
        for arg in call_node.args:
            if not isinstance(arg, A.Variable):
                continue
            if (
                func_scope in type_info.vec256_vars
                and arg.name in type_info.vec256_vars[func_scope]
            ):
                return "256"
            if (
                None in type_info.vec256_vars
                and arg.name in type_info.vec256_vars[None]
            ):
                return "256"
            if (
                func_scope in type_info.vec512_vars
                and arg.name in type_info.vec512_vars[func_scope]
            ):
                return "512"
            if (
                None in type_info.vec512_vars
                and arg.name in type_info.vec512_vars[None]
            ):
                return "512"
        return None

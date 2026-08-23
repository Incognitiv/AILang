"""CodeGen function-analysis helpers mixin."""

from __future__ import annotations

from typing import Any

from ast_access import arg_at, param_at


class _CodeGenFunctionAnalysisMixin:
    def _walk_ast_nodes(self: Any, node: Any):
        from parser.ast import ASTNode

        if node is None:
            return
        if isinstance(node, ASTNode):
            yield node
            values = vars(node).values() if hasattr(node, "__dict__") else ()
            for value in values:
                yield from self._walk_ast_nodes(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                yield from self._walk_ast_nodes(item)
        elif isinstance(node, dict):
            for item in node.values():
                yield from self._walk_ast_nodes(item)

    def _find_recursive_functions(self: Any, func_nodes: list[Any]) -> set[str]:
        """Return functions in direct or mutual recursion cycles."""
        from parser.ast import Call

        function_names = {node.name for node in func_nodes}
        self._recursion_analyzed_functions = set(function_names)
        graph: dict[str, set[str]] = {name: set() for name in function_names}
        for node in func_nodes:
            for child in self._walk_ast_nodes(getattr(node, "body", [])):
                if isinstance(child, Call) and child.name in function_names:
                    graph[node.name].add(child.name)

        recursive: set[str] = set()

        def reaches(start: str, current: str, seen: set[str]) -> bool:
            for nxt in graph.get(current, set()):
                if nxt == start:
                    return True
                if nxt in seen:
                    continue
                seen.add(nxt)
                if reaches(start, nxt, seen):
                    return True
            return False

        for name in function_names:
            if reaches(name, name, set()):
                recursive.add(name)
        return recursive

    def _find_recursion_guard_elisions(self: Any, func_nodes: list[Any]) -> set[str]:
        """Return recursive functions proven to stay below the depth guard.

        This does not weaken the default recursion safety policy.  It only
        elides the guard for a narrow, C-like shape:
        one integer parameter, a leading base-case return, only direct self
        calls with that parameter minus a positive literal, and every external
        entry call has a known bounded argument.
        """
        recursive: set[str] = getattr(self, "_recursive_functions", set())
        if not recursive:
            return set()
        nodes_by_name = {node.name: node for node in func_nodes}
        result: set[str] = set()
        for name in recursive:
            node = nodes_by_name.get(name)
            if node is None:
                continue
            if self._can_elide_recursion_guard(node, func_nodes, recursive):
                result.add(name)
        return result

    def _can_elide_recursion_guard(
        self: Any,
        node: Any,
        func_nodes: list[Any],
        recursive: set[str],
    ) -> bool:
        from abi_symbols import has_export_decorator

        if has_export_decorator(getattr(node, "decorators", [])):
            return False
        param_name = self._single_integer_param_name(node)
        if param_name is None:
            return False
        if param_name in self._analyze_param_mutations(node):
            return False
        base_upper = self._leading_base_case_upper(node, param_name)
        if base_upper is None:
            return False
        if not self._recursive_calls_decrease(node, param_name, recursive):
            return False
        entry_upper = self._known_nonself_entry_upper(node.name, func_nodes)
        if entry_upper is None:
            return False
        if entry_upper <= base_upper:
            worst_depth = 1
        else:
            worst_depth = entry_upper - base_upper + 2
        return worst_depth < getattr(self, "max_recursion_depth", 10000)

    def _single_integer_param_name(self: Any, node: Any) -> str | None:
        from parser.ast import parsed_type_to_str

        params = getattr(node, "params", [])
        if len(params) != 1:
            return None
        param = param_at(node, 0)
        if len(param) < 2:
            return None
        type_text = parsed_type_to_str(param[1]).lower().replace(" ", "")
        integer_types = {
            "byte",
            "char",
            "short",
            "int",
            "long",
            "i8",
            "i16",
            "i32",
            "i64",
            "u8",
            "u16",
            "u32",
            "u64",
            "size_t",
            "usize",
        }
        return param[0] if type_text in integer_types else None

    def _leading_base_case_upper(self: Any, node: Any, param_name: str) -> int | None:
        from parser import ast as A

        body = getattr(node, "body", [])
        if not body or not isinstance(body[0], A.If):
            return None
        base_upper = self._base_case_upper_from_condition(body[0].cond, param_name)
        if base_upper is None:
            return None
        if self._node_calls_function(body[0].then_body, node.name):
            return None
        has_return = any(isinstance(stmt, A.Return) for stmt in body[0].then_body)
        return base_upper if has_return else None

    def _base_case_upper_from_condition(
        self: Any, cond: Any, param_name: str
    ) -> int | None:
        from parser import ast as A

        if not isinstance(cond, A.BinaryOp):
            return None
        op = cond.op
        left_var = isinstance(cond.left, A.Variable) and cond.left.name == param_name
        right_var = isinstance(cond.right, A.Variable) and cond.right.name == param_name
        left_lit = self._int_literal(cond.left)
        right_lit = self._int_literal(cond.right)
        if left_var and right_lit is not None:
            if op in {"<=", "lte"}:
                return right_lit
            if op in {"<", "lt"}:
                return right_lit - 1
        if right_var and left_lit is not None:
            if op in {">=", "gte"}:
                return left_lit
            if op in {">", "gt"}:
                return left_lit - 1
        return None

    def _recursive_calls_decrease(
        self: Any, node: Any, param_name: str, recursive: set[str]
    ) -> bool:
        from parser import ast as A

        saw_self_call = False
        for child in self._walk_ast_nodes(getattr(node, "body", [])):
            if not isinstance(child, A.Call) or child.name not in recursive:
                continue
            if child.name != node.name:
                return False
            saw_self_call = True
            if len(child.args) != 1:
                return False
            if self._positive_param_decrement(arg_at(child, 0), param_name) is None:
                return False
        return saw_self_call

    def _positive_param_decrement(self: Any, expr: Any, param_name: str) -> int | None:
        from parser import ast as A

        if not isinstance(expr, A.BinaryOp):
            return None
        if not (isinstance(expr.left, A.Variable) and expr.left.name == param_name):
            return None
        literal = self._int_literal(expr.right)
        if literal is None:
            return None
        if expr.op in {"-", "minus"} and literal > 0:
            return literal
        if expr.op in {"+", "plus"} and literal < 0:
            return -literal
        return None

    def _known_nonself_entry_upper(
        self: Any, target_name: str, func_nodes: list[Any]
    ) -> int | None:
        from parser import ast as A

        facts = getattr(self, "range_facts", None)
        if facts is None:
            return None
        found = False
        highest: int | None = None
        for caller in func_nodes:
            caller_name = getattr(caller, "name", None)
            if caller_name == target_name:
                continue
            for child in self._walk_ast_nodes(getattr(caller, "body", [])):
                if not isinstance(child, A.Call) or child.name != target_name:
                    continue
                if len(child.args) != 1:
                    return None
                arg = arg_at(child, 0)
                snapshot = facts._expr_scope_snapshot(arg, caller_name)
                interval = facts._expr_interval(arg, caller_name, snapshot)
                if interval is None:
                    return None
                found = True
                highest = (
                    interval.high if highest is None else max(highest, interval.high)
                )
        return highest if found else None

    def _int_literal(self: Any, node: Any) -> int | None:
        from parser import ast as A

        if isinstance(node, A.Number) and not node.is_float:
            return int(node.value)
        return None

    def _node_calls_function(self: Any, node: Any, func_name: str) -> bool:
        from parser import ast as A

        return any(
            isinstance(child, A.Call) and child.name == func_name
            for child in self._walk_ast_nodes(node)
        )

    def _analyze_param_mutations(self: Any, func_node: Any) -> set[str]:
        """
        Analyze which parameters are reassigned in the function body.
        This optimization allows read-only parameters to use SSA values directly,
        avoiding unnecessary alloca/store/load overhead.
        Returns:
            Set of parameter names that are assigned to (mutated) in the body.
        """
        from parser.ast import Assign, FieldAssign, TupleAssign, Variable

        param_names = {p[0] for p in func_node.params}
        if not param_names:
            return set()
        mutated: set[str] = set()
        stack = list(func_node.body)
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if isinstance(node, Assign) and node.var_name in param_names:
                mutated.add(node.var_name)
            elif (
                isinstance(node, FieldAssign)
                and isinstance(node.object_expr, Variable)
                and node.object_expr.name in param_names
            ):
                mutated.add(node.object_expr.name)
            elif isinstance(node, TupleAssign):
                mutated.update(v for v in node.var_names if v in param_names)
            stack.extend(self._get_child_statements(node))
        return mutated

    def _get_child_statements(self: Any, node: Any) -> list[Any]:
        """Get child statements from an AST node for walking."""
        from parser.ast import (
            BlockCall,
            For,
            Foreach,
            If,
            Loop,
            Match,
            Repeat,
            TryExcept,
            While,
        )

        if isinstance(node, If):
            return list(node.then_body) + list(node.else_body)
        if isinstance(node, (While, Loop, Repeat, Foreach)):
            return list(node.body)
        if isinstance(node, For):
            return [node.init, node.step, *list(node.body)]
        if isinstance(node, Match):
            return self._get_match_children(node)
        if isinstance(node, TryExcept):
            return self._get_try_except_children(node)
        if isinstance(node, BlockCall) and node.block:
            return list(node.block.body)
        return []

    def _get_match_children(self: Any, node: Any) -> list[Any]:
        """Get child statements from a Match node."""
        children: list[Any] = []
        for _, case_body in node.cases:
            children.extend(case_body)
        if node.default_case:
            children.extend(node.default_case)
        return children

    def _get_try_except_children(self: Any, node: Any) -> list[Any]:
        """Get child statements from a TryExcept node."""
        children: list[Any] = list(node.try_body)
        for _, _, handler_body in node.catch_blocks:
            children.extend(handler_body)
        if node.except_block:
            _, except_body = node.except_block
            children.extend(except_body)
        if node.finally_block:
            children.extend(node.finally_block)
        return children

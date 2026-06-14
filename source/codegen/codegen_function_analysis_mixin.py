"""CodeGen function-analysis helpers mixin."""

from __future__ import annotations

from typing import Any


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

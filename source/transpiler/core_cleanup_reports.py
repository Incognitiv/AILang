"""CTranspiler check reports and ownership-cleanup helpers."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Dict, List, Optional, Set, Tuple

from transpiler.class_field_ownership import (
    auto_owned_field_kind,
    auto_owned_field_names,
    auto_owned_fields,
    auto_owned_string_field_names,
    expr_produces_owned_value,
    field_type_text,
    owned_field_flag_name,
)

ClassField = Tuple[str, str, str]
RecordField = Tuple[str, str]


class _CTranspilerCleanupReportMixin:
    def __init__(self: Any) -> None:
        # Linter-only declarations for attributes reassigned during transpile().
        self._source_file: str = ""
        self.runtime_needs: Any = None
        self._globally_used_names: Set[str] = set()
        self._const_global_names: Set[str] = set()
        self._static_global_names: Set[str] = set()

    def _is_string_expr_for_scan(self: Any, node: A.ASTNode) -> bool:
        return self.type_info.is_string_expr_for_scan(node)

    def _might_be_string_static(
        self: Any, node: A.ASTNode, func_scope: Optional[str]
    ) -> bool:
        return self.type_info.might_be_string_static(node, func_scope)

    def _might_be_string(self: Any, node: A.ASTNode) -> bool:
        return self.type_info.might_be_string(node, self.current_function)

    def _can_elide_binary_safety(
        self: Any, node: A.BinaryOp, func_scope: Optional[str]
    ) -> bool:
        return self.range_facts.can_prove_no_overflow(node, func_scope)

    def _why_binary_safety_not_elided(
        self: Any, node: A.BinaryOp, func_scope: Optional[str]
    ) -> str:
        _proven, reason = self.range_facts.explain_no_overflow(node, func_scope)
        return reason

    def _binary_safety_decision(
        self: Any, node: A.BinaryOp, func_scope: Optional[str]
    ) -> Tuple[bool, str]:
        return self.range_facts.explain_no_overflow(node, func_scope)

    def _modulo_safety_decision(
        self: Any, node: A.BinaryOp, func_scope: Optional[str]
    ) -> Tuple[bool, str]:
        try:
            if self.range_facts.can_prove_safe_modulo(
                node, func_scope, bit_width=64, is_unsigned=False
            ):
                return True, "positive_divisor_proven"
        except (AttributeError, TypeError, ValueError) as exc:
            return False, f"modulo_safety_error:{type(exc).__name__}"
        return False, "modulo_safety_unknown"

    def _record_check_decision(
        self: Any,
        node: A.ASTNode,
        *,
        check_kind: str,
        operation: str,
        decision: str,
        reason: str,
    ) -> None:
        line = int(getattr(node, "line", 0) or 0)
        col = int(getattr(node, "col", 0) or 0)
        func = self.current_function or "<global>"
        row = {
            "check_kind": check_kind,
            "operation": operation,
            "decision": decision,
            "reason": reason,
            "line": line,
            "col": col,
            "function": func,
        }
        self._check_decisions.append(row)
        key = f"{check_kind}:{decision}"
        self._check_summary[key] = int(self._check_summary.get(key, 0)) + 1

    def get_check_report(self: Any) -> Dict[str, Any]:
        """Return collected check-elision decisions + summary counters."""
        return {
            "summary": dict(self._check_summary),
            "decisions": list(self._check_decisions),
        }

    def _record_format_decision(
        self: Any,
        node: A.ASTNode,
        *,
        format_kind: str,
        decision: str,
        reason: str,
        fallback_func: str = "",
    ) -> None:
        line = int(getattr(node, "line", 0) or 0)
        col = int(getattr(node, "col", 0) or 0)
        func = self.current_function or "<global>"
        row = {
            "format_kind": format_kind,
            "decision": decision,
            "reason": reason,
            "fallback_func": fallback_func,
            "line": line,
            "col": col,
            "function": func,
        }
        self._format_decisions.append(row)
        key = f"{format_kind}:{decision}"
        self._format_summary[key] = int(self._format_summary.get(key, 0)) + 1
        if fallback_func:
            fb_key = f"fallback:{fallback_func}"
            self._format_summary[fb_key] = int(self._format_summary.get(fb_key, 0)) + 1

    def get_format_report(self: Any) -> Dict[str, Any]:
        """Return collected formatting-specialization decisions."""
        return {
            "summary": dict(self._format_summary),
            "decisions": list(self._format_decisions),
        }

    def _field_ailang_type(
        self: Any, parent_class: str, field_name: str
    ) -> Optional[str]:
        return self.type_info.field_ailang_type(parent_class, field_name)

    def _class_ptr_type(self: Any, node: A.ASTNode) -> Optional[str]:
        return self.type_info.class_ptr_type(node, self._current_class)

    def _is_owned_string_alloc(self: Any, expr: A.ASTNode) -> bool:
        return self.ownership.is_owned_string_alloc(expr, self.current_function)

    def _user_fn_call_is_non_capturing(self: Any, node: A.Call) -> bool:
        return self.ownership.user_fn_call_is_non_capturing(node)

    def _collect_string_locals(self: Any, body: List[A.ASTNode]) -> List[str]:
        return self.ownership.collect_string_locals(body, self.current_function)

    def _collect_mixed_ownership_string_locals(
        self: Any, body: List[A.ASTNode]
    ) -> List[str]:
        return self.ownership.collect_mixed_ownership_string_locals(
            body, self.current_function
        )

    def _collect_array_locals(
        self: Any, body: List[A.ASTNode], call_name: str
    ) -> List[str]:
        return self.ownership.collect_array_locals(body, call_name)

    def _collect_with_owning(
        self: Any,
        body: List[A.ASTNode],
        owning_calls: Set[str],
        self_mutating_calls: Set[str],
    ) -> List[str]:
        return self.ownership.collect_with_owning(
            body, owning_calls, self_mutating_calls
        )

    def _collect_class_locals(
        self: Any, body: List[A.ASTNode]
    ) -> List[Tuple[str, str]]:
        return self.ownership.collect_class_locals(body)

    def _detect_escaping_class_locals(
        self: Any,
        body: List[A.ASTNode],
        class_locals: List[Tuple[str, str]],
    ) -> Set[str]:
        return self.ownership.detect_escaping_class_locals(
            body, class_locals, self.current_function
        )

    def _detect_escaping_locals(
        self: Any, body: List[A.ASTNode], var_names: Set[str]
    ) -> Set[str]:
        return self.ownership.detect_escaping_locals(
            body, var_names, self.current_function
        )

    def _mixed_owned_flag(self: Any, var_name: str) -> str:
        """C identifier for the runtime is_owned flag of a
        mixed-ownership tracked string local."""
        return f"__{self._mangle_var(var_name)}_owned"

    def _auto_owned_string_field_names(self: Any, class_name: str) -> Set[str]:
        """Class string fields with hidden ownership flags."""
        return auto_owned_string_field_names(self.classes.get(class_name))

    def _auto_owned_field_names(self: Any, class_name: str) -> Set[str]:
        """Class fields with hidden ownership flags."""
        return auto_owned_field_names(self.classes.get(class_name), self.classes)

    def _auto_owned_field_kind(self: Any, field_type: Any) -> Optional[str]:
        """Return the hidden-ownership kind for an AILang field type."""
        return auto_owned_field_kind(field_type, self.classes)

    def _auto_owned_param_entries(self: Any) -> Dict[str, Tuple[str, str, Any]]:
        """Return owned parameter metadata: name -> (flag, kind, type)."""
        return getattr(self, "_owned_param_flags", None) or {}

    def _class_field_owned_flag(self: Any, field_name: str) -> str:
        """Hidden C flag field for a class-owned string field."""
        return owned_field_flag_name(field_name)

    def _expr_produces_owned_value(
        self: Any,
        expr: A.ASTNode,
        kind: str,
        field_type: Any,
    ) -> bool:
        """Return whether an expression transfers ownership for a kind."""
        return expr_produces_owned_value(
            expr,
            kind,
            field_type,
            self.classes,
            self._is_owned_string_alloc,
        )

    def _owned_value_cleanup_lines(
        self: Any,
        kind: str,
        field_type: Any,
        target: str,
    ) -> List[str]:
        """C lines that release one owned value and reset the target."""
        if kind == "string":
            return [
                f"ailang_safe_free((void *)(uintptr_t)({target}));",
                f"{target} = NULL;",
            ]
        if kind == "class":
            class_name = field_type_text(field_type).strip()
            return [
                f"if ({target}) {{",
                f"    {class_name}_destructor({target});",
                f"    ailang_safe_free({target});",
                f"    {target} = NULL;",
                "}",
            ]
        if kind == "array":
            return [f"ailang_dyn_array_free(&{target});"]
        if kind == "str_array":
            return [f"ailang_str_array_free_v2(&{target});"]
        if kind == "dict":
            return [
                f"dict_destroy_fn({target});",
                f"{target} = NULL;",
            ]
        return []

    def _owned_field_cleanup_lines(self: Any, class_name: str, owner: str) -> List[str]:
        """C lines that free still-owned compiler-managed fields on `owner`."""
        lines: List[str] = []
        for field_name, field_type, kind in auto_owned_fields(
            self.classes.get(class_name), self.classes
        ):
            flag = self._class_field_owned_flag(field_name)
            target = f"{owner}->{field_name}"
            cleanup = self._owned_value_cleanup_lines(kind, field_type, target)
            lines.extend(
                [
                    f"if ({owner}->{flag}) {{",
                    *[f"    {line}" for line in cleanup],
                    f"    {owner}->{flag} = 0;",
                    "}",
                ]
            )
        return lines

    def _emit_owned_field_cleanup(self: Any, class_name: str, owner: str) -> None:
        """Emit cleanup for still-owned compiler-managed class fields."""
        for line in self._owned_field_cleanup_lines(class_name, owner):
            self.emit(line)

    def _emit_owned_param_cleanup(
        self: Any, excluded_names: Optional[Set[str]] = None
    ) -> None:
        """Free still-owned parameters that were not transferred."""
        excluded = excluded_names or set()
        for param_name, (
            flag,
            kind,
            param_type,
        ) in self._auto_owned_param_entries().items():
            if param_name in excluded:
                continue
            c_name = self._mangle_var(param_name)
            self.emit(f"if ({flag}) {{")
            for line in self._owned_value_cleanup_lines(kind, param_type, c_name):
                self.emit(f"    {line}")
            self.emit(f"    {flag} = 0;")
            self.emit("}")

    def _emit_owned_string_param_cleanup(
        self: Any, excluded_names: Optional[Set[str]] = None
    ) -> None:
        """Compatibility wrapper; now cleans all owned param kinds."""
        self._emit_owned_param_cleanup(excluded_names)

    def _has_any_cleanup(self: Any, exclude: Optional[A.ASTNode] = None) -> bool:
        """True if at least one tracked local would be freed by
        ``_emit_class_cleanup``. Used by visit_Return to decide whether
        to wrap the return expression in a typeof temp."""
        excluded: Set[str] = set()
        if exclude is not None and isinstance(exclude, A.Variable):
            excluded.add(exclude.name)
        cleanups: List[List[Any]] = [
            getattr(self, "_class_locals_for_cleanup", None) or [],
            getattr(self, "_string_locals_for_cleanup", None) or [],
            getattr(self, "_str_array_locals_for_cleanup", None) or [],
            getattr(self, "_int_array_locals_for_cleanup", None) or [],
            getattr(self, "_dyn_array_locals_for_cleanup", None) or [],
            getattr(self, "_lc_str_array_locals_for_cleanup", None) or [],
            getattr(self, "_dict_locals_for_cleanup", None) or [],
            getattr(self, "_mixed_ownership_cleanup", None) or [],
        ]
        for cl in cleanups:
            for entry in cl:
                if isinstance(entry, tuple):
                    var_name, _ = entry
                else:
                    var_name = entry
                if var_name not in excluded:
                    return True
        for param_name in self._auto_owned_param_entries():
            if param_name not in excluded:
                return True
        return False

    def _emit_class_cleanup(self: Any, exclude: Optional[A.ASTNode] = None) -> None:
        """Emit class-destructor + free + string free for non-escaping
        owned locals. ``exclude`` is the return-value expression: any
        var directly returned is skipped (it's escaping by transfer)."""
        excluded_names: Set[str] = set()
        if exclude is not None and isinstance(exclude, A.Variable):
            excluded_names.add(exclude.name)
        self._emit_owned_param_cleanup(excluded_names)
        cleanup_list = getattr(self, "_class_locals_for_cleanup", None) or []
        stack_class_locals = getattr(self, "_stack_owned_class_locals", None) or {}
        for var, class_name in cleanup_list:
            if var in excluded_names:
                continue
            mangled = self._mangle_var(var)
            if var in stack_class_locals:
                self.emit(f"if ({mangled}) {{ {class_name}_destructor({mangled}); }}")
            else:
                self.emit(
                    f"if ({mangled}) {{ {class_name}_destructor({mangled}); "
                    f"ailang_safe_free({mangled}); }}"
                )
        self._emit_cleanup_list(
            getattr(self, "_string_locals_for_cleanup", None) or [],
            excluded_names,
            lambda mangled, _var: f"ailang_safe_free((void *)(uintptr_t)({mangled}));",
        )
        self._emit_cleanup_list(
            getattr(self, "_str_array_locals_for_cleanup", None) or [],
            excluded_names,
            lambda mangled, _var: f"ailang_str_array_free(&{mangled});",
        )
        self._emit_cleanup_list(
            getattr(self, "_int_array_locals_for_cleanup", None) or [],
            excluded_names,
            lambda mangled, _var: f"ailang_int_array_free(&{mangled});",
        )
        self._emit_cleanup_list(
            getattr(self, "_dyn_array_locals_for_cleanup", None) or [],
            excluded_names,
            lambda mangled, _var: f"ailang_dyn_array_free(&{mangled});",
        )
        self._emit_cleanup_list(
            getattr(self, "_lc_str_array_locals_for_cleanup", None) or [],
            excluded_names,
            lambda mangled, _var: f"ailang_str_array_free_v2(&{mangled});",
        )
        self._emit_cleanup_list(
            getattr(self, "_dict_locals_for_cleanup", None) or [],
            excluded_names,
            lambda mangled, _var: f"dict_destroy_fn({mangled});",
        )
        self._emit_cleanup_list(
            getattr(self, "_mixed_ownership_cleanup", None) or [],
            excluded_names,
            lambda mangled, var: (
                f"if ({self._mixed_owned_flag(var)}) "
                f"ailang_safe_free((void *)(uintptr_t)({mangled}));"
            ),
        )

    def _emit_cleanup_list(
        self: Any,
        vars_list: List[str],
        excluded_names: Set[str],
        emitter: Any,
    ) -> None:
        for var in vars_list:
            if var in excluded_names:
                continue
            mangled = self._mangle_var(var)
            self.emit(emitter(mangled, var))

    def _count_var_reads(
        self: Any, body: List[A.ASTNode], var_names: Set[str]
    ) -> Dict[str, int]:
        """Count how many times each tracked var is READ in ``body``
        (excludes the LHS of an Assign — that's a write, not a read).
        Used by the ownership pass to identify single-use vars eligible
        for being consumed by the next op (e.g. ``d = c + "x"`` consumes
        ``c`` if ``c`` is used only once)."""
        counts: Dict[str, int] = dict.fromkeys(var_names, 0)
        if not var_names:
            return counts

        def walk_expr(expr: A.ASTNode) -> None:
            if expr is None:
                return
            if isinstance(expr, A.Variable) and expr.name in counts:
                counts[expr.name] += 1
                return
            for attr in (
                "left",
                "right",
                "true_expr",
                "false_expr",
                "cond",
                "object_expr",
                "array",
                "index",
                "expr",
                "value",
                "iterable",
            ):
                sub = getattr(expr, attr, None)
                if sub is not None and not isinstance(sub, (str, int, bool, float)):
                    walk_expr(sub)
            for attr in ("args", "elements", "parts"):
                seq = getattr(expr, attr, None)
                if isinstance(seq, list):
                    for child in seq:
                        if not isinstance(child, (str, int, bool, float, tuple)):
                            walk_expr(child)

        def walk(node: A.ASTNode) -> None:
            if node is None:
                return
            if isinstance(node, A.Assign):
                walk_expr(node.value)
            elif isinstance(node, A.Return):
                if node.value is not None:
                    walk_expr(node.value)
            elif isinstance(node, A.FieldAssign):
                walk_expr(node.value)
                walk_expr(node.object_expr)
            elif isinstance(node, A.Call):
                for arg in node.args or []:
                    walk_expr(arg)
            elif isinstance(node, A.MethodCall):
                walk_expr(node.object_expr)
                for arg in node.args or []:
                    walk_expr(arg)
            for attr in (
                "body",
                "then_body",
                "else_body",
                "try_body",
                "finally_body",
            ):
                sub = getattr(node, attr, None)
                if isinstance(sub, list):
                    for s in sub:
                        walk(s)
            elsif = getattr(node, "elsif_branches", None)
            if elsif:
                for cond, branch in elsif:
                    walk_expr(cond)
                    if isinstance(branch, list):
                        for s in branch:
                            walk(s)

        for stmt in body:
            walk(stmt)
        return counts

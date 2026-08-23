"""
OwnershipAnalyzer — service that answers ownership / escape questions
about an AST.

Replaces the analysis half of ``_OwnershipMixin``. The emit half
(``_emit_class_cleanup``, ``_mixed_owned_flag``, ``_has_any_cleanup``)
stays on ``CTranspiler`` because it writes C source, not because it's
inherently bound to the analyzer -- it'll move into a future emit
service.

Phase 5 of the New Path roadmap. Unlike the helper / type / var-typing
phases, ownership doesn't have a single one-shot ``run()`` entry
point: each query takes a function body and returns a per-function
result. The analyzer is built once per compile (it carries the type
tables + dispatch sets it consults) and queried many times during
emit. Effectively a stateless service over ``TypeInfo``.
"""

from __future__ import annotations

from parser import ast as A
from typing import Dict, List, Optional, Set, Tuple

from ast_access import arg_at
from transpiler.type_info import TypeInfo


class OwnershipAnalyzer:
    """Pure analysis of which AST locals own heap memory and which
    escape past their function. Constructed with a populated
    ``TypeInfo`` and the two dispatch sets that classify builtin
    behavior; held on ``CTranspiler`` as ``self.ownership`` and queried
    during emit.
    """

    # Type names whose values cannot smuggle a pointer back to the caller.
    # A user fn with one of these return types and only-primitive-or-string
    # params is treated as non-capturing for tracked-var args -- like the
    # known non-capturing builtin set.
    _NON_CAPTURING_RETURN_TYPES: "frozenset[str]" = frozenset(
        {
            "int",
            "int64_t",
            "int32_t",
            "int16_t",
            "int8_t",
            "long",
            "uint64_t",
            "uint32_t",
            "uint16_t",
            "uint8_t",
            "bool",
            "float",
            "double",
            "quad",
            "long double",
            "void",
        }
    )
    _NON_CAPTURING_PARAM_TYPES: "frozenset[str]" = frozenset(
        {
            "int",
            "int64_t",
            "int32_t",
            "int16_t",
            "int8_t",
            "long",
            "uint64_t",
            "uint32_t",
            "uint16_t",
            "uint8_t",
            "bool",
            "float",
            "double",
            "quad",
            "long double",
            "string",
            "char *",
            "const char *",
            "char",
        }
    )

    def __init__(
        self,
        type_info: TypeInfo,
        owning_calls: "frozenset[str]",
        non_capturing_calls: "frozenset[str]",
    ) -> None:
        self._type_info = type_info
        self._owning_calls = owning_calls
        self._non_capturing_calls = non_capturing_calls

    # ==================== ownership queries ====================

    def is_owned_string_alloc(
        self, expr: A.ASTNode, current_function: Optional[str] = None
    ) -> bool:
        """Does ``expr`` necessarily produce a fresh malloc'd string?

        Borrowed values (literals, plain variables, field reads) are
        NOT owned. The caller must free an owned string at scope exit
        unless escape analysis transfers ownership to a return /
        capturing call.

        ``current_function`` is forwarded to ``might_be_string`` so
        per-function-scoped string vars (the common case) are
        recognized correctly. Callers that have no scope context (e.g.
        outside a function body) may pass ``None``.
        """
        if expr is None:
            return False
        if isinstance(expr, A.InterpolatedString):
            return True
        if isinstance(expr, A.StringSlice):
            return True
        if isinstance(expr, A.Call) and expr.name in self._owning_calls:
            return True
        # User-defined fns returning ``string`` always heap-allocate
        # only when the type collector proved every return path produces
        # owned storage. Literal-return helpers are borrowed and must not
        # be freed by callers.
        if isinstance(expr, A.Call) and expr.name in self._type_info.functions:
            if expr.name in self._type_info.owned_string_return_funcs:
                return True
        # ``a + b`` on strings lowers to ailang_strcat -> fresh heap.
        if (isinstance(expr, A.BinaryOp) and expr.op == "+") and (
            self._type_info.might_be_string(expr.left, current_function)
            or self._type_info.might_be_string(expr.right, current_function)
        ):
            return True
        # A tracked owned-string local read exactly once is safe to
        # CONSUME. Without this the chained-temp pattern leaks:
        # ``c = a+b; d = c+str(42)`` -- c is read once in d's RHS,
        # escape detection marks it, nothing frees it. Marking it
        # owned here lets consuming-strcat take it.
        return (
            isinstance(expr, A.Variable)
            and expr.name in self._type_info.single_use_owned_strings
        )

    def user_fn_call_is_non_capturing(self, node: A.Call) -> bool:
        """A user fn cannot retain its pointer args past the call when
        (a) its return type can't carry a pointer back to the caller,
        AND (b) all its params are primitives / strings (no class
        receiver where it could field-store the arg). Conservative --
        doesn't recognize global-pointer smuggling, but that's rare in
        idiomatic AILang."""
        if node.name not in self._type_info.functions:
            return False
        params, ret_type = self._type_info.functions[node.name]
        if ret_type not in self._NON_CAPTURING_RETURN_TYPES:
            return False
        return all(ptype in self._NON_CAPTURING_PARAM_TYPES for ptype in params or [])

    # ==================== local collectors ====================

    def collect_string_locals(
        self, body: List[A.ASTNode], current_function: Optional[str] = None
    ) -> List[str]:
        """Vars where every assignment is an owned-string-alloc.
        Eligible for free-before-reassign + scope-exit auto-free."""
        owned, _mixed = self._scan_string_assigns(body, current_function)
        return owned

    def collect_mixed_ownership_string_locals(
        self, body: List[A.ASTNode], current_function: Optional[str] = None
    ) -> List[str]:
        """Vars with SOME owned and SOME non-owning string assigns.
        These need a runtime ``__var_owned`` flag -- set/cleared per
        assign, conditional free at scope exit."""
        _owned, mixed = self._scan_string_assigns(body, current_function)
        return mixed

    def _scan_string_assigns(
        self, body: List[A.ASTNode], current_function: Optional[str]
    ) -> Tuple[List[str], List[str]]:
        """Split string-assigned vars into pure-owned and mixed-ownership.
        Vars assigned only non-owning strings (literals, borrowed
        pointers) are in neither group -- those need no cleanup."""
        owned_seen: Set[str] = set()
        non_owning_seen: Set[str] = set()
        order: List[str] = []

        def walk(node: A.ASTNode) -> None:
            if node is None:
                return
            if isinstance(node, (A.Assign, A.VarDecl)):
                var_name = node.var_name
                value = node.value if isinstance(node, A.Assign) else node.init_value
                if value is None:
                    return
                if self.is_owned_string_alloc(value, current_function):
                    if var_name not in owned_seen and var_name not in non_owning_seen:
                        order.append(var_name)
                    owned_seen.add(var_name)
                elif self._type_info.might_be_string(value, current_function):
                    if var_name not in owned_seen and var_name not in non_owning_seen:
                        order.append(var_name)
                    non_owning_seen.add(var_name)
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
            if isinstance(node, A.TryExcept):
                for _et, _vn, cb in node.catch_blocks:
                    for s in cb:
                        walk(s)
                if node.except_block:
                    _ev, eb = node.except_block
                    for s in eb:
                        walk(s)
            elsif = getattr(node, "elsif_branches", None)
            if elsif:
                for _cond, branch in elsif:
                    if isinstance(branch, list):
                        for s in branch:
                            walk(s)

        for stmt in body:
            walk(stmt)
        owned_only = [v for v in order if v in owned_seen and v not in non_owning_seen]
        mixed = [v for v in order if v in owned_seen and v in non_owning_seen]
        return owned_only, mixed

    def collect_array_locals(self, body: List[A.ASTNode], call_name: str) -> List[str]:
        """Vars where every assign is ``var = call_name(...)``. Used
        for split() / split_ints() collection-tracking."""
        return self.collect_with_owning(body, {call_name}, set())

    def collect_with_owning(
        self,
        body: List[A.ASTNode],
        owning_calls: Set[str],
        self_mutating_calls: Set[str],
    ) -> List[str]:
        """Generic owned-local collector.

        Track a var iff every Assign to it is either:
          - ``var = <call>(...)`` for some call in ``owning_calls``, OR
          - ``var = <self_mutate>(var, ...)`` where the first arg is
            the same var (e.g. ``arr = array_push(arr, x)`` preserves
            ownership of arr's heap data; the new struct value just
            replaces the stack-stored fields).

        Any other assignment excludes the var (conservative: leak
        rather than free a borrowed value)."""
        owned_seen: Set[str] = set()
        non_owning_seen: Set[str] = set()
        order: List[str] = []

        def preserves_ownership(value: A.ASTNode, var_name: str) -> bool:
            if not isinstance(value, A.Call):
                return False
            if value.name in owning_calls:
                return True
            if value.name in self_mutating_calls and value.args:
                first = arg_at(value, 0)
                if isinstance(first, A.Variable) and first.name == var_name:
                    return True
                if isinstance(first, A.Call) and first.name in owning_calls:
                    return True
            return False

        def walk(node: A.ASTNode) -> None:
            if node is None:
                return
            if isinstance(node, (A.Assign, A.VarDecl)):
                var_name = node.var_name
                value = node.value if isinstance(node, A.Assign) else node.init_value
                if value is None:
                    return
                if preserves_ownership(value, var_name):
                    if var_name not in owned_seen:
                        owned_seen.add(var_name)
                        order.append(var_name)
                else:
                    non_owning_seen.add(var_name)
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
                for _cond, branch in elsif:
                    if isinstance(branch, list):
                        for s in branch:
                            walk(s)

        for stmt in body:
            walk(stmt)
        return [v for v in order if v not in non_owning_seen]

    def collect_class_locals(self, body: List[A.ASTNode]) -> List[Tuple[str, str]]:
        """Vars where every assign is ``new ClassName(...)``. Mixed
        class types are excluded for safety."""
        owned_class: Dict[str, str] = {}
        non_owning_seen: Set[str] = set()
        order: List[str] = []

        def walk(node: A.ASTNode) -> None:
            if node is None:
                return
            if isinstance(node, (A.Assign, A.VarDecl)):
                var_name = node.var_name
                value = node.value if isinstance(node, A.Assign) else node.init_value
                declared_type = ""
                if isinstance(node, A.VarDecl) and node.type_name is not None:
                    try:
                        declared_type = A.parsed_type_to_str(node.type_name)
                    except (TypeError, ValueError):
                        declared_type = str(node.type_name)
                class_type = None
                if (
                    isinstance(value, A.NewExpr)
                    and value.type_name in self._type_info.classes
                ):
                    class_type = value.type_name
                elif (
                    isinstance(value, A.Call)
                    and value.name in self._type_info.functions
                ):
                    _params, ret_type = self._type_info.functions[value.name]
                    if ret_type in self._type_info.classes:
                        class_type = ret_type
                if class_type is not None:
                    if var_name not in owned_class:
                        owned_class[var_name] = class_type
                        order.append(var_name)
                    elif owned_class[var_name] != class_type:
                        non_owning_seen.add(var_name)
                elif (
                    isinstance(node, A.Assign)
                    or declared_type in self._type_info.classes
                ):
                    non_owning_seen.add(var_name)
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
                for _cond, branch in elsif:
                    if isinstance(branch, list):
                        for s in branch:
                            walk(s)

        for stmt in body:
            walk(stmt)
        return [(v, owned_class[v]) for v in order if v not in non_owning_seen]

    def class_locals_constructed_by_new(
        self, body: List[A.ASTNode], class_locals: List[Tuple[str, str]]
    ) -> Set[str]:
        """Tracked class locals whose assignments are direct constructors.

        A class-returning call transfers heap ownership to the caller and must
        be cleaned as heap storage. Only direct ``new Class(...)`` values are
        eligible for stack lowering.
        """
        expected = dict(class_locals)
        if not expected:
            return set()
        constructed: Set[str] = set()
        blocked: Set[str] = set()

        def walk(node: A.ASTNode) -> None:
            if node is None:
                return
            if isinstance(node, (A.Assign, A.VarDecl)) and node.var_name in expected:
                value = node.value if isinstance(node, A.Assign) else node.init_value
                if (
                    isinstance(value, A.NewExpr)
                    and value.type_name == expected[node.var_name]
                ):
                    constructed.add(node.var_name)
                else:
                    blocked.add(node.var_name)
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
                for _cond, branch in elsif:
                    if isinstance(branch, list):
                        for s in branch:
                            walk(s)

        for stmt in body:
            walk(stmt)
        return constructed - blocked

    def detect_escaping_class_locals(
        self,
        body: List[A.ASTNode],
        class_locals: List[Tuple[str, str]],
        current_function: Optional[str] = None,
    ) -> Set[str]:
        """Compatibility wrapper for the legacy callers; delegates to
        the generic ``detect_escaping_locals``."""
        return self.detect_escaping_locals(
            body, {v for v, _ in class_locals}, current_function
        )

    # ==================== escape analysis ====================

    def detect_escaping_locals(
        self,
        body: List[A.ASTNode],
        var_names: Set[str],
        current_function: Optional[str] = None,
    ) -> Set[str]:
        """Subset of ``var_names`` that escape via return / field-store /
        capturing call / method receiver. Generic over class-typed and
        string-typed locals."""
        if not var_names:
            return set()
        escaping: Set[str] = set()

        def mark_uses_in(expr: A.ASTNode) -> None:
            if expr is None:
                return
            if isinstance(expr, A.Variable) and expr.name in var_names:
                escaping.add(expr.name)
                return
            # Indexed reads of a tracked local read/mutate data through the
            # local owner; the owner pointer itself is not retained elsewhere.
            # This covers arrays and dicts, because parser syntax `x[k]`
            # reaches the analyzer as ArrayAccess even when x is dictionary-
            # typed. Still walk computed receivers and indexes for real escapes.
            if isinstance(expr, A.ArrayAccess):
                if not (
                    isinstance(expr.array, A.Variable) and expr.array.name in var_names
                ):
                    mark_uses_in(expr.array)
                mark_uses_in(expr.index)
                return
            if isinstance(expr, A.DictAccess):
                if not (
                    isinstance(expr.dict_expr, A.Variable)
                    and expr.dict_expr.name in var_names
                ):
                    mark_uses_in(expr.dict_expr)
                mark_uses_in(expr.key_expr)
                return
            # Field read: ``r = x.field`` reads a value out of x;
            # x's pointer is not captured. Stop here so we don't
            # spuriously mark the receiver as escaped.
            if isinstance(expr, A.FieldAccess):
                return
            # Method receiver use is not an ownership escape by itself.
            # AILang lowers `obj.method()` to a direct function call with
            # `obj` as the receiver; the call may read/mutate the object,
            # but it does not retain the receiver pointer unless the method
            # explicitly stores/returns it, which is handled in that method's
            # own body. Still walk non-tracked receiver expressions and args.
            if isinstance(expr, A.MethodCall):
                obj = expr.object_expr
                if not (isinstance(obj, A.Variable) and obj.name in var_names):
                    mark_uses_in(obj)
                for child in expr.args or []:
                    mark_uses_in(child)
                return
            # Non-capturing call: direct var args don't escape, but
            # walk into nested non-Variable args to catch deeper
            # escapes.
            if isinstance(expr, A.Call) and (
                expr.name in self._non_capturing_calls
                or self.user_fn_call_is_non_capturing(expr)
            ):
                for child in expr.args or []:
                    if not (isinstance(child, A.Variable) and child.name in var_names):
                        mark_uses_in(child)
                return
            # ``a + b`` on strings -> ailang_strcat. Allocates a fresh
            # buffer + COPIES from each operand; operand pointers are
            # not retained -- same semantics as a non-capturing call.
            if (
                isinstance(expr, A.BinaryOp)
                and expr.op == "+"
                and (
                    self._type_info.might_be_string(expr.left, current_function)
                    or self._type_info.might_be_string(expr.right, current_function)
                )
            ):
                for child in (expr.left, expr.right):
                    if isinstance(child, A.Variable) and child.name in var_names:
                        continue
                    mark_uses_in(child)
                    return
            # Interpolated strings lower to a chain of ailang_strcat
            # calls -- same copying semantics as ``+``. Variable parts
            # are read into the new buffer, not aliased into it.
            if isinstance(expr, A.InterpolatedString):
                for part in expr.parts:
                    if isinstance(part, str):
                        continue
                    if isinstance(part, A.Variable) and part.name in var_names:
                        continue
                    mark_uses_in(part)
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
                    mark_uses_in(sub)
            for attr in ("args", "elements", "parts"):
                seq = getattr(expr, attr, None)
                if isinstance(seq, list):
                    for child in seq:
                        if not isinstance(child, (str, int, bool, float, tuple)):
                            mark_uses_in(child)

        def walk(node: A.ASTNode) -> None:
            if node is None:
                return
            # Return / FieldAssign: any var in the value escapes.
            if (isinstance(node, A.Return) and node.value is not None) or isinstance(
                node, A.FieldAssign
            ):
                if node.value is not None:
                    mark_uses_in(node.value)
            elif isinstance(node, A.Assign):
                # Self-mutate: ``var = mutate(var, ...)`` is in-place,
                # not an escape. Walk only the OTHER args of that call.
                self_mutates = {"array_push", "str_array_push", "array_set"}
                if (
                    isinstance(node.value, A.Call)
                    and node.value.name in self_mutates
                    and node.value.args
                    and isinstance(arg_at(node.value, 0), A.Variable)
                    and arg_at(node.value, 0).name == node.var_name
                ):
                    for arg in node.value.args[1:]:
                        mark_uses_in(arg)
                # If RHS reads a tracked var, that var escapes (passed
                # into another binding we don't track for cleanup).
                elif node.var_name not in var_names:
                    mark_uses_in(node.value)
                else:
                    # Re-init of a tracked var; walk RHS for OTHER
                    # tracked-var reads.
                    mark_uses_in(node.value)
            elif isinstance(node, A.VarDecl) and node.init_value is not None:
                mark_uses_in(node.init_value)
            elif isinstance(node, A.DictAssign):
                if not (
                    isinstance(node.dict_expr, A.Variable)
                    and node.dict_expr.name in var_names
                ):
                    mark_uses_in(node.dict_expr)
                mark_uses_in(node.key_expr)
                mark_uses_in(node.value_expr)
            elif isinstance(node, A.Call):
                non_capturing = (
                    node.name in self._non_capturing_calls
                    or node.name in ("dealloc", "free")
                    or self.user_fn_call_is_non_capturing(node)
                )
                if not non_capturing:
                    for arg in node.args or []:
                        mark_uses_in(arg)
                else:
                    for arg in node.args or []:
                        if not (isinstance(arg, A.Variable) and arg.name in var_names):
                            mark_uses_in(arg)
            elif isinstance(node, A.MethodCall):
                # Calling a method on a local is a normal use, not an
                # ownership escape. Escapes are still caught when a method call
                # expression is returned/stored by the generic expression walk.
                for arg in node.args or []:
                    mark_uses_in(arg)
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
                for _cond, branch in elsif:
                    if isinstance(branch, list):
                        for s in branch:
                            walk(s)
            for attr in ("value", "init_value", "cond", "init", "step"):
                sub = getattr(node, attr, None)
                if sub is not None and not isinstance(node, (A.Assign, A.VarDecl)):
                    walk(sub)

        for stmt in body:
            walk(stmt)
        return escaping

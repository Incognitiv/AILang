from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Dict, List, Set

from transpiler.class_field_ownership import (
    auto_owned_field_kind,
    is_auto_owned_param,
    is_string_type,
    owned_param_flag_name,
    param_name,
    string_len_param_name,
)


def _explicitly_deallocated_locals(
    body: List[A.ASTNode], var_names: Set[str]
) -> Set[str]:
    """Tracked locals passed to dealloc/free must stay heap-backed."""
    hits: Set[str] = set()

    def walk(node: A.ASTNode) -> None:
        if node is None:
            return
        if isinstance(node, A.Call) and node.name in {"dealloc", "free"}:
            for arg in node.args or []:
                if isinstance(arg, A.Variable) and arg.name in var_names:
                    hits.add(arg.name)
        for attr in (
            "body",
            "then_body",
            "else_body",
            "try_body",
            "finally_body",
        ):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                for stmt in sub:
                    walk(stmt)
        if isinstance(node, A.TryExcept):
            for _err_type, _var_name, catch_body in node.catch_blocks:
                for stmt in catch_body:
                    walk(stmt)
            if node.except_block:
                _err_var, except_body = node.except_block
                for stmt in except_body:
                    walk(stmt)
        elsif = getattr(node, "elsif_branches", None)
        if elsif:
            for _cond, branch in elsif:
                if isinstance(branch, list):
                    for stmt in branch:
                        walk(stmt)

    for stmt in body:
        walk(stmt)
    return hits


def _scan_dict_locals(body: List[A.ASTNode]) -> Set[str]:
    dict_vars: Set[str] = set()

    def walk(stmts: List[A.ASTNode]) -> None:
        for stmt in stmts:
            if isinstance(stmt, A.Assign) and isinstance(stmt.value, A.DictLit):
                dict_vars.add(stmt.var_name)
            if isinstance(stmt, A.VarDecl) and isinstance(stmt.init_value, A.DictLit):
                dict_vars.add(stmt.var_name)
            for attr in (
                "body",
                "then_body",
                "else_body",
                "try_body",
                "finally_body",
            ):
                sub = getattr(stmt, attr, None)
                if isinstance(sub, list):
                    walk(sub)

    walk(body)
    return dict_vars


def _prepare_owned_local_cleanup(
    self, body: List[A.ASTNode], track_single_use_strings: bool = False
) -> None:
    class_locals = self._collect_class_locals(body)
    string_locals = self._collect_string_locals(body)
    mixed_string_locals = self._collect_mixed_ownership_string_locals(body)
    str_array_locals = self._collect_array_locals(body, "split")
    int_array_locals = self._collect_array_locals(body, "split_ints")
    dyn_array_locals = self._collect_with_owning(
        body,
        {"array_new"},
        {"array_push", "array_set"},
    )
    lc_str_array_locals = self._collect_with_owning(
        body,
        {"str_array_new"},
        {"str_array_push"},
    )
    dict_vars = _scan_dict_locals(body)
    all_var_names = (
        {v for v, _ in class_locals}
        | set(string_locals)
        | set(mixed_string_locals)
        | set(str_array_locals)
        | set(int_array_locals)
        | set(dyn_array_locals)
        | set(lc_str_array_locals)
        | set(dict_vars)
    )
    escaping = self._detect_escaping_locals(body, all_var_names)
    non_escaping_class = [(v, c) for v, c in class_locals if v not in escaping]
    non_escaping_string = [v for v in string_locals if v not in escaping]
    non_escaping_mixed = [v for v in mixed_string_locals if v not in escaping]
    non_escaping_str_array = [v for v in str_array_locals if v not in escaping]
    non_escaping_int_array = [v for v in int_array_locals if v not in escaping]
    non_escaping_dyn_array = [v for v in dyn_array_locals if v not in escaping]
    non_escaping_lc_str_array = [v for v in lc_str_array_locals if v not in escaping]
    explicit_dealloc_class = _explicitly_deallocated_locals(
        body, {v for v, _ in non_escaping_class}
    )
    stack_constructed_class = self._class_locals_constructed_by_new(
        body, non_escaping_class
    )
    self._tracked_owned_class_locals = dict(non_escaping_class)
    self._stack_owned_class_locals = {
        v: c
        for v, c in non_escaping_class
        if v in stack_constructed_class and v not in explicit_dealloc_class
    }
    self._tracked_owned_string_locals = set(non_escaping_string)
    self._mixed_ownership_string_locals = set(non_escaping_mixed)
    self._mixed_ownership_cleanup = list(non_escaping_mixed)
    self._class_locals_for_cleanup = list(non_escaping_class)
    self._string_locals_for_cleanup = list(non_escaping_string)
    self._str_array_locals_for_cleanup = list(non_escaping_str_array)
    self._int_array_locals_for_cleanup = list(non_escaping_int_array)
    self._dyn_array_locals_for_cleanup = list(non_escaping_dyn_array)
    self._lc_str_array_locals_for_cleanup = list(non_escaping_lc_str_array)
    self._dict_locals_for_cleanup = [v for v in dict_vars if v not in escaping]
    self._owned_value_local_kinds = {
        **{v: ("class", class_name) for v, class_name in class_locals},
        **{v: ("string", "string") for v in string_locals},
        **{v: ("array", "array") for v in dyn_array_locals},
        **{v: ("str_array", "str_array") for v in lc_str_array_locals},
        **{v: ("dict", "dict") for v in dict_vars},
    }
    if track_single_use_strings:
        read_counts = self._count_var_reads(body, set(string_locals))
        self._single_use_owned_strings = {
            v for v in string_locals if v in escaping and read_counts.get(v, 0) == 1
        }
    _emit_owned_local_initializers(
        self,
        non_escaping_class,
        non_escaping_string,
        non_escaping_mixed,
        non_escaping_str_array,
        non_escaping_int_array,
        non_escaping_dyn_array,
        non_escaping_lc_str_array,
    )


def _emit_owned_local_initializers(
    self,
    non_escaping_class,
    non_escaping_string,
    non_escaping_mixed,
    non_escaping_str_array,
    non_escaping_int_array,
    non_escaping_dyn_array,
    non_escaping_lc_str_array,
) -> None:
    for var, _ in non_escaping_class:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = NULL;")
    for var, cls in self._stack_owned_class_locals.items():
        mangled = self._mangle_var(var)
        self.emit(f"{cls} __ailang_stack_{mangled};")
    for var in non_escaping_string:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = NULL;")
    for var in non_escaping_mixed:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = NULL;")
        self.emit(f"int {self._mixed_owned_flag(var)} = 0;")
    for var in non_escaping_str_array:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = (typeof({mangled})){{0}};")
    for var in non_escaping_int_array:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = (typeof({mangled})){{0}};")
    for var in non_escaping_dyn_array:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = (typeof({mangled})){{0}};")
    for var in non_escaping_lc_str_array:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = (typeof({mangled})){{0}};")
    for var in self._dict_locals_for_cleanup:
        mangled = self._mangle_var(var)
        self.emit(f"{mangled} = NULL;")


def _generate_class_method(self, class_name: str, method: A.Function) -> None:
    """Generate a class method as a C function with self parameter."""
    self.declared_vars = set()
    self._current_class = class_name
    method_name = self._sanitize_method_name(method.name, class_name)
    self.current_function = f"{class_name}_{method_name}"

    # Add 'self' and other parameters to declared vars; also track
    # the AILang type of class-typed params so `_class_ptr_type`
    # recognizes them as pointers in subsequent expression visits.
    self.declared_vars.add("self")
    for p in method.params or []:
        if isinstance(p, tuple):
            pname = p[0]
            ptype = p[1] if len(p) > 1 else None
            if ptype is not None:
                self._var_types[pname] = parsed_type_to_str(ptype)
                if is_string_type(ptype):
                    self.declared_vars.add(string_len_param_name(pname))
        else:
            pname = str(p)
        self.declared_vars.add(pname)

    # Collect all variables
    all_vars: Dict[str, str] = {}
    self._collect_vars_in_body(method.body, all_vars)

    # Build parameter list with self pointer first
    params = f"{class_name} *self"
    if method.params:
        param_strs = []
        for p in method.params:
            if isinstance(p, tuple):
                pname = p[0]
                ptype = p[1] if len(p) > 1 else "int"
                ptype_str = parsed_type_to_str(ptype)
                param_strs.append(self._format_c_param_declaration(ptype_str, pname))
                if is_string_type(ptype):
                    param_strs.append(f"int64_t {string_len_param_name(pname)}")
            else:
                pname = str(p)
                param_strs.append(f"int64_t {pname}")
            if is_auto_owned_param(p, self.classes):
                param_strs.append(f"int {owned_param_flag_name(param_name(p))}")
        params += ", " + ", ".join(param_strs)

    ret_type = self._get_return_type(method)
    func_name = f"{class_name}_{method_name}"

    self.emit_raw("")
    self.emit_raw(f"{ret_type} {func_name}({params}) {{")
    self.indent += 1

    self._owned_param_flags = {
        param_name(p): (
            owned_param_flag_name(param_name(p)),
            auto_owned_field_kind(p[1], self.classes) or "",
            p[1],
        )
        for p in method.params or []
        if is_auto_owned_param(p, self.classes)
    }
    self._owned_string_param_flags = {
        param_name(p): owned_param_flag_name(param_name(p))
        for p in method.params or []
        if isinstance(p, tuple)
        and auto_owned_field_kind(p[1], self.classes) == "string"
    }

    # Declare all local variables at top
    for var_name, var_type in all_vars.items():
        if var_name not in self.declared_vars:
            self.emit(self._format_c_declaration(var_type, var_name) + ";")
            self.declared_vars.add(var_name)

    # Recursion-depth guard: only for methods that participate in a
    # call cycle (e.g. NodeIndex_grow_to recurses on itself). For all
    # others, the entry check + per-Return wrapper would be pure
    # overhead, so skip both. Matches visit_Function's policy.
    self._guard_active = func_name in self._recursive_funcs
    if self._guard_active:
        self.emit(f'__ailang_check_recursion("{func_name}");')

    _prepare_owned_local_cleanup(self, method.body)

    # Generate body
    for stmt in method.body:
        self.visit(stmt)

    # Implicit return
    if not method.body or not isinstance(method.body[-1], A.Return):
        if method_name == "destructor":
            self._emit_owned_field_cleanup(class_name, "self")
        self._emit_class_cleanup(None)
        if self._guard_active:
            self.emit("__ailang_end_recursion();")
        if ret_type == "void":
            pass
        else:
            self.emit("return 0;")

    self.indent -= 1
    self.emit_raw("}")
    self._guard_active = False
    self._owned_string_param_flags = {}
    self._owned_param_flags = {}
    self._owned_value_local_kinds = {}
    self._stack_owned_class_locals = {}
    self._current_class = None
    self.current_function = None

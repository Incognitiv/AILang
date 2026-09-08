"""Function/class/comptime statement visitors for CStmtEmitter."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from parser.return_type_inference import body_terminates_with_value_or_throw
from typing import Any

from abi_symbols import explicit_c_abi_parts, has_export_decorator
from ast_access import param_at
from calling_conventions import c_callconv_macro
from target_info import os_from_platform
from transpiler.class_field_ownership import (
    is_auto_owned_field_type,
    is_auto_owned_param,
    is_string_type,
    owned_field_flag_name,
    owned_param_flag_name,
    param_name,
    string_len_field_name,
    string_len_param_name,
)
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr

from .local_int_narrowing import apply_proven_i32_narrowing
from .stmt_visit_class_method import (
    _prepare_owned_local_cleanup,
)
from .strlen_assign_cache import collect_length_only_string_locals


def _param_parts(param: Any) -> tuple[str, Any]:
    if isinstance(param, tuple):
        ptype = param[1] if len(param) >= 2 else None
        return str(param[0]), ptype
    return str(param), None


def _can_seed_call_hint_ranges(self, node: A.Function) -> bool:
    if node.name == "main":
        return False
    if has_export_decorator(getattr(node, "decorators", [])):
        return False
    return node.name not in getattr(self, "_fn_ptr_function_names", set())


def _seed_call_hint_param_ranges(self, node: A.Function) -> None:
    self._codegen_int_ranges = {}
    self._codegen_field_int_ranges = {}
    self._fixed_dict_value_ranges = {}
    self._codegen_string_length_ranges = {}
    if not _can_seed_call_hint_ranges(self, node):
        return
    facts = getattr(self, "range_facts", None)
    if facts is None:
        return
    hints = getattr(facts, "call_arg_ranges", {}).get(node.name, {})
    for index, param in enumerate(node.params or []):
        pname, ptype = _param_parts(param)
        declared = _declared_param_range(self, ptype)
        is_int = self._is_integer_type_name(parsed_type_to_str(ptype) if ptype else "")
        if declared is None and not is_int:
            continue
        if declared is not None:
            self._codegen_int_ranges[pname] = declared
        interval = hints.get(index)
        if interval is not None:
            hinted = (int(interval.low), int(interval.high))
            if declared is not None:
                hinted = (max(declared[0], hinted[0]), min(declared[1], hinted[1]))
            self._codegen_int_ranges[pname] = hinted


def _declared_param_range(self, ptype: Any) -> tuple[int, int] | None:
    target = ptype
    aliases = getattr(self, "_type_aliases", {})
    seen: set[str] = set()
    while isinstance(target, str) and target in aliases and target not in seen:
        seen.add(target)
        target = aliases[target]
    if not isinstance(target, A.RangeType):
        return None
    if not (
        isinstance(target.low, A.Number)
        and isinstance(target.high, A.Number)
        and isinstance(target.low.value, int)
        and isinstance(target.high.value, int)
    ):
        return None
    low = int(target.low.value)
    high = int(target.high.value)
    return low, high - 1 if target.exclusive else high


def _is_literal_return_guard(stmt: A.ASTNode, param_name: str) -> int | None:
    if not isinstance(stmt, A.If):
        return None
    cond = stmt.cond
    if not isinstance(cond, A.BinaryOp) or cond.op not in {"<=", "<"}:
        return None
    if not isinstance(cond.left, A.Variable) or cond.left.name != param_name:
        return None
    if not isinstance(cond.right, A.Number) or not isinstance(cond.right.value, int):
        return None
    if not stmt.then_body or not isinstance(stmt.then_body[-1], A.Return):
        return None
    bound = int(cond.right.value)
    return bound if cond.op == "<=" else bound - 1


def _is_decreasing_self_arg(expr: A.ASTNode, param_name: str) -> bool:
    if not isinstance(expr, A.BinaryOp) or expr.op not in {"-", "minus"}:
        return False
    if not isinstance(expr.left, A.Variable) or expr.left.name != param_name:
        return False
    if not isinstance(expr.right, A.Number) or not isinstance(expr.right.value, int):
        return False
    return int(expr.right.value) > 0


def _iter_ast_nodes(node: A.ASTNode):
    yield node
    for value in vars(node).values():
        if isinstance(value, A.ASTNode):
            yield from _iter_ast_nodes(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, A.ASTNode):
                    yield from _iter_ast_nodes(item)


def _self_calls_are_decreasing(node: A.Function, param_name: str, index: int) -> bool:
    found = False
    for stmt in node.body:
        for child in _iter_ast_nodes(stmt):
            if not isinstance(child, A.Call) or child.name != node.name:
                continue
            args = child.args or []
            if index >= len(args) or not _is_decreasing_self_arg(
                args[index], param_name
            ):
                return False
            found = True
    return found


def _can_elide_recursion_guard(self, node: A.Function) -> bool:
    if node.name not in getattr(self, "_recursive_funcs", set()):
        return False
    if len(node.params or []) != 1:
        return False
    param = param_at(node, 0)
    if not isinstance(param, tuple) or len(param) < 2:
        return False
    param_name = str(param[0])
    guard_bound: int | None = None
    for stmt in node.body:
        guard_bound = _is_literal_return_guard(stmt, param_name)
        if guard_bound is not None:
            break
    if guard_bound is None:
        return False
    facts = getattr(self, "range_facts", None)
    hints = getattr(facts, "call_arg_ranges", {}).get(node.name, {}) if facts else {}
    entry = hints.get(0)
    if entry is None:
        return False
    max_depth = int(entry.high) - int(guard_bound) + 1
    max_depth = max(max_depth, 1)
    if max_depth > 10000:
        return False
    return _self_calls_are_decreasing(node, param_name, 0)


def visit_Function(self, node: A.Function) -> None:
    """Generate C function with Fortran-style optimization attributes."""
    self.declared_vars = set()
    self.current_function = node.name
    self._current_function_body = node.body
    self._c_strlen_cache = {}
    self._owned_string_param_flags = {}
    self._owned_param_flags = {}
    self._bigint_params_for_cleanup = []
    self._bigint_locals_for_cleanup = []
    self._stack_array_field_values = {}
    self._inline_this_expr = None
    self._inline_this_stack_var = None
    self._current_param_type_overrides = {}

    # Check for Fortran-style decorators (strip @ prefix if present)
    decorators = [d.lstrip("@").lower() for d in getattr(node, "decorators", [])]
    use_noalias = "noalias" in decorators
    use_inline = "inline" in decorators
    use_pure = "pure" in decorators
    use_synchronized = "synchronized" in decorators
    self._unchecked_mode = "unchecked" in decorators
    callconv = c_callconv_macro(decorators)

    # Add parameters to declared vars and track their types
    for p in node.params or []:
        if isinstance(p, tuple) and len(p) >= 2:
            pname, ptype = p[0], p[1]
            self.declared_vars.add(pname)
            # Track parameter type for type inference
            ptype_str = parsed_type_to_str(ptype) if ptype else "i64"
            self._var_types[pname] = ptype_str
        else:
            pname = p[0] if isinstance(p, tuple) else str(p)
            self.declared_vars.add(pname)

    self._bigint_params_for_cleanup = [
        p[0]
        for p in (node.params or [])
        if isinstance(p, tuple)
        and len(p) >= 2
        and parsed_type_to_str(p[1]).strip().lower() == "unbounded"
    ]

    # Collect all variables
    all_vars: dict[str, str] = {}
    self._collect_vars_in_body(node.body, all_vars)
    self._current_param_type_overrides = apply_proven_i32_narrowing(
        self, node, all_vars
    )
    self._current_local_c_types = dict(all_vars)
    for p in node.params or []:
        if isinstance(p, tuple) and len(p) >= 2:
            pname, ptype = str(p[0]), p[1]
            self._current_local_c_types[pname] = self._current_param_type_overrides.get(
                pname, parsed_type_to_str(ptype) if ptype else "i64"
            )
    self._length_only_string_locals = collect_length_only_string_locals(
        self, node.body, all_vars
    )

    # Format params with restrict if @noalias
    params = self._format_params(node.params, use_noalias)
    ret_type = self._get_return_type(node)
    mangled_name = self._mangle_name(node.name)
    c_abi = explicit_c_abi_parts(getattr(node, "decorators", []))
    if c_abi is not None:
        ret_type, c_params = c_abi
        params = ", ".join(c_params) if c_params else "void"

    # Special handling for main() with command-line args
    is_main = node.name == "main"
    if is_main and "cmdline" in self.used_helpers:
        params = "int argc, char **argv"
        ret_type = "int"

    # Track both the C return type and the AILang declaration.  The latter
    # carries signedness/width semantics that C's implicit conversions would
    # otherwise silently truncate or reinterpret at `return`.
    self._current_ret_type = ret_type
    self._current_ret_spec = getattr(node, "return_type", None) or (
        "int" if node.name == "main" else None
    )

    # Build function attributes
    attrs = []
    if use_inline:
        attrs.append("static inline")
    if use_pure:
        attrs.append("__attribute__((pure))")

    self.emit_raw("")
    attr_str = " ".join(attrs) + " " if attrs else ""
    self.emit_raw(f"{attr_str}{ret_type} {callconv}{mangled_name}({params}) {{")
    self.indent += 1

    # Store argc/argv in globals at start of main
    if is_main and "cmdline" in self.used_helpers:
        self.emit("__ailang_argc = argc;")
        self.emit("__ailang_argv = argv;")

    # Register the built-in leak reporter to fire at process exit.
    # Fires only on real leaks by default; AILANG_LEAK_REPORT=1 forces
    # the report even when zero bytes are live.
    if is_main:
        self.emit("#ifndef AILANG_FREESTANDING")
        self.emit("atexit(__ailang_leak_report);")
        self.emit("__ailang_install_abnormal_exit_handlers();")
        self.emit("#endif")
        # Force stdout/stdin to binary mode on Windows. Default text
        # mode translates \n -> \r\n on output and \r\n -> \n on input,
        # which silently breaks byte-equivalent comparisons (the
        # AILang-Pure equivalence harness needs this; so does any
        # program piping bytes to/from another). This is the right
        # place for it: every AILang program gets it for free, and
        # `.ail` source never needs `#template` blocks with raw
        # `_setmode(_fileno(stdout), _O_BINARY)`. Added 30-04-2026.
        self.emit(
            "#if (defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)) "
            "&& !defined(AILANG_FREESTANDING)"
        )
        self.emit("    _setmode(_fileno(stdout), _O_BINARY);")
        self.emit("    _setmode(_fileno(stdin),  _O_BINARY);")
        self.emit("#endif")

    # Declare all local variables at top
    for var_name, var_type in all_vars.items():
        if var_name not in self.declared_vars:
            mangled = self._mangle_var(var_name)
            self.emit(self._format_c_declaration(var_type, mangled) + ";")
            self.declared_vars.add(var_name)

    # Detect unused parameters and silence warnings
    used_names = self._collect_used_names_in_body(node.body)
    if node.params:
        for param in node.params:
            param_name = param[0] if isinstance(param, tuple) else param
            if param_name not in used_names:
                mangled = self._mangle_var(param_name)
                self.emit(f"(void){mangled};  /* unused parameter */")

    # Detect unused local variables (assigned but never read) and silence warnings
    for var_name in all_vars:
        if var_name not in used_names and var_name not in (
            p[0] if isinstance(p, tuple) else p for p in (node.params or [])
        ):
            mangled = self._mangle_var(var_name)
            self.emit(f"(void){mangled};  /* unused variable */")

    # Recursion depth guard: only emit for functions in a call cycle.
    # `_unchecked_mode` (the @unchecked decorator) skips it explicitly;
    # for everything else, leaf functions don't need the check at all.
    self._guard_active = (
        not self._unchecked_mode
        and node.name in self._recursive_funcs
        and not _can_elide_recursion_guard(self, node)
    )
    if self._guard_active:
        self.emit(f'__ailang_check_recursion("{node.name}");')

    # @synchronized: emit static mutex + lock at entry
    if use_synchronized:
        safe = node.name.replace("~", "_dtor_")
        mtx_name = f"__sync_mtx_{safe}"
        self._synchronized_mutex_name = mtx_name
        # Static mutex handle (initialized once)
        self.emit(f"static int64_t {mtx_name} = 0;")
        self.emit(f"if ({mtx_name} == 0) {{ {mtx_name} = ailang_mutex_create(); }}")
        self.emit(f"ailang_mutex_lock({mtx_name});")
    else:
        self._synchronized_mutex_name = None

    _prepare_owned_local_cleanup(self, node.body, track_single_use_strings=True)
    _seed_call_hint_param_ranges(self, node)

    # Generate body
    for stmt in node.body:
        self.visit(stmt)

    # Emit a fallthrough return only when control flow can actually reach the
    # function end.  A trailing if/match/try may terminate on every branch
    # even though the final AST node is not itself Return.
    if not body_terminates_with_value_or_throw(node.body):
        # Auto-cleanup of non-escaping class locals at function exit.
        self._emit_class_cleanup(None)
        # Unlock @synchronized mutex before implicit return
        if self._synchronized_mutex_name:
            self.emit(f"ailang_mutex_unlock({self._synchronized_mutex_name});")
        if self._guard_active:
            self.emit("__ailang_end_recursion();")
        # Language-level void functions may fall through. Native main still
        # has an int ABI and therefore returns process status 0. No other
        # non-void function is allowed a manufactured default value.
        language_ret = (
            parsed_type_to_str(getattr(node, "return_type", None) or "void")
            .strip()
            .lower()
        )
        if ret_type == "void":
            self.emit("return;")
        elif is_main and language_ret == "void":
            self.emit("return 0;")
        else:
            raise ValueError(
                f"non-void function {node.name!r} can reach end without returning a value"
            )

    self.indent -= 1
    self.emit_raw("}")
    self.current_function = None
    self._current_function_body = None
    self._c_strlen_cache = {}
    self._owned_string_param_flags = {}
    self._owned_param_flags = {}
    self._owned_value_local_kinds = {}
    self._stack_owned_class_locals = {}
    self._stack_array_field_values = {}
    self._inline_this_expr = None
    self._inline_this_stack_var = None
    self._length_only_string_locals = set()
    self._codegen_int_ranges = {}
    self._codegen_field_int_ranges = {}
    self._fixed_dict_value_ranges = {}
    self._codegen_string_length_ranges = {}
    self._current_param_type_overrides = {}
    self._current_local_c_types = {}
    # Reset unchecked mode and synchronized state after function
    self._unchecked_mode = False
    self._synchronized_mutex_name = None


def visit_RecordDef(self, _node: A.RecordDef) -> None:
    """Record definitions are handled in type collection."""


def visit_GenericRecord(self, _node: A.GenericRecord) -> None:
    """Generic record definitions - currently deferred to instantiation."""
    # Generic records are monomorphized at instantiation time
    # No code generated until instantiated with concrete types


def visit_GenericClass(self, _node: A.GenericClass) -> None:
    """Generic class definitions - currently deferred to instantiation."""
    # Generic classes are monomorphized at instantiation time
    # No code generated until instantiated with concrete types


def visit_GenericFunction(self, _node: A.GenericFunction) -> None:
    """Generic function definitions - currently deferred to instantiation."""
    # Generic functions are monomorphized at call time
    # No code generated until called with concrete types


def visit_EnumDef(self, _node: A.EnumDef) -> None:
    """Enum definitions are handled in type collection."""


def visit_TemplateBlock(self, _node: A.TemplateBlock) -> None:
    """Template blocks are handled in _emit_template_blocks."""


def visit_CInclude(self, _node: A.CInclude) -> None:
    """CInclude directives are handled in _emit_cinclude_directives."""


def visit_LinkDirective(self, _node: A.LinkDirective) -> None:
    """Link directives are handled in _emit_link_directives."""


def visit_ExternFn(self, _node: A.ExternFn) -> None:
    """Extern fn declarations are handled in _emit_forward_declarations."""


def visit_ExternVar(self, _node: A.ExternVar) -> None:
    """Extern var declarations are handled in _emit_forward_declarations."""


def visit_ExternRecordDef(self, _node: A.ExternRecordDef) -> None:
    """Extern/opaque records are handled in type definitions."""


def visit_UnionDef(self, _node: A.UnionDef) -> None:
    """Union definitions are handled in type collection."""


def visit_ReinterpretCast(self, node: A.ReinterpretCast) -> str:
    """Emit reinterpret/bitcast as C cast."""
    c_type = self._ailang_type_to_c(node.target_type)
    inner = self.expr(node.value)
    # For pointer targets, use void* intermediate
    if "*" in c_type:
        return f"(({c_type})((void *)(uintptr_t)({inner})))"
    return f"(({c_type})({inner}))"


def visit_ComptimeExpr(self, node: A.ComptimeExpr) -> str:
    """Evaluate compile-time expression and emit constant."""
    result = self._evaluate_comptime(node.expr)
    if result is not None:
        if isinstance(result, bool):
            return "1" if result else "0"
        if isinstance(result, str):
            return f'"{result}"'
        return str(result)
    # Fall back to runtime evaluation
    return self.expr(node.expr)


def visit_ComptimeBlock(self, node: A.ComptimeBlock) -> None:
    """Execute compile-time block."""
    for stmt in node.body:
        self.visit(stmt)


def visit_ComptimeIf(self, node: A.ComptimeIf) -> None:
    """Handle compile-time conditional."""
    result = self._evaluate_comptime(node.cond)
    if result is not None:
        if result:
            for stmt in node.then_body:
                self.visit(stmt)
        else:
            for stmt in node.else_body:
                self.visit(stmt)
    else:
        # Fall back to runtime if - shouldn't happen
        self.emit("/* Warning: comptime if evaluated at runtime */")
        self.visit_If(A.If(node.cond, node.then_body, node.else_body))


def visit_StaticAssert(self, node: A.StaticAssert) -> None:
    """Handle static assertion - compile-time only."""
    result = self._evaluate_comptime(node.condition)
    if result is None:
        self.emit("/* Warning: static_assert could not be evaluated */")
        return
    if not result:
        msg = node.message or "Static assertion failed"
        raise ValueError(f"static_assert failed: {msg}")
    # If passes, emit nothing (compile-time only)


def _evaluate_comptime(self, expr: A.ASTNode) -> Any:
    """Evaluate an expression at compile time if possible."""
    if isinstance(expr, A.Number):
        if expr.is_float:
            return float(expr.value)
        return int(expr.value)

    if isinstance(expr, A.Bool):
        return expr.value

    if isinstance(expr, A.StringLit):
        return expr.value

    if isinstance(expr, A.Call):
        if expr.args:
            return None
        if expr.name == "target_os":
            return os_from_platform()
        if expr.name == "target_backend":
            return "c"

    if isinstance(expr, A.BinaryOp):
        left = self._evaluate_comptime(expr.left)
        right = self._evaluate_comptime(expr.right)
        if left is None or right is None:
            return None

        op = expr.op
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                return None  # Can't divide by zero
            return left // right if isinstance(left, int) else left / right
        if op == "%":
            return left % right
        if op == "**":
            return left**right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
        if op in ("and", "&&"):
            return left and right
        if op in ("or", "||"):
            return left or right

    if isinstance(expr, A.UnaryOp):
        operand = self._evaluate_comptime(expr.operand)
        if operand is None:
            return None
        if expr.op == "-":
            return -operand
        if expr.op in ("not", "!"):
            return not operand

    return None


def visit_ClassDef(self, node: A.ClassDef) -> None:
    """Generate C struct and methods for class definition."""
    # Struct is already emitted in type collection
    # Generate methods as regular functions with self parameter
    for method in node.methods:
        self._generate_class_method(node.name, method)
    # If the user did NOT define a destructor (~ClassName), emit a
    # default no-op stub so the auto-cleanup path can always call
    # `ClassName_destructor + free`.
    has_destructor = any(m.name.startswith("~") for m in node.methods)
    if not has_destructor:
        self.emit_raw("")
        self.emit_raw(
            f"AILANG_UNUSED static void {node.name}_destructor({node.name} *self) {{"
        )
        self.emit_raw("    if (!self) return;")
        cleanup_lines = self._owned_field_cleanup_lines(node.name, "self")
        if cleanup_lines:
            for line in cleanup_lines:
                self.emit_raw(f"    {line}")
        else:
            self.emit_raw("    (void)self;")
        self.emit_raw("}")
    # Emit a `ClassName_new(args) -> ClassName *` constructor wrapper
    # so `new ClassName(args)` lowers to a plain function call rather
    # than a GCC statement-expression (which -Wpedantic forbids).
    self._emit_class_new_wrapper(node)


def _class_new_signature(self, node: A.ClassDef) -> tuple[str, list[str]]:
    """Return (param_decl, arg_names) for Class_new based on init or fields.

    AILang's JIT codegen (expr_generator.visit_NewExpr) uses two
    modes: if the class declares `init`, the constructor takes that
    method's parameters and forwards to it; otherwise it takes one
    argument per field, in declaration order, and assigns positionally.
    Mirror both modes here so the C output matches the JIT semantics.
    """
    init_method: A.Function | None = next(
        (m for m in node.methods if m.name == "init"), None
    )
    if init_method is not None:
        if not init_method.params:
            return "void", []
        param_strs = []
        arg_names = []
        for param in init_method.params:
            param_strs.append(self._format_method_param(param))
            pname = param_name(param)
            if (
                isinstance(param, tuple)
                and len(param) >= 2
                and is_string_type(param[1])
            ):
                param_strs.append(f"int64_t {string_len_param_name(pname)}")
            if is_auto_owned_param(param, self.classes):
                param_strs.append(f"int {owned_param_flag_name(pname)}")
            arg_names.append(pname)
        return ", ".join(param_strs), arg_names
    # No init -> positional field initialization (record-style).
    fields, _methods = self.classes.get(node.name, ([], []))
    if not fields:
        return "void", []
    param_strs = []
    arg_names = []
    for _vis, fname, ftype in fields:
        ctype = self._ailang_type_to_c(ftype)
        param_strs.append(f"{ctype} {fname}")
        if is_string_type(ftype):
            param_strs.append(f"int64_t {string_len_field_name(fname)}")
        if is_auto_owned_field_type(ftype, self.classes):
            param_strs.append(f"int {owned_field_flag_name(fname)}")
        arg_names.append(fname)
    return ", ".join(param_strs), arg_names


def _emit_class_new_wrapper(self, node: A.ClassDef) -> None:
    """Emit `Class_new(...)` allocator. If the class has an `init`
    method, the wrapper forwards to it; otherwise it does positional
    field assignment (record-style construction)."""
    class_name = node.name
    param_decl, arg_names = self._class_new_signature(node)
    init_method: A.Function | None = next(
        (m for m in node.methods if m.name == "init"), None
    )
    self.emit_raw("")
    self.emit_raw(f"static {class_name} *{class_name}_new({param_decl}) {{")
    self.emit_raw(
        f"    {class_name} *__t = "
        f"({class_name} *)ailang_safe_malloc(sizeof({class_name}));"
    )
    # Every instance starts from deterministic zeroed storage. This makes the
    # C backend match LLVM's default initialization and prevents uninitialized
    # fields when a class has an explicit init() that touches only a subset.
    self.emit_raw(f"    *__t = ({class_name}){{0}};")

    # Apply declaration-time field defaults before init().  They are part of
    # the class declaration contract, not a parser-only decoration.
    for _vis, field_name, field_type, init_value in node.fields:
        if init_value is None:
            continue
        init_expr = self.expr(init_value)
        checked_expr = checked_fixed_int_conversion_expr(
            self, init_value, init_expr, field_type
        )
        if checked_expr is not None:
            init_expr = checked_expr
        field_type_text = parsed_type_to_str(field_type).strip().lower()
        if (
            field_type_text in {"array", "str_array", "stringarray", "intarray"}
            and isinstance(init_value, A.Number)
            and not isinstance(init_value.value, float)
            and int(init_value.value) == 0
        ):
            init_expr = (
                f"({self._ailang_type_to_c(parsed_type_to_str(field_type))}){{0}}"
            )
        self.emit_raw(f"    __t->{field_name} = {init_expr};")
        if is_string_type(field_type):
            self.emit_raw(
                f"    __t->{string_len_field_name(field_name)} = "
                f"{self._emit_known_strlen(init_value, init_expr)};"
            )
        if is_auto_owned_field_type(field_type, self.classes):
            kind = self._auto_owned_field_kind(field_type)
            if kind is not None:
                owned = self._expr_produces_owned_value(init_value, kind, field_type)
                self.emit_raw(
                    f"    __t->{owned_field_flag_name(field_name)} = "
                    f"{'1' if owned else '0'};"
                )
    if init_method is not None:
        init_call_args = ["__t"]
        for param in init_method.params or []:
            pname = param_name(param)
            init_call_args.append(pname)
            if (
                isinstance(param, tuple)
                and len(param) >= 2
                and is_string_type(param[1])
            ):
                init_call_args.append(string_len_param_name(pname))
            if is_auto_owned_param(param, self.classes):
                init_call_args.append(owned_param_flag_name(pname))
        init_args = ", ".join(init_call_args)
        self.emit_raw(f"    {class_name}_init({init_args});")
    else:
        for name in arg_names:
            self.emit_raw(f"    __t->{name} = {name};")
            field_type = self._field_ailang_type(class_name, name)
            if is_string_type(field_type):
                self.emit_raw(
                    f"    __t->{string_len_field_name(name)} = "
                    f"{string_len_field_name(name)};"
                )
            if name in self._auto_owned_field_names(class_name):
                self.emit_raw(
                    f"    __t->{owned_field_flag_name(name)} = "
                    f"{owned_field_flag_name(name)};"
                )
    self.emit_raw("    return __t;")
    self.emit_raw("}")


def _sanitize_method_name(self, method_name: str, _class_name: str = "") -> str:
    """Convert method name to valid C identifier.

    Handles destructors: ~ClassName -> destructor
    """
    if method_name.startswith("~"):
        return "destructor"
    return method_name

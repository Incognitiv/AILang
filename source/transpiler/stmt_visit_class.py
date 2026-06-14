"""Function/class/comptime statement visitors for CStmtEmitter."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any, Dict, List, Optional, Tuple

from abi_symbols import explicit_c_abi_parts
from calling_conventions import c_callconv_macro
from target_info import os_from_platform
from transpiler.class_field_ownership import (
    auto_owned_fields,
    is_auto_owned_field_type,
    is_auto_owned_param,
    is_string_type,
    owned_field_flag_name,
    owned_param_flag_name,
    param_name,
    string_len_field_name,
    string_len_param_name,
)

from .stmt_visit_class_method import (
    _prepare_owned_local_cleanup,
)
from .strlen_assign_cache import collect_length_only_string_locals


def visit_Function(self, node: A.Function) -> None:
    """Generate C function with Fortran-style optimization attributes."""
    self.declared_vars = set()
    self.current_function = node.name
    self._current_function_body = node.body
    self._c_strlen_cache = {}
    self._owned_string_param_flags = {}
    self._owned_param_flags = {}
    self._stack_array_field_values = {}
    self._inline_this_expr = None
    self._inline_this_stack_var = None

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

    # Collect all variables
    all_vars: Dict[str, str] = {}
    self._collect_vars_in_body(node.body, all_vars)
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

    # Track return type so visit_Return knows if we're void
    self._current_ret_type = ret_type

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
        self.emit("atexit(__ailang_leak_report);")
        self.emit("__ailang_install_abnormal_exit_handlers();")
        # Force stdout/stdin to binary mode on Windows. Default text
        # mode translates \n -> \r\n on output and \r\n -> \n on input,
        # which silently breaks byte-equivalent comparisons (the
        # AILang-Pure equivalence harness needs this; so does any
        # program piping bytes to/from another). This is the right
        # place for it: every AILang program gets it for free, and
        # `.ail` source never needs `#template` blocks with raw
        # `_setmode(_fileno(stdout), _O_BINARY)`. Added 30-04-2026.
        self.emit("#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)")
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
    self._guard_active = not self._unchecked_mode and node.name in self._recursive_funcs
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

    # Generate body
    for stmt in node.body:
        self.visit(stmt)

    # Implicit return for functions that don't end with a return statement
    if not node.body or not isinstance(node.body[-1], A.Return):
        # Auto-cleanup of non-escaping class locals at function exit.
        self._emit_class_cleanup(None)
        # Unlock @synchronized mutex before implicit return
        if self._synchronized_mutex_name:
            self.emit(f"ailang_mutex_unlock({self._synchronized_mutex_name});")
        if self._guard_active:
            self.emit("__ailang_end_recursion();")
        # Emit appropriate implicit return based on return type
        if ret_type == "void":
            self.emit("return;")
        elif ret_type in (
            "int",
            "int64_t",
            "int32_t",
            "int16_t",
            "int8_t",
            "uint64_t",
            "uint32_t",
            "uint16_t",
            "uint8_t",
        ):
            self.emit("return 0;")
        elif ret_type in ("double", "float"):
            self.emit("return 0.0;")
        elif "*" in ret_type:
            self.emit("return NULL;")
        else:
            self.emit("return 0;")

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


def _class_new_signature(self, node: A.ClassDef) -> Tuple[str, List[str]]:
    """Return (param_decl, arg_names) for Class_new based on init or fields.

    AILang's JIT codegen (expr_generator.visit_NewExpr) uses two
    modes: if the class declares `init`, the constructor takes that
    method's parameters and forwards to it; otherwise it takes one
    argument per field, in declaration order, and assigns positionally.
    Mirror both modes here so the C output matches the JIT semantics.
    """
    init_method: Optional[A.Function] = next(
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
    init_method: Optional[A.Function] = next(
        (m for m in node.methods if m.name == "init"), None
    )
    self.emit_raw("")
    self.emit_raw(f"static {class_name} *{class_name}_new({param_decl}) {{")
    self.emit_raw(
        f"    {class_name} *__t = "
        f"({class_name} *)ailang_safe_malloc(sizeof({class_name}));"
    )
    if init_method is not None:
        for field_name, _field_type, kind in auto_owned_fields(
            self.classes.get(class_name), self.classes
        ):
            if kind in {"string", "class", "dict"}:
                self.emit_raw(f"    __t->{field_name} = NULL;")
            elif kind in {"array", "str_array"}:
                self.emit_raw(f"    __t->{field_name}.data = NULL;")
                self.emit_raw(f"    __t->{field_name}.length = 0;")
                self.emit_raw(f"    __t->{field_name}.capacity = 0;")
            if kind == "string":
                self.emit_raw(f"    __t->{string_len_field_name(field_name)} = 0;")
            self.emit_raw(f"    __t->{owned_field_flag_name(field_name)} = 0;")
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

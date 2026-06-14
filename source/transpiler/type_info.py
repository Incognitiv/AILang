"""
TypeInfo — explicit data container for static program type information.

The single home for everything the analysis phases learn about types:
record / union / enum / class shapes, function signatures, decorator
metadata, plus per-function-scope variable typing (which locals are
strings, vectors, dicts, etc.).

Two services populate it:

* ``TypeCollector`` walks the AST once and fills the type-table fields
  (records, unions, enums, classes, functions, ...).
* ``VarTypingScanner`` runs after, three iterations, and fills the
  variable-typing fields (string_vars, vec256_vars, ...). It needs the
  type tables already populated to know what's a class vs an enum etc.

After both services run, ``TypeInfo`` is read-only for the rest of
compilation. Emit phases query it via the methods below.

The container also owns the queries that consume its data --
``field_ailang_type`` and ``class_ptr_type`` -- because the answer
depends on the same fields. Keeping the queries with the data means
the emit phases get a single object to ask everything of, not "look
up X in self.classes, then Y in self._var_types, then..."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from parser import ast as A
from typing import Any, Dict, List, Optional, Set, Tuple

from ast_access import arg_at

# Type aliases used widely. Re-exported here to avoid a tangled import
# graph -- emit_prologue and the tests import these names from here.
RecordField = Tuple[str, str]  # (field_name, field_type)
ClassField = Tuple[str, str, str]  # (visibility, field_name, field_type)


@dataclass(slots=True)
class TypeInfo:
    """Static type information about the program being compiled.

    Every field defaults to empty so a fresh ``TypeInfo`` is the "no
    program seen yet" state. Services populate the fields in place;
    no service mutates a field another service is currently reading.
    """

    # ==================== Type tables (TypeCollector) ====================

    records: Dict[str, List[RecordField]] = field(default_factory=dict)
    opaque_records: Set[str] = field(default_factory=set)
    extern_record_c_names: Dict[str, str] = field(default_factory=dict)
    extern_record_c_name_explicit: Set[str] = field(default_factory=set)
    extern_record_opaque: Set[str] = field(default_factory=set)
    extern_record_layouts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    unions: Dict[str, List[RecordField]] = field(default_factory=dict)
    enums: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    # Class entry: ([(visibility, field_name, field_type), ...], [methods])
    classes: Dict[str, Tuple[List[ClassField], List[A.Function]]] = field(
        default_factory=dict
    )
    # Function entry: ([param_type_names], return_type_name)
    functions: Dict[str, Tuple[List[str], str]] = field(default_factory=dict)
    # Data-carrying enum variants: enum_name -> {variant_name -> [(field_name, field_type)]}
    data_enums: Dict[str, Dict[str, List[Tuple[str, str]]]] = field(
        default_factory=dict
    )
    # Decorators attached to type definitions: type_name -> [decorator_names]
    type_decorators: Dict[str, List[Any]] = field(default_factory=dict)
    # Compile-time type aliases, including range aliases.
    type_aliases: Dict[str, Any] = field(default_factory=dict)
    callback_aliases: Dict[str, Any] = field(default_factory=dict)
    # Function default-arg metadata: fn_name -> [(param_index, default_value_node)]
    func_defaults: Dict[str, List[Tuple[int, A.ASTNode]]] = field(default_factory=dict)
    # Functions / methods that participate in a call cycle. Computed once per
    # compile by ``TypeCollector`` from the call graph; consumed during emit
    # to decide whether to wrap a function body in a recursion-depth guard.
    # Names are bare for free functions, ``Class_method`` for methods.
    recursive_funcs: Set[str] = field(default_factory=set)
    # User functions whose string return value is known to be heap-owned.
    # Literal/borrowed string returns stay out of this set so the C backend
    # does not free static storage returned through a `: string` signature.
    owned_string_return_funcs: Set[str] = field(default_factory=set)

    # ==================== Variable typing (VarTypingScanner) ====================
    #
    # Most of these are keyed by function scope (None == global) because
    # the same variable name may carry different types in different
    # functions. ``array_vars`` and a few others are global-only because
    # they were that way on the legacy mixin; folding them into per-scope
    # dicts is a future cleanup.

    string_vars: Dict[Optional[str], Set[str]] = field(default_factory=dict)
    vec256_vars: Dict[Optional[str], Set[str]] = field(default_factory=dict)
    vec512_vars: Dict[Optional[str], Set[str]] = field(default_factory=dict)
    array_vars: Set[str] = field(default_factory=set)
    dict_vars: Set[str] = field(default_factory=set)
    dyn_array_vars: Set[str] = field(default_factory=set)
    enum_vars: Set[str] = field(default_factory=set)
    # Variable -> AILang type name (for typeof() and field-access type-resolution).
    var_types: Dict[str, str] = field(default_factory=dict)
    # Owned-string locals read exactly once -- consume-on-read candidates.
    single_use_owned_strings: Set[str] = field(default_factory=set)

    # ==================== Queries ====================
    #
    # All queries are pure: ``self`` (TypeInfo) + arguments in, answer out.
    # The emit phases call these instead of poking around in
    # ``classes`` / ``var_types`` / ``string_vars`` directly. New queries
    # belong here as long as they're answerable from ``TypeInfo`` alone.

    # Builtins whose AILang return type is a string. Used by both the
    # helper-scan-time check (``is_string_expr_for_scan``) and the more
    # complete ``might_be_string`` query during emit.
    _STRING_RETURNING_BUILTINS: frozenset = frozenset(
        {
            "str",
            "chr",
            "substr",
            "concat",
            "read_stdin",
            "read_file",
            "input",
            "hex",
            "bin",
            "oct",
            "str_replace",
            "typeof",
            "str_array_get",
            "str_array_join",
            "dict_key_at",
            "fn_call_str",
            "split_str_get",
            "dict_get_string",
            "dict_get_type",
            "str_array_pop",
            "tcp_recv",
            "win32_full_path",
            "current_dir",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "argv",
            "getenv",
        }
    )
    # Same set + ``ailang_strcat`` (the lowered form of `+` on strings,
    # which appears in the AST after some passes). Used by the
    # post-scan queries; the pre-scan check intentionally excludes
    # ``ailang_strcat`` because the lowering hasn't happened yet.
    _STRING_RETURNING_BUILTINS_POSTSCAN: frozenset = frozenset(
        {
            "str",
            "ailang_strcat",
            "chr",
            "substr",
            "concat",
            "read_stdin",
            "read_file",
            "input",
            "hex",
            "bin",
            "oct",
            "str_replace",
            "typeof",
            "str_array_get",
            "str_array_join",
            "dict_key_at",
            "fn_call_str",
            "split_str_get",
            "dict_get_string",
            "dict_get_type",
            "str_array_pop",
            "tcp_recv",
            "win32_full_path",
            "current_dir",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "argv",
            "getenv",
        }
    )

    def is_string_expr_for_scan(self, node: A.ASTNode) -> bool:
        """Pre-scan check: 'is this expression a string?' Used by the
        helper scanner before var-typing has populated. Only consults
        the literal forms, the string-returning-builtin set, and any
        already-tracked string vars."""
        if isinstance(node, (A.StringLit, A.InterpolatedString, A.StringSlice)):
            return True
        if isinstance(node, A.Call):
            if node.name in self._STRING_RETURNING_BUILTINS:
                return True
            if node.name in self.functions:
                _params, ret_type = self.functions[node.name]
                if ret_type in ("const char *", "char *", "string"):
                    return True
        if isinstance(node, A.BinaryOp) and node.op == "+":
            return self.is_string_expr_for_scan(
                node.left
            ) or self.is_string_expr_for_scan(node.right)
        if isinstance(node, A.Variable):
            for scope_vars in self.string_vars.values():
                if node.name in scope_vars:
                    return True
        return False

    def might_be_string_static(
        self, node: A.ASTNode, func_scope: Optional[str]
    ) -> bool:
        """Static check used during the var-typing scan itself: the
        scanner needs to ask 'is this RHS a string?' to decide whether
        to record the LHS as a string var. Same body as
        ``might_be_string`` but uses an explicit ``func_scope`` rather
        than the emit-time ``current_function``."""
        if isinstance(node, (A.StringLit, A.InterpolatedString, A.StringSlice)):
            return True
        if (
            isinstance(node, A.Call)
            and node.name in self._STRING_RETURNING_BUILTINS_POSTSCAN
        ):
            return True
        if isinstance(node, A.Call) and node.name in self.functions:
            _params, ret_type = self.functions[node.name]
            if ret_type in ("const char *", "char *", "string"):
                return True
        if isinstance(node, A.BinaryOp) and node.op == "+":
            return self.might_be_string_static(
                node.left, func_scope
            ) or self.might_be_string_static(node.right, func_scope)
        if isinstance(node, A.Variable):
            if node.name in self.enum_vars:
                return False
            if (
                func_scope in self.string_vars
                and node.name in self.string_vars[func_scope]
            ):
                return True
            if None in self.string_vars and node.name in self.string_vars[None]:
                return True
        return False

    def might_be_string(self, node: A.ASTNode, current_function: Optional[str]) -> bool:
        """Full string-typing check used during emit. Accepts user-fn
        return types in addition to the built-in string-returning set
        consulted by ``might_be_string_static``."""
        if isinstance(node, (A.StringLit, A.InterpolatedString, A.StringSlice)):
            return True
        if isinstance(node, A.Call):
            if node.name in self._STRING_RETURNING_BUILTINS_POSTSCAN:
                return True
            if node.name in self.functions:
                _params, ret_type = self.functions[node.name]
                if ret_type in ("const char *", "char *", "string"):
                    return True
        if isinstance(node, A.BinaryOp) and node.op == "+":
            return self.might_be_string(
                node.left, current_function
            ) or self.might_be_string(node.right, current_function)
        if isinstance(node, A.Variable):
            if node.name in self.enum_vars:
                return False
            if (
                current_function in self.string_vars
                and node.name in self.string_vars[current_function]
            ):
                return True
            if None in self.string_vars and node.name in self.string_vars[None]:
                return True
        return False

    def field_ailang_type(self, parent_class: str, field_name: str) -> Optional[str]:
        """Return the AILang type name of a class field, or None."""
        info = self.classes.get(parent_class)
        if not info:
            return None
        fields, _methods = info
        for _vis, fname, ftype in fields:
            if fname == field_name:
                return ftype
        return None

    def class_ptr_type(
        self, node: A.ASTNode, current_class: Optional[str]
    ) -> Optional[str]:
        """Return the class name if ``node`` evaluates to a class pointer.

        Classes have pointer semantics in C output. Many code-gen
        decisions (`->` vs `.`, method receiver, array_push casts)
        depend on knowing whether an expression carries a class
        pointer. ``current_class`` is the class whose method we're
        currently emitting (so ``this`` resolves correctly); pass
        ``None`` outside method bodies.
        """
        if isinstance(node, A.ThisExpr):
            return current_class
        if isinstance(node, A.Variable):
            t = self.var_types.get(node.name)
            if t and t in self.classes:
                return t
        if isinstance(node, A.NewExpr) and node.type_name in self.classes:
            return node.type_name
        if isinstance(node, A.FieldAccess):
            parent_cls = self.class_ptr_type(node.object_expr, current_class)
            if parent_cls is None and isinstance(node.object_expr, A.ThisExpr):
                parent_cls = current_class
            if parent_cls is not None:
                ft = self.field_ailang_type(parent_cls, node.field_name)
                if ft and ft in self.classes:
                    return ft
        if isinstance(node, A.Call):
            if node.name == "as_class" and len(node.args) >= 2:
                tn = arg_at(node, 1)
                if isinstance(tn, A.StringLit) and tn.value in self.classes:
                    return tn.value
                if isinstance(tn, A.Variable) and tn.name in self.classes:
                    return tn.name
            if node.name in self.functions:
                _params, ret = self.functions[node.name]
                if ret in self.classes:
                    return ret
        return None

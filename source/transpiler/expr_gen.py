"""Expression generation for the C transpiler.

``expr()`` is the AST-to-C dispatch root. The instance proxies state through a
``CTranspiler`` back-reference, preserving the old mixin contract.
"""

from __future__ import annotations

from parser import ast as A
from typing import Any, List, Optional, Set

from ast_access import arg_at
from transpiler.class_field_ownership import (
    auto_owned_field_kind,
    is_auto_owned_field_type,
    is_auto_owned_param,
    is_string_type,
    string_len_field_name,
    string_len_param_name,
)
from transpiler.expr_string_fastpath import static_string_byte_length
from transpiler.expr_strlen_dynamic import emit_dynamic_strlen_c
from transpiler.strlen_cache import lookup_c_strlen_cache

from .expr_gen_array_impl import _can_elide_index_safety as _m_can_elide_index_safety
from .expr_gen_array_impl import _expr_array_access as _m_expr_array_access
from .expr_gen_array_impl import _known_array_len_hint as _m_known_array_len_hint
from .expr_gen_basic_impl import _expr_comptime as _m_expr_comptime
from .expr_gen_basic_impl import _expr_field_access as _m_expr_field_access
from .expr_gen_basic_impl import (
    _expr_interpolated_string as _m_expr_interpolated_string,
)
from .expr_gen_basic_impl import _expr_literal as _m_expr_literal
from .expr_gen_basic_impl import _expr_string_slice as _m_expr_string_slice
from .expr_gen_basic_impl import _expr_ternary_op as _m_expr_ternary_op
from .expr_gen_basic_impl import _expr_tuple_lit as _m_expr_tuple_lit
from .expr_gen_basic_impl import _expr_unary_op as _m_expr_unary_op
from .expr_gen_binary_impl import _expr_binary_op as _m_expr_binary_op
from .expr_gen_call_entry import _generate_call as _m_generate_call
from .expr_gen_call_syscall import _emit_syscall_call as _m_emit_syscall_call
from .expr_gen_type_impl import _infer_type as _m_infer_type
from .expr_gen_type_impl import _infer_typeof as _m_infer_typeof
from .expr_gen_type_impl import _infer_vec_call_type as _m_infer_vec_call_type


class CExprEmitter:
    """Expression-emit service backed by a ``CTranspiler`` reference."""

    # State annotations document what the proxied transpiler exposes.
    # mypy uses these to type-check the legacy ``self.X`` access patterns
    # in the method bodies below; at runtime every read/write is
    # forwarded to the transpiler via ``__getattr__`` / ``__setattr__``.
    output: List[str]
    current_function: Optional[str]
    user_defined_funcs: Set[str]
    _current_class: Optional[str]
    _unchecked_mode: bool
    _scanning_unchecked: bool
    _loop_depth: int

    def __init__(self, transpiler: object) -> None:
        # Bypass our custom ``__setattr__`` for the back-ref itself --
        # otherwise the assignment would recurse forever.
        object.__setattr__(self, "_t", transpiler)

    def __getattr__(self, name: str) -> Any:
        # Called only when the attribute isn't on this instance. Forward
        # to the transpiler so legacy ``self.output`` / ``self.type_info``
        # / ``self._class_locals_for_cleanup`` / etc. all keep working
        # without any change to method bodies. Returns ``Any`` so mypy
        # doesn't infer ``object`` for every legacy ``self.X`` access
        # in the method bodies below.
        return getattr(self._t, name)

    def __setattr__(self, name: str, value: object) -> None:
        # Forward every write to the transpiler so per-function emit
        # state (declared_vars, _class_locals_for_cleanup, etc.)
        # mutates on the orchestrator, not on a stale local copy.
        setattr(self._t, name, value)

    _expr_array_access = _m_expr_array_access
    _known_array_len_hint = _m_known_array_len_hint
    _can_elide_index_safety = _m_can_elide_index_safety

    def expr(self, node: A.ASTNode) -> str:
        """Generate C expression from AST node.
        Dispatches to specialized handlers by node type category.
        """
        # Literals
        if isinstance(node, (A.Number, A.Bool, A.Null, A.StringLit)):
            return self._expr_literal(node)
        if isinstance(node, A.InterpolatedString):
            return self._expr_interpolated_string(node)
        # Variables and access
        if isinstance(node, A.Variable):
            return self._mangle_var(node.name)
        if isinstance(node, A.ArrayAccess):
            return self._expr_array_access(node)
        if isinstance(node, A.FieldAccess):
            return self._expr_field_access(node)
        if isinstance(node, A.ThisExpr):
            inline_this = getattr(self, "_inline_this_expr", None)
            if inline_this is not None:
                return inline_this
            return "self"
        # Operators
        if isinstance(node, A.BinaryOp):
            return self._expr_binary_op(node)
        if isinstance(node, A.UnaryOp):
            return self._expr_unary_op(node)
        if isinstance(node, A.TernaryOp):
            return self._expr_ternary_op(node)
        # Collections
        if isinstance(node, A.ArrayLit):
            elements = ", ".join(self.expr(e) for e in node.elements)
            return f"(int64_t[]){{ {elements} }}"
        if isinstance(node, A.TupleLit):
            return self._expr_tuple_lit(node)
        if isinstance(node, A.TupleAccess):
            return f"{self.expr(node.tuple_expr)}._{node.index}"
        if isinstance(node, A.DictLit):
            return f"_dict_{id(node)}"
        if isinstance(node, A.DictAccess):
            return f"dict_get({self.expr(node.dict_expr)}, {self.expr(node.key_expr)})"
        # Calls and construction
        if isinstance(node, A.Call):
            return self._generate_call(node)
        if isinstance(node, A.NewExpr):
            return self._expr_new(node)
        if isinstance(node, A.MethodCall):
            return self._expr_method_call(node)
        if isinstance(node, A.EnumConstruct):
            return self._expr_enum_construct(node)
        if isinstance(node, A.EnumFieldAccess):
            return f"{self.expr(node.expr)}.data.{node.field_name}"
        # Type operations
        if isinstance(node, A.Cast):
            val = self.expr(node.expr)
            ctype = self._ailang_type_to_c(node.target_type)
            return f"(({ctype})({val}))"
        if isinstance(node, A.ReinterpretCast):
            val = self.expr(node.value)
            ctype = self._ailang_type_to_c(node.target_type)
            if "*" in ctype:
                return f"(({ctype})((void *)(uintptr_t)({val})))"
            return f"(({ctype})(uintptr_t)({val}))"
        # Strings and slicing
        if isinstance(node, A.StringSlice):
            return self._expr_string_slice(node)
        if isinstance(node, A.Range):
            return f"/* range {self.expr(node.start)}..{self.expr(node.end)} */"
        # List comprehension placeholder
        if isinstance(node, A.ListComprehension):
            return f"_listcomp_{id(node)}"
        # Compile-time evaluation
        if isinstance(node, A.ComptimeExpr):
            return self._expr_comptime(node)
        # Await (synchronous in C)
        if isinstance(node, A.Await):
            return self.expr(node.expr)
        # Concurrency (threading, atomics, channels)
        concurrency_result = self._expr_concurrency(node)
        if concurrency_result is not None:
            return concurrency_result
        # Generic binary op fallback
        if hasattr(node, "op") and hasattr(node, "left") and hasattr(node, "right"):
            left = self.expr(node.left)
            right = self.expr(node.right)
            op = "&&" if node.op == "and" else ("||" if node.op == "or" else node.op)
            return f"({left} {op} {right})"
        return f"/* unknown: {type(node).__name__} */"

    def _flatten_string_concat(self, node: A.BinaryOp) -> Optional[List[A.ASTNode]]:
        """Walk a left-associative `+` chain of strings into a flat
        list of operand nodes. Returns None if any operand isn't
        clearly string-typed (so we fall back to the pairwise path)."""
        operands: List[A.ASTNode] = []

        def walk(n: A.ASTNode) -> bool:
            if (
                isinstance(n, A.BinaryOp)
                and n.op == "+"
                and (self._might_be_string(n.left) or self._might_be_string(n.right))
            ):
                if not walk(n.left):
                    return False
                return walk(n.right)
            if not self._might_be_string(n):
                return False
            operands.append(n)
            return True

        if not walk(node):
            return None
        return operands

    def _emit_strcat_n(self, parts: List[A.ASTNode]) -> str:
        """Emit a single `ailang_strcat_n(N, parts_arr, owned_arr, lens_arr)`
        call covering all operands of a `+`-chain. The lens_arr lets the
        helper skip strlen() on parts whose length is known at compile
        time (string literals). perf showed strlen at ~17% of CPU after
        the SQLite/printf wins; literals account for ~half of strcat_n
        parts in the status hot path. `(size_t)-1` means
        "unknown, call strlen"."""
        rendered: List[str] = []
        owned: List[str] = []
        lens: List[str] = []
        for p in parts:
            rendered.append(f"(const char *)({self.expr(p)})")
            owned.append("1" if self._is_owned_string_alloc(p) else "0")
            known_len = static_string_byte_length(p)
            lens.append(str(known_len) if known_len is not None else "(size_t)-1")
        parts_lit = "(const char *const []){" + ", ".join(rendered) + "}"
        owned_lit = "(const int []){" + ", ".join(owned) + "}"
        lens_lit = "(const size_t []){" + ", ".join(lens) + "}"
        return f"ailang_strcat_n({len(parts)}, {parts_lit}, {owned_lit}, {lens_lit})"

    def _emit_lit_i64_concat(self, node: A.BinaryOp) -> Optional[str]:
        """Fuse `"literal" + str(i64)` into one allocation.

        The generic lowering allocates `str(i)` and then allocates the
        concatenated string. Hot protocol/object paths use this shape for
        small labels and IDs, so avoid the temporary when the prefix length
        is known at compile time.
        """
        if node.op != "+":
            return None
        if not isinstance(node.left, A.StringLit):
            return None
        if not (
            isinstance(node.right, A.Call)
            and node.right.name == "str"
            and len(node.right.args) == 1
        ):
            return None
        self.used_helpers.add("strcat")
        prefix = self.expr(node.left)
        prefix_len = len(node.left.value.encode("utf-8"))
        value = self.expr(arg_at(node.right, 0))
        return f"ailang_strcat_lit_i64({prefix}, {prefix_len}u, {value})"

    def _emit_virtual_strlen(self, node: A.ASTNode) -> Optional[str]:
        """Lower strlen("literal" + str(int)) without materializing a string."""
        if not isinstance(node, A.BinaryOp):
            return None
        if node.op not in ("+", "plus"):
            return None
        if not isinstance(node.left, A.StringLit):
            return None
        if not (
            isinstance(node.right, A.Call)
            and node.right.name == "str"
            and len(node.right.args) == 1
        ):
            return None
        value_node = arg_at(node.right, 0)
        if not self._str_arg_is_known_integer(value_node):
            return None
        prefix_len = len(node.left.value.encode("utf-8"))
        value = self.expr(value_node)
        self.used_helpers.add("i64_decimal_len")
        if prefix_len == 0:
            return f"ailang_i64_decimal_len({value})"
        return f"({prefix_len}LL + ailang_i64_decimal_len({value}))"

    def _emit_known_strlen(
        self, node: A.ASTNode, rendered: Optional[str] = None
    ) -> str:
        """Return a known/cached string length expression when possible."""
        static_len = static_string_byte_length(node)
        if static_len is not None:
            return f"{static_len}LL"
        if (
            isinstance(node, A.Call)
            and node.name == "str"
            and len(node.args) == 1
            and self._str_arg_is_known_integer(arg_at(node, 0))
        ):
            self.used_helpers.add("i64_decimal_len")
            return f"ailang_i64_decimal_len({self.expr(arg_at(node, 0))})"
        dynamic_len = emit_dynamic_strlen_c(self, node)
        if dynamic_len is not None:
            return dynamic_len
        virtual_len = self._emit_virtual_strlen(node)
        if virtual_len is not None:
            return virtual_len
        if isinstance(node, A.Variable):
            cached_len = lookup_c_strlen_cache(self, node)
            if cached_len is not None:
                return cached_len
            len_name = string_len_param_name(node.name)
            if len_name in getattr(self, "declared_vars", set()):
                return len_name
        if isinstance(node, A.FieldAccess):
            cached = self._cached_field_strlen(node)
            if cached is not None:
                return cached
        value = rendered if rendered is not None else self.expr(node)
        self.used_helpers.add("strlen")
        return f"ailang_strlen({value})"

    def _is_virtual_string_expr(self, node: A.ASTNode) -> bool:
        if not isinstance(node, A.BinaryOp) or node.op not in ("+", "plus"):
            return False
        if not isinstance(node.left, A.StringLit):
            return False
        if not (
            isinstance(node.right, A.Call)
            and node.right.name == "str"
            and len(node.right.args) == 1
        ):
            return False
        return self._str_arg_is_known_integer(arg_at(node.right, 0))

    def _can_elide_virtual_string_arg(
        self, class_name: str, method_name: str, param_index: int, arg: A.ASTNode
    ) -> bool:
        return self._is_virtual_string_expr(arg) and (
            class_name,
            method_name,
            param_index,
        ) in getattr(self, "_virtual_string_elidable_params", set())

    def _cached_field_strlen(self, node: A.FieldAccess) -> Optional[str]:
        owner_class = None
        if isinstance(node.object_expr, A.ThisExpr):
            owner_class = self._current_class
        elif self._class_ptr_type(node.object_expr) is not None:
            owner_class = self._class_ptr_type(node.object_expr)
        if owner_class is None:
            return None
        if not is_string_type(self._field_ailang_type(owner_class, node.field_name)):
            return None
        hidden = string_len_field_name(node.field_name)
        if isinstance(node.object_expr, A.ThisExpr):
            inline_this = getattr(self, "_inline_this_expr", None)
            if inline_this is not None:
                return f"{inline_this}->{hidden}"
            return f"self->{hidden}"
        obj = self.expr(node.object_expr)
        if self._class_ptr_type(node.object_expr) is not None:
            return f"{obj}->{hidden}"
        return f"{obj}.{hidden}"

    def _str_arg_is_known_integer(self, node: A.ASTNode) -> bool:
        if isinstance(node, A.Number):
            return not node.is_float
        if isinstance(node, A.Variable):
            var_type = getattr(self, "_var_types", {}).get(node.name)
            return var_type is not None and self._is_integer_type_name(var_type)
        if isinstance(node, A.UnaryOp):
            return node.op in ("+", "plus", "-", "minus") and (
                self._str_arg_is_known_integer(node.operand)
            )
        if isinstance(node, A.BinaryOp):
            if node.op not in {
                "+",
                "plus",
                "-",
                "minus",
                "*",
                "%",
                "/",
                "//",
                "mod",
            }:
                return False
            return self._str_arg_is_known_integer(
                node.left
            ) and self._str_arg_is_known_integer(node.right)
        return False

    def _is_integer_type_name(self, type_name: Any) -> bool:
        lowered = str(type_name).strip().lower()
        if lowered in {
            "int",
            "integer",
            "long",
            "short",
            "byte",
            "i8",
            "i16",
            "i32",
            "i64",
            "isize",
            "uint",
            "ulong",
            "ushort",
            "ubyte",
            "u8",
            "u16",
            "u32",
            "u64",
            "usize",
        }:
            return True
        if lowered.startswith("int") and lowered[3:].isdigit():
            return True
        return lowered.startswith("uint") and lowered[4:].isdigit()

    def _expr_new(self, node: A.NewExpr) -> str:
        """Generate C code for new expressions.
        Classes -> call the per-class `Class_new(args)` wrapper emitted
        in visit_ClassDef. Records keep value-typed struct-literal init.
        """
        if node.type_name in self.classes:
            class_info = self.classes.get(node.type_name)
            fields, methods = class_info if class_info else ([], [])
            init_method = next((m for m in methods if m.name == "init"), None)
            ownership_sources = (
                init_method.params if init_method is not None else fields
            )
            call_args: list[str] = []
            for index, arg in enumerate(node.args):
                arg_expr = self.expr(arg)
                call_args.append(arg_expr)
                if index < len(ownership_sources):
                    source = ownership_sources[index]
                    if init_method is not None:
                        needs_flag = is_auto_owned_param(source, self.classes)
                        needs_len = (
                            isinstance(source, tuple)
                            and len(source) >= 2
                            and is_string_type(source[1])
                        )
                        kind = (
                            auto_owned_field_kind(source[1], self.classes)
                            if isinstance(source, tuple) and len(source) >= 2
                            else None
                        )
                        source_type = source[1] if isinstance(source, tuple) else None
                    else:
                        needs_flag = is_auto_owned_field_type(source[2], self.classes)
                        needs_len = is_string_type(source[2])
                        kind = auto_owned_field_kind(source[2], self.classes)
                        source_type = source[2]
                    if needs_len:
                        call_args.append(self._emit_known_strlen(arg, arg_expr))
                    if needs_flag and kind is not None:
                        owned = self._expr_produces_owned_value(arg, kind, source_type)
                        call_args.append("1" if owned else "0")
            args = ", ".join(call_args)
            return f"{node.type_name}_new({args})"
        new_args = ", ".join(self.expr(a) for a in node.args)
        return f"({node.type_name}){{ {new_args} }}"

    def _expr_method_call(self, node: A.MethodCall) -> str:
        """Generate C code for method calls."""
        # Check if this is enum construction: EnumName.Variant(args)
        if isinstance(node.object_expr, A.Variable):
            enum_name = node.object_expr.name
            variant_name = node.method_name
            if enum_name in self.data_enums:
                data_variants = self.data_enums[enum_name]
                if variant_name in data_variants:
                    variant_fields = data_variants[variant_name]
                    args = [self.expr(a) for a in node.args]
                    field_inits = ", ".join(
                        f".{field_name} = {arg}"
                        for (field_name, _), arg in zip(
                            variant_fields, args, strict=False
                        )
                    )
                    return (
                        f"(({enum_name}){{ "
                        f".tag = {enum_name}_TAG_{variant_name}, "
                        f".data.{variant_name.lower()} = {{ {field_inits} }} }})"
                    )
                return f"(({enum_name}){{ .tag = {enum_name}_TAG_{variant_name} }})"
        return self._emit_method_call_text(node)

    def _expr_enum_construct(self, node: A.EnumConstruct) -> str:
        """Generate C code for enum construction."""
        enum_name = node.enum_name
        variant_name = node.variant_name
        if enum_name in self.data_enums:
            data_variants = self.data_enums[enum_name]
            if variant_name in data_variants:
                variant_fields = data_variants[variant_name]
                args = [self.expr(a) for a in node.args]
                field_inits = ", ".join(
                    f".{field_name} = {arg}"
                    for (field_name, _), arg in zip(variant_fields, args, strict=False)
                )
                return (
                    f"(({enum_name}){{ "
                    f".tag = {enum_name}_TAG_{variant_name}, "
                    f".data.{variant_name.lower()} = {{ {field_inits} }} }})"
                )
        return f"{enum_name}_{variant_name}"

    def _expr_concurrency(self, node: A.ASTNode) -> Optional[str]:
        """Generate C code for concurrency-related expressions."""
        # Threading: spawn - create new thread.
        # ailang_spawn / per-target callers return `ailang_thread_t *` but
        # AILang stores thread handles as int64. Cast through uintptr_t
        # so the assignment to an int variable doesn't trigger
        # implicit-conversion errors under -Wpedantic / clang.
        if isinstance(node, A.Spawn):
            if isinstance(node.func_call, A.Call):
                func_name = node.func_call.name
                call_args = node.func_call.args or []
                # Zero-arg spawn: legacy direct path, no boxing needed.
                if not call_args:
                    return (
                        f"(int64_t)(uintptr_t)ailang_spawn"
                        f"((ailang_thread_func_t){func_name}, nullptr)"
                    )
                # Args present: route through the per-target caller helper
                # generated by _emit_spawn_thunks.
                if func_name in self._spawn_targets:
                    arg_strs = [self.expr(a) for a in call_args]
                    return (
                        f"(int64_t)(uintptr_t){self._spawn_caller_name(func_name)}"
                        f"({', '.join(arg_strs)})"
                    )
                # Function not in our user-defined table -- fall back.
                return (
                    f"(int64_t)(uintptr_t)ailang_spawn"
                    f"((ailang_thread_func_t){func_name}, nullptr)"
                )
            return "/* spawn: complex func_call not yet supported */"
        # Threading: join - wait for thread. Reverse the cast that spawn
        # applied: AILang stores the handle as int64; ailang_join takes
        # the original ailang_thread_t* pointer.
        if isinstance(node, A.Join):
            handle = self.expr(node.handle)
            return f"ailang_join((ailang_thread_t *)(uintptr_t)({handle}))"
        # Atomic operations
        if isinstance(node, A.AtomicOp):
            return self._expr_atomic(node)
        # Channel operations
        return self._expr_channel(node)

    def _expr_atomic(self, node: A.AtomicOp) -> str:
        """Generate C code for atomic operations."""
        ptr = self.expr(node.ptr)
        if node.op == "load":
            return f"ailang_atomic_load(&{ptr})"
        if node.op == "store":
            val = self.expr(node.value) if node.value else "0"
            return f"(ailang_atomic_store(&{ptr}, {val}), 0)"
        if node.op == "add":
            val = self.expr(node.value) if node.value else "0"
            return f"ailang_atomic_add(&{ptr}, {val})"
        if node.op == "sub":
            val = self.expr(node.value) if node.value else "0"
            return f"ailang_atomic_sub(&{ptr}, {val})"
        if node.op == "exchange":
            val = self.expr(node.value) if node.value else "0"
            return f"ailang_atomic_exchange(&{ptr}, {val})"
        if node.op == "cmpxchg":
            expected = self.expr(node.expected) if node.expected else "0"
            desired = self.expr(node.value) if node.value else "0"
            return f"ailang_atomic_cas(&{ptr}, {expected}, {desired})"
        return f"/* atomic_{node.op}: unknown operation */"

    def _expr_channel(self, node: A.ASTNode) -> Optional[str]:
        """Generate C code for channel operations.
        Like the SQLite handle bridge: AILang exposes channels as
        int64_t to user code, but the C runtime works with
        `ailang_channel_t *`. Cast at every boundary.
        """
        if isinstance(node, A.ChannelCreate):
            capacity = self.expr(node.capacity)
            return f"((int64_t)(uintptr_t)ailang_channel_create({capacity}))"
        if isinstance(node, A.ChannelSend):
            ch = self.expr(node.channel)
            val = self.expr(node.value)
            return (
                f"(ailang_channel_send("
                f"(ailang_channel_t *)(uintptr_t)({ch}), {val}), 0)"
            )
        if isinstance(node, A.ChannelRecv):
            ch = self.expr(node.channel)
            return f"ailang_channel_recv(" f"(ailang_channel_t *)(uintptr_t)({ch}))"
        if isinstance(node, A.ChannelTrySend):
            ch = self.expr(node.channel)
            val = self.expr(node.value)
            return (
                f"ailang_channel_try_send("
                f"(ailang_channel_t *)(uintptr_t)({ch}), {val})"
            )
        if isinstance(node, A.ChannelTryRecv):
            ch = self.expr(node.channel)
            return (
                f"ailang_channel_try_recv("
                f"(ailang_channel_t *)(uintptr_t)({ch}), &_try_recv_success)"
            )
        if isinstance(node, A.ChannelClose):
            ch = self.expr(node.channel)
            return (
                f"(ailang_channel_close(" f"(ailang_channel_t *)(uintptr_t)({ch})), 0)"
            )
        return None

    _expr_binary_op = _m_expr_binary_op
    _expr_comptime = _m_expr_comptime
    _expr_field_access = _m_expr_field_access
    _expr_interpolated_string = _m_expr_interpolated_string
    _expr_literal = _m_expr_literal
    _expr_string_slice = _m_expr_string_slice
    _expr_ternary_op = _m_expr_ternary_op
    _expr_tuple_lit = _m_expr_tuple_lit
    _expr_unary_op = _m_expr_unary_op
    _emit_syscall_call = _m_emit_syscall_call
    _generate_call = _m_generate_call
    _infer_vec_call_type = _m_infer_vec_call_type
    _infer_type = _m_infer_type
    _infer_typeof = _m_infer_typeof

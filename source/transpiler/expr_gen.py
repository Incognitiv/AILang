"""Expression generation for the C transpiler.

``expr()`` is the AST-to-C dispatch root. The instance proxies state through a
``CTranspiler`` back-reference, preserving the old mixin contract.
"""

from __future__ import annotations

from parser import ast as A
from typing import Any

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
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr
from transpiler.strlen_assign_cache import baseconv_known_integer_arg, baseconv_len_expr
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
from .expr_gen_concurrency_mixin import CExprConcurrencyMixin
from .expr_gen_type_impl import _infer_type as _m_infer_type
from .expr_gen_type_impl import _infer_typeof as _m_infer_typeof
from .expr_gen_type_impl import _infer_vec_call_type as _m_infer_vec_call_type


class CExprEmitter(CExprConcurrencyMixin):
    """Expression-emit service backed by a ``CTranspiler`` reference."""

    # State annotations document what the proxied transpiler exposes.
    # mypy uses these to type-check the legacy ``self.X`` access patterns
    # in the method bodies below; at runtime every read/write is
    # forwarded to the transpiler via ``__getattr__`` / ``__setattr__``.
    output: list[str]
    current_function: str | None
    user_defined_funcs: set[str]
    _current_class: str | None
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
            if isinstance(node.dict_expr, A.Variable) and isinstance(
                node.key_expr, A.StringLit
            ):
                scalar_values = getattr(self, "_fixed_dict_scalar_values", {})
                var_name = node.dict_expr.name
                key = node.key_expr.value
                if var_name in scalar_values and key in scalar_values[var_name]:
                    return scalar_values[var_name][key]
                slots = getattr(self, "_fixed_dict_literal_slots", {})
                if var_name in slots and key in slots[var_name]:
                    return (
                        f"{self.expr(node.dict_expr)}"
                        f"->entries[{slots[var_name][key]}].value"
                    )
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
            source_type = self._infer_type(node.value)
            target_is_ptr = "*" in ctype
            source_is_ptr = "*" in source_type
            if target_is_ptr and not source_is_ptr:
                return f"(({ctype})((void *)(uintptr_t)({val})))"
            if not target_is_ptr and source_is_ptr:
                return f"(({ctype})(uintptr_t)({val}))"
            # Integer-to-integer reinterpretation must preserve the full source
            # bit-pattern.  Routing every cast through uintptr_t silently
            # truncated i128..i8192 to the host pointer width.
            return f"(({ctype})({val}))"
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

    def _flatten_string_concat(self, node: A.BinaryOp) -> list[A.ASTNode] | None:
        """Walk a left-associative `+` chain of strings into a flat
        list of operand nodes. Returns None if any operand isn't
        clearly string-typed (so we fall back to the pairwise path)."""
        operands: list[A.ASTNode] = []

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

    def _emit_strcat_n(self, parts: list[A.ASTNode]) -> str:
        """Emit a single `ailang_strcat_n(N, parts_arr, owned_arr, lens_arr)`
        call covering all operands of a `+`-chain. The lens_arr lets the
        helper skip strlen() on parts whose length is known at compile
        time (string literals). perf showed strlen at ~17% of CPU after
        the SQLite/printf wins; literals account for ~half of strcat_n
        parts in the status hot path. `(size_t)-1` means
        "unknown, call strlen"."""
        rendered: list[str] = []
        owned: list[str] = []
        lens: list[str] = []
        for p in parts:
            rendered.append(f"(const char *)({self.expr(p)})")
            owned.append("1" if self._is_owned_string_alloc(p) else "0")
            known_len = static_string_byte_length(p)
            lens.append(str(known_len) if known_len is not None else "(size_t)-1")
        parts_lit = "(const char *const []){" + ", ".join(rendered) + "}"
        owned_lit = "(const int []){" + ", ".join(owned) + "}"
        lens_lit = "(const size_t []){" + ", ".join(lens) + "}"
        return f"ailang_strcat_n({len(parts)}, {parts_lit}, {owned_lit}, {lens_lit})"

    def _emit_lit_i64_concat(self, node: A.BinaryOp) -> str | None:
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

    def _emit_virtual_strlen(self, node: A.ASTNode) -> str | None:
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

    def _emit_known_strlen(self, node: A.ASTNode, rendered: str | None = None) -> str:
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
        base_arg = baseconv_known_integer_arg(self, node)
        if base_arg is not None:
            kind, arg = base_arg
            return baseconv_len_expr(self, kind, arg)
        if isinstance(node, A.InterpolatedString):
            parts: list[str] = []
            for part in node.parts:
                if isinstance(part, str):
                    parts.append(f"{len(part.encode('utf-8'))}LL")
                    continue
                part_len = self._emit_known_strlen(part)
                if part_len.startswith("ailang_strlen("):
                    break
                parts.append(part_len)
            else:
                if not parts:
                    return "0LL"
                if len(parts) == 1:
                    return parts[0]
                return "(" + " + ".join(parts) + ")"
        # Prefer the allocation-free numeric specialization before the
        # generic dynamic planner. Hidden string-length arguments must not
        # materialize str(i) merely to measure it.
        virtual_len = self._emit_virtual_strlen(node)
        if virtual_len is not None:
            return virtual_len
        dynamic_len = emit_dynamic_strlen_c(self, node)
        if dynamic_len is not None:
            return dynamic_len
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

    def _cached_field_strlen(self, node: A.FieldAccess) -> str | None:
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
            local_types = getattr(self, "_current_local_c_types", None)
            var_type = (
                local_types.get(node.name) if isinstance(local_types, dict) else None
            )
            if var_type is None:
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
            "int8_t",
            "int16_t",
            "int32_t",
            "int64_t",
            "uint8_t",
            "uint16_t",
            "uint32_t",
            "uint64_t",
            "size_t",
            "ssize_t",
            "long long",
            "unsigned long long",
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
            source_args = list(node.args)
            if init_method is None:
                if len(source_args) > len(fields):
                    raise ValueError(
                        f"{node.type_name} expects at most {len(fields)} constructor "
                        f"arguments, got {len(source_args)}"
                    )
                defaults = self.type_info.class_field_defaults.get(node.type_name, {})
                for source in fields[len(source_args) :]:
                    field_name = source[1] if len(source) >= 2 else ""
                    default_expr = defaults.get(str(field_name))
                    if default_expr is None:
                        raise ValueError(
                            f"{node.type_name} requires constructor argument for field "
                            f"'{field_name}'"
                        )
                    source_args.append(default_expr)
            call_args: list[str] = []
            for index, arg in enumerate(source_args):
                arg_expr = self.expr(arg)
                source_type = None
                if index < len(ownership_sources):
                    source = ownership_sources[index]
                    if init_method is not None:
                        source_type = (
                            source[1]
                            if isinstance(source, tuple) and len(source) >= 2
                            else None
                        )
                    else:
                        source_type = (
                            source[2]
                            if isinstance(source, tuple) and len(source) >= 3
                            else None
                        )
                    if source_type is not None:
                        checked_arg = checked_fixed_int_conversion_expr(
                            self, arg, arg_expr, source_type
                        )
                        if checked_arg is not None:
                            arg_expr = checked_arg
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
        record_fields = self.records.get(node.type_name, [])
        source_args = list(node.args)
        if len(source_args) > len(record_fields):
            raise ValueError(
                f"{node.type_name} expects at most {len(record_fields)} constructor "
                f"arguments, got {len(source_args)}"
            )
        defaults = self.type_info.record_defaults.get(node.type_name, {})
        for field_name, _field_type in record_fields[len(source_args) :]:
            default_expr = defaults.get(field_name)
            if default_expr is None:
                raise ValueError(
                    f"{node.type_name} requires constructor argument for field "
                    f"'{field_name}'"
                )
            source_args.append(default_expr)
        converted_args: list[str] = []
        for index, arg in enumerate(source_args):
            arg_expr = self.expr(arg)
            if index < len(record_fields):
                _field_name, field_type = record_fields[index]
                checked_arg = checked_fixed_int_conversion_expr(
                    self, arg, arg_expr, field_type
                )
                if checked_arg is not None:
                    arg_expr = checked_arg
            converted_args.append(arg_expr)
        new_args = ", ".join(converted_args)
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
                    args = []
                    for (field_name, field_type), arg_node in zip(
                        variant_fields, node.args, strict=False
                    ):
                        arg_code = self.expr(arg_node)
                        checked_arg = checked_fixed_int_conversion_expr(
                            self, arg_node, arg_code, field_type
                        )
                        args.append(
                            checked_arg if checked_arg is not None else arg_code
                        )
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
                args = []
                for (field_name, field_type), arg_node in zip(
                    variant_fields, node.args, strict=False
                ):
                    arg_code = self.expr(arg_node)
                    checked_arg = checked_fixed_int_conversion_expr(
                        self, arg_node, arg_code, field_type
                    )
                    args.append(checked_arg if checked_arg is not None else arg_code)
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

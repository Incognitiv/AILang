"""Default-argument filling and final ABI coercion for C calls."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from transpiler.c_bigint import is_unbounded_spec, owned_bigint_expr
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr

from .expr_gen_call_common import (
    _NARROW_INT_LIMITS,
    _int_range_fits_c_type,
    _narrow_integer_arg_expr,
)


def finalize_call(
    self: Any,
    node: A.Call,
    call_arg_nodes: list[A.ASTNode],
    call_args: list[str],
) -> str:
    # Fill in default arguments if needed
    if node.name in self._func_defaults and node.name in self.functions:
        expected_params = len(self.functions[node.name][0])
        if len(call_args) < expected_params:
            # Fill in missing args with defaults
            defaults = self._func_defaults[node.name]
            for param_idx, default_val in defaults:
                if param_idx >= len(call_args):
                    call_arg_nodes.append(default_val)
                    if param_idx < len(
                        self.functions[node.name][0]
                    ) and is_unbounded_spec(
                        self, self.functions[node.name][0][param_idx]
                    ):
                        call_args.append(owned_bigint_expr(self, default_val))
                    else:
                        call_args.append(self.expr(default_val))

    # Use mangled name for function calls
    mangled_name = self._mangle_name(node.name)

    # Cast arguments to expected parameter types to suppress
    # -Wint-conversion and -Woverflow (narrowing) warnings
    if node.name in self.functions:
        param_types = self.functions[node.name][0]
        for i, arg_val in enumerate(call_args):
            if i < len(param_types):
                if is_unbounded_spec(self, param_types[i]):
                    continue
                c_type = self._ailang_type_to_c(param_types[i])
                arg_node = call_arg_nodes[i] if i < len(call_arg_nodes) else None
                if arg_node is not None:
                    checked_int = checked_fixed_int_conversion_expr(
                        self, arg_node, arg_val, param_types[i]
                    )
                    if checked_int is not None:
                        call_args[i] = checked_int
                        continue
                is_str_arg = arg_val.startswith('"') or (
                    i < len(node.args) and self._might_be_string(node.args[i])
                )
                # String → integer parameter: cast via uintptr_t
                # (AILang represents strings as int64_t pointers)
                if is_str_arg and c_type in (
                    "int64_t",
                    "int32_t",
                    "int16_t",
                    "int8_t",
                ):
                    call_args[i] = f"({c_type})(uintptr_t)({arg_val})"
                # Narrowing cast only for scalar/integral C types.
                # ISO C forbids casting nonscalar to nonscalar (-Wpedantic),
                # so never cast struct/record types (PascalCase or ailang_*).
                elif c_type in (
                    "int32_t",
                    "int16_t",
                    "int8_t",
                    "uint64_t",
                    "uint32_t",
                    "uint16_t",
                    "uint8_t",
                    "float",
                    "double",
                    "long double",
                    "bool",
                    "uintptr_t",
                    "size_t",
                    "ptrdiff_t",
                ):
                    arg_type = (
                        self._infer_type(arg_node)
                        if arg_node is not None
                        else "int64_t"
                    )
                    if (
                        arg_node is not None
                        and c_type in _NARROW_INT_LIMITS
                        and _int_range_fits_c_type(self, arg_node, c_type)
                    ):
                        call_args[i] = _narrow_integer_arg_expr(self, arg_node, c_type)
                        continue
                    if arg_type != c_type:
                        call_args[i] = f"({c_type})({arg_val})"

    args_str = ", ".join(call_args)
    return f"{mangled_name}({args_str})"

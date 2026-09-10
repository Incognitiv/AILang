"""Call/member/enum visitors for LLVM expression generation.

Extracted from ``emit_expressions.py`` as part of the LLVM-side
ExprGenerator decomposition. Method bodies are unchanged.
"""

from __future__ import annotations

from parser.ast import (
    BinaryOp,
    Call,
    Number,
    StringLit,
    Variable,
)
from typing import Any

from callback_types import callback_parts, resolve_callback_alias
from calling_conventions import llvm_calling_convention
from llvmlite import ir
from transpiler.expr_common import ExprGenError
from transpiler.llvm_bigint import (
    bigint_from_decimal_literal,
    clone_if_borrowed,
    fixed_to_bigint,
    is_bigint_value,
    is_unbounded_spec,
)
from transpiler.llvm_fixed_int_casts import (
    fixed_int_info_for_spec,
)
from transpiler.pure_eval import stable_literal_bindings, try_eval_call

from .expr_call_names import string_len_name as _string_len_name
from .expr_calls_object_mixin import ExprCallObjectMixin


class ExprCallEmitter(ExprCallObjectMixin):
    """Call/member/enum expression service for ``ExprGenerator``."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _emit_string_len_for_expr(
        self, node: Any, materialized_value: ir.Value | None = None
    ) -> ir.Value:
        hidden = None
        if isinstance(node, Variable):
            hidden = _string_len_name(node.name)
        if hidden and hidden in getattr(self.codegen, "locals", {}):
            return self.ensure_int64(self.codegen.locals[hidden])
        from codegen.strlen_fact_cache import lookup_strlen_fact

        cached_len = lookup_strlen_fact(self.codegen, node)
        if cached_len is not None:
            return self.ensure_int64(cached_len)
        virtual_len = self.codegen.builtin_string._try_emit_virtual_strlen(node)
        if virtual_len is not None:
            return virtual_len
        value = (
            materialized_value
            if materialized_value is not None
            else self.generate_expr(node)
        )
        return self.builder.call(
            self.codegen.get_strlen(), [value], name="cached_strlen"
        )

    def _is_virtual_string_expr(self, node: Any) -> bool:
        if not isinstance(node, BinaryOp) or node.op.lower() not in {"+", "plus"}:
            return False
        if not isinstance(node.left, StringLit):
            return False
        return (
            isinstance(node.right, Call)
            and node.right.name == "str"
            and len(node.right.args) == 1
        )

    def _can_elide_virtual_string_arg(
        self, class_name: str, method_name: str, param_index: int, arg: Any
    ) -> bool:
        return self._is_virtual_string_expr(arg) and (
            class_name,
            method_name,
            param_index,
        ) in getattr(self.codegen, "_virtual_string_elidable_params", set())

    def _check_module_function_visibility(self, node: Call) -> None:
        function_nodes = getattr(self.codegen, "_function_nodes", {})
        target_node = function_nodes.get(node.name)
        if target_node is None:
            return
        target_source = getattr(target_node, "_source_path", "")
        entry_source = getattr(self.codegen, "_compile_source_file", "")
        caller_name = getattr(self.codegen, "_current_function_name", None)
        caller_node = function_nodes.get(caller_name) if caller_name else None
        caller_source = getattr(caller_node, "_source_path", "") or entry_source
        if not target_source or target_source == caller_source:
            return
        if not getattr(target_node, "is_public", True):
            raise ExprGenError(
                f"Private function '{node.name}' is not visible outside its module"
            )
        if caller_source == entry_source and node.name not in getattr(
            self.codegen, "_entry_visible_function_names", set()
        ):
            raise ExprGenError(
                f"Function '{node.name}' is not imported into this module"
            )

    def visit_Call(self, node: Call):
        self._check_module_function_visibility(node)
        func_name = node.name.lower()
        # Correctness phase: do not pure-fold user functions before their
        # parameter/return contracts have been applied.  The old fast path
        # could bypass checked iN/uN conversions entirely (for example a
        # function declared ``(): u8`` returning ``i8(-1)``).  Typed pure
        # evaluation can be re-enabled later only when it proves semantic
        # equivalence with the runtime path.
        # Check user-defined functions FIRST to allow overriding builtins
        user_func = self.codegen.functions.get(node.name)
        if user_func is not None:
            return self._call_user_function(node, user_func)
        # Generic function call: trigger monomorphization on first use
        generic_base = getattr(node, "generic_base", None)
        if generic_base is not None:
            type_args = getattr(node, "generic_type_args", [])
            mangled = self.codegen.monomorphizer.instantiate(generic_base, type_args)
            # Generate the specialized function immediately, but save/restore
            # builder state so we don't corrupt the calling function's IR.
            from parser import ast as A

            for spec in self.codegen.monomorphizer.get_specialized_definitions():
                if isinstance(spec, A.Function) and spec.name == mangled:
                    if mangled not in self.codegen.functions:
                        saved_builder = self.codegen.builder
                        saved_locals = self.codegen.locals.copy()
                        saved_func = self.codegen.func
                        try:
                            self.codegen.declare_function(spec)
                            self.codegen.generate_function(spec)
                        finally:
                            self.codegen.builder = saved_builder
                            self.codegen.locals = saved_locals
                            self.codegen.func = saved_func
                    break
            user_func = self.codegen.functions.get(mangled)
            if user_func is not None:
                return self._call_user_function(node, user_func)
        callback_call = self._call_callback_variable(node)
        if callback_call is not None:
            return callback_call
        # Dispatch via table lookup for builtins (O(1) instead of O(n) if-chain)
        dispatch = self._get_call_dispatch()
        handler = dispatch.get(func_name)
        if handler is not None:
            # Special case: char_at with unsafe flag
            if func_name == "char_at" and getattr(node, "unsafe", False):
                return self.codegen.builtin_unsafe_char_at(node.args)
            if func_name == "char_at":
                return self.codegen.builtin_char_at(node.args, node)
            return handler(node.args)
        raise ExprGenError(f"Undefined function: {node.name}")

    def _try_fold_pure_call(self, node: Call) -> ir.Value | None:
        function_nodes = getattr(self.codegen, "_function_nodes", {})
        if not function_nodes:
            return None
        body = getattr(self.codegen, "_current_function_body", []) or []
        bindings = stable_literal_bindings(body)
        value = try_eval_call(function_nodes, node, bindings)
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int):
            return ir.Constant(ir.IntType(64), value)
        return None

    def _call_user_function(self, node: Call, func: ir.Function) -> ir.Value:
        """Call a user function while enforcing language-level value contracts."""
        provided_args = list(node.args)
        expected_count = len(func.args)
        is_variadic = bool(getattr(func.function_type, "var_arg", False))
        if len(provided_args) < expected_count:
            defaults = self.codegen.function_defaults.get(node.name, [])
            defaults_dict = dict(defaults)
            for i in range(len(provided_args), expected_count):
                if i in defaults_dict:
                    provided_args.append(defaults_dict[i])
                else:
                    raise ExprGenError(
                        f"Missing argument {i} for {node.name} with no default"
                    )
        elif len(provided_args) > expected_count and not is_variadic:
            raise ExprGenError(f"Too many arguments for {node.name}")

        function_node = getattr(self.codegen, "_function_nodes", {}).get(node.name)
        param_specs = list(getattr(function_node, "params", []) or [])
        arg_values: list[ir.Value] = []
        for index, (arg_node, expected) in enumerate(zip(provided_args, func.args)):
            param_spec = None
            if index < len(param_specs):
                param = param_specs[index]
                if isinstance(param, tuple) and len(param) >= 2:
                    param_spec = param[1]

            if param_spec is not None and is_unbounded_spec(self.codegen, param_spec):
                if isinstance(arg_node, Number):
                    arg_value = bigint_from_decimal_literal(
                        self.codegen, self.builder, arg_node
                    )
                else:
                    arg_value = self.generate_expr(arg_node)
                    if is_bigint_value(self.codegen, arg_value):
                        # Parameters own their incoming BigInt.  Clone borrowed
                        # variables/fields; fresh expression results transfer.
                        arg_value = clone_if_borrowed(
                            self.codegen, self.builder, arg_node, arg_value
                        )
                    elif isinstance(arg_value.type, ir.IntType):
                        arg_value = fixed_to_bigint(
                            self.codegen,
                            self.builder,
                            arg_value,
                            unsigned=self.codegen.is_unsigned_value(arg_value),
                        )
                    else:
                        raise TypeError(
                            f"Type mismatch for argument {index} of {node.name}: "
                            f"expected unbounded, got {arg_value.type}"
                        )
                if arg_value.type != expected.type:
                    raise TypeError(
                        f"Type mismatch for argument {index} of {node.name}: "
                        f"expected {expected.type}, got {arg_value.type}"
                    )
                arg_values.append(arg_value)
                continue

            arg_value = self.generate_expr(arg_node)
            target_info = (
                fixed_int_info_for_spec(self.codegen, param_spec)
                if param_spec is not None
                else None
            )
            needs_fixed_contract = (
                target_info is not None
                and isinstance(arg_value.type, ir.IntType)
                and isinstance(expected.type, ir.IntType)
            )
            if arg_value.type != expected.type or needs_fixed_contract:
                try:
                    arg_value = self.codegen.cast_value(
                        arg_value,
                        expected.type,
                        target_unsigned=(
                            target_info.unsigned if target_info is not None else None
                        ),
                    )
                except (TypeError, ValueError):
                    raise TypeError(
                        f"Type mismatch for argument {index} of {node.name}: "
                        f"expected {expected.type}, got {arg_value.type}"
                    ) from None
            arg_values.append(arg_value)

        if is_variadic and len(provided_args) > expected_count:
            for arg_node in provided_args[expected_count:]:
                arg_value = self.generate_expr(arg_node)
                # C varargs use the default argument promotions. LLVM IR does
                # not apply them for us, so emit them explicitly at the ABI
                # boundary: f32 -> f64 and integer types narrower than 32 bits
                # -> i32. Wider integers, pointers, and aggregates keep their
                # language/native representation.
                if isinstance(arg_value.type, ir.FloatType):
                    arg_value = self.builder.fpext(arg_value, ir.DoubleType())
                elif (
                    isinstance(arg_value.type, ir.IntType) and arg_value.type.width < 32
                ):
                    target = ir.IntType(32)
                    if self.codegen.is_unsigned_value(arg_value):
                        arg_value = self.builder.zext(arg_value, target)
                    else:
                        arg_value = self.builder.sext(arg_value, target)
                arg_values.append(arg_value)

        result = self.codegen.call_or_invoke(func, arg_values, name=f"call_{node.name}")
        # A function call creates a fresh SSA value.  LLVM's iN carries no
        # signedness, so restore the language return contract at the call site
        # (e.g. a u32 function returning 0xffffffff must remain 4294967295).
        if function_node is not None:
            ret_spec = getattr(function_node, "return_type", None)
            ret_info = (
                fixed_int_info_for_spec(self.codegen, ret_spec)
                if ret_spec is not None
                else None
            )
            if ret_info is not None and isinstance(result.type, ir.IntType):
                self.codegen.set_signedness(result, not ret_info.unsigned)
        return result

    def _call_callback_variable(self, node: Call) -> ir.Value | None:
        """Call a local or parameter whose declared type is a callback alias."""
        declared = getattr(self.codegen, "local_decl_types", {}).get(node.name)
        if not isinstance(declared, str):
            return None
        spec = resolve_callback_alias(
            declared, getattr(self.codegen, "type_aliases", {})
        )
        if spec is None:
            return None
        params, ret_type, decorators = callback_parts(spec)
        if len(node.args) != len(params):
            raise ExprGenError(
                f"Callback '{node.name}' expects {len(params)} argument(s), "
                f"got {len(node.args)}"
            )
        fn_ptr_type = self.codegen.get_llvm_type(declared)
        fn_ptr = self.generate_expr(Variable(node.name))
        if fn_ptr.type != fn_ptr_type:
            fn_ptr = self.cast_value(fn_ptr, fn_ptr_type)
        call_args = []
        for arg_node, (_pname, ptype) in zip(node.args, params):
            value = self.generate_expr(arg_node)
            expected = self.codegen.get_llvm_type(ptype)
            if value.type != expected:
                value = self.cast_value(value, expected)
            call_args.append(value)
        ret_llvm = self.codegen.get_llvm_type(ret_type)
        result_name = "" if isinstance(ret_llvm, ir.VoidType) else f"call_{node.name}"
        call = self.builder.call(fn_ptr, call_args, name=result_name)
        callconv = llvm_calling_convention(decorators)
        if callconv:
            call.calling_convention = callconv
        return call

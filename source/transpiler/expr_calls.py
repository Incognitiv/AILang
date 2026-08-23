"""Call/member/enum visitors for LLVM expression generation.

Extracted from ``emit_expressions.py`` as part of the LLVM-side
ExprGenerator decomposition. Method bodies are unchanged.
"""

from __future__ import annotations

from parser.ast import (
    BinaryOp,
    Call,
    EnumConstruct,
    EnumFieldAccess,
    FieldAccess,
    MatchPattern,
    MethodCall,
    NewExpr,
    Number,
    Return,
    SafeFieldAccess,
    StringLit,
    ThisExpr,
    Variable,
    parsed_type_to_str,
)
from typing import Any

from ast_access import body_at
from callback_types import callback_parts, resolve_callback_alias
from calling_conventions import llvm_calling_convention
from llvmlite import ir
from transpiler.expr_common import ExprGenError
from transpiler.llvm_fixed_int_casts import cast_to_declared_int, fixed_int_info_for_spec
from transpiler.llvm_bigint import (
    bigint_from_decimal_literal,
    clone_if_borrowed,
    fixed_to_bigint,
    is_bigint_value,
    is_unbounded_spec,
)
from transpiler.pure_eval import stable_literal_bindings, try_eval_call


def _is_string_type(type_name: Any) -> bool:
    return str(type_name).strip().lower() in {"string", "str"}


def _string_len_name(name: str) -> str:
    return f"__ailang_{name}_len"


class ExprCallEmitter:
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

    def visit_Call(self, node: Call):
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

    def visit_FieldAccess(self, node: FieldAccess):
        if isinstance(node.object_expr, Variable):
            enum_name = node.object_expr.name
            variant_name = node.field_name
            enum_key = f"{enum_name}.{variant_name}"
            # Check if this is a data-carrying enum access (e.g., AST.Empty)
            if enum_name in self.codegen.data_enums:
                # Create a data enum instance with no data
                tag_values = self.codegen.data_enum_tags[enum_name]
                enum_type = self.codegen.data_enum_types[enum_name]
                if variant_name in tag_values:
                    tag = tag_values[variant_name]
                    enum_ptr = self.builder.alloca(
                        enum_type, name=f"{enum_name}_{variant_name}"
                    )
                    tag_ptr = self.builder.gep(
                        enum_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), 0),
                        ],
                        name="tag_ptr",
                    )
                    self.builder.store(ir.Constant(ir.IntType(32), tag), tag_ptr)
                    return enum_ptr

            if enum_key in self.codegen.enum_values:
                return ir.Constant(ir.IntType(64), self.codegen.enum_values[enum_key])

            local_storage = self.codegen.locals.get(enum_name)
            if isinstance(
                getattr(local_storage, "type", None), ir.PointerType
            ) and isinstance(local_storage.type.pointee, ir.LiteralStructType):
                local_struct_type = local_storage.type.pointee
                record_name = self.codegen.get_record_name_from_type(local_struct_type)
                field_index, _ = self.codegen.get_field_info(
                    record_name, node.field_name
                )
                field_ptr = self.builder.gep(
                    local_storage,
                    [
                        ir.Constant(ir.IntType(32), 0),
                        ir.Constant(ir.IntType(32), field_index),
                    ],
                    name=f"{node.field_name}_ptr",
                )
                return self.builder.load(field_ptr, name=node.field_name)

        obj_value = self.generate_expr(node.object_expr)
        if not isinstance(obj_value.type, ir.PointerType):
            if isinstance(obj_value.type, ir.LiteralStructType):
                record_name = self.codegen.get_record_name_from_type(obj_value.type)
                field_index, _ = self.codegen.get_field_info(
                    record_name, node.field_name
                )
                return self.builder.extract_value(
                    obj_value, field_index, name=node.field_name
                )
            raise TypeError("Field access requires record value or pointer")

        struct_type = obj_value.type.pointee
        if not isinstance(struct_type, ir.LiteralStructType):
            raise TypeError("Field access on non-record type")

        record_name = self.codegen.get_record_name_from_type(struct_type)
        field_index, _ = self.codegen.get_field_info(record_name, node.field_name)
        field_ptr = self.builder.gep(
            obj_value,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
            name=f"{node.field_name}_ptr",
        )
        return self.builder.load(field_ptr, name=node.field_name)

    def visit_SafeFieldAccess(self, node: SafeFieldAccess):
        """Handle safe field access: object?.field

        Returns nil (null pointer) if object is nil, otherwise returns field value.
        """
        obj_ptr = self.generate_expr(node.object_expr)
        if not isinstance(obj_ptr.type, ir.PointerType):
            raise TypeError("Safe field access requires pointer to record")

        struct_type = obj_ptr.type.pointee
        if not isinstance(struct_type, ir.LiteralStructType):
            raise TypeError("Safe field access on non-record type")

        current_func = self.builder.function
        is_nil_block = current_func.append_basic_block("safe_nil")
        not_nil_block = current_func.append_basic_block("safe_not_nil")
        merge_block = current_func.append_basic_block("safe_merge")

        null_ptr = ir.Constant(obj_ptr.type, None)
        is_nil = self.builder.icmp_unsigned("==", obj_ptr, null_ptr, "is_nil")
        self.builder.cbranch(is_nil, is_nil_block, not_nil_block)

        self.builder.position_at_end(is_nil_block)
        record_name = self.codegen.get_record_name_from_type(struct_type)
        _, field_type = self.codegen.get_field_info(record_name, node.field_name)
        llvm_field_type = self.codegen.get_llvm_type(field_type)
        nil_value = self.codegen.default_value(llvm_field_type)
        self.builder.branch(merge_block)
        nil_block_end = self.builder.block

        self.builder.position_at_end(not_nil_block)
        field_index, _ = self.codegen.get_field_info(record_name, node.field_name)
        field_ptr = self.builder.gep(
            obj_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
            name=f"{node.field_name}_ptr",
        )
        field_value = self.builder.load(field_ptr, name=node.field_name)
        self.builder.branch(merge_block)
        not_nil_block_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(field_value.type, name="safe_result")
        phi.add_incoming(nil_value, nil_block_end)
        phi.add_incoming(field_value, not_nil_block_end)
        return phi

    def visit_MethodCall(self, node: MethodCall):
        """Handle method call: object.method(args).

        Also handles data-carrying enum construction: EnumName.Variant(args)

        Translates to: ClassName_method(object, args)

        Class name resolution (in order of priority):
        1. From struct type pointee (for locally created objects)
        2. From parameter type annotation (for class-typed parameters)
        3. Error if neither available
        """
        # Check if this is data-carrying enum construction: EnumName.Variant(args)
        if isinstance(node.object_expr, Variable):
            enum_name = node.object_expr.name
            method_variant = node.method_name

            if enum_name in self.codegen.data_enums:
                # This is enum construction - delegate to EnumConstruct handling
                # Import locally to avoid circular dependency
                enum_construct = EnumConstruct(enum_name, method_variant, node.args)
                return self.visit_EnumConstruct(enum_construct)

        # Get the object pointer
        obj_ptr = self.generate_expr(node.object_expr)
        if not isinstance(obj_ptr.type, ir.PointerType):
            raise ExprGenError("Method call requires pointer to class instance")

        struct_type = obj_ptr.type.pointee
        class_name = None

        # Try to get class name from struct type (works for local objects)
        if isinstance(struct_type, ir.LiteralStructType):
            # Check if this struct type is registered as a record/class
            struct_id = id(struct_type)
            if struct_id in self.codegen.record_type_ids:
                class_name = self.codegen.record_type_ids[struct_id]
            elif not class_name:
                # Fallback: search by type equality
                for name, rtype in self.codegen.record_types.items():
                    if rtype is struct_type:
                        class_name = name
                        break

        # If not found, try parameter type annotation (Option 2: explicit types)
        # Check if the object expression is a simple variable reference
        # Variable is already imported at module level
        if (not class_name) and isinstance(node.object_expr, Variable):
            var_name = node.object_expr.name
            class_name = self.codegen.get_variable_class_type(var_name)

        if not class_name:
            raise ExprGenError(
                f"Cannot determine class type for method call '{node.method_name}'. "
                f"Add type annotation: param: ClassName"
            )

        # Look up the mangled method name: ClassName_methodName
        mangled_name = f"{class_name}_{node.method_name}"
        method_func = self.codegen.functions.get(mangled_name)

        if not method_func:
            raise ExprGenError(
                f"Unknown method '{node.method_name}' for class '{class_name}'"
            )

        method_ast = None
        for candidate in self.codegen.class_methods.get(class_name, []):
            if candidate.name == node.method_name:
                method_ast = candidate
                break

        # Correctness phase: do not bypass the method's typed parameter/return
        # boundary with AST-level inlining. Typed inlining can be restored in
        # the later optimization phase once it proves semantic equivalence.

        # Build argument list: [this, ...args]
        call_args = [obj_ptr]
        params = getattr(method_ast, "params", []) if method_ast is not None else []
        for index, arg_expr in enumerate(node.args):
            receiver_stack_local = isinstance(
                node.object_expr, Variable
            ) and node.object_expr.name in getattr(
                self.codegen, "_stack_class_locals", set()
            )
            can_elide_virtual = (
                receiver_stack_local
                and self._can_elide_virtual_string_arg(
                    class_name, node.method_name, index, arg_expr
                )
            )
            if can_elide_virtual:
                arg_value = ir.Constant(ir.IntType(8).as_pointer(), None)
            else:
                arg_value = self.generate_expr(arg_expr)
            if index < len(params):
                param = params[index]
                if isinstance(param, tuple) and len(param) >= 2:
                    param_type = param[1]
                    expected_type = self.codegen.get_llvm_type(param_type)
                    arg_value = cast_to_declared_int(
                        self.codegen, arg_value, expected_type, param_type
                    )
            call_args.append(arg_value)
            if index < len(params):
                param = params[index]
                if len(param) >= 2 and _is_string_type(param[1]):
                    call_args.append(
                        self._emit_string_len_for_expr(arg_expr, arg_value)
                    )

        # Verify argument count (method has 'this' + user args)
        expected_args = len(method_func.args)
        if len(call_args) != expected_args:
            raise ExprGenError(
                f"Method '{node.method_name}' expects {expected_args - 1} arguments, "
                f"got {len(node.args)}"
            )

        return self.codegen.call_or_invoke(
            method_func, call_args, name=f"call_{mangled_name}"
        )

    def _try_inline_stack_method_return_expr(
        self,
        node: MethodCall,
        obj_ptr: ir.Value,
        class_name: str,
        method_ast: Any,
    ) -> ir.Value | None:
        """Inline trivial stack-local no-arg methods as expressions."""
        if method_ast is None or method_ast.name == "init" or method_ast.params:
            return None
        if node.args:
            return None
        if len(method_ast.body) != 1 or not isinstance(body_at(method_ast, 0), Return):
            return None
        ret_expr = body_at(method_ast, 0).value
        if ret_expr is None:
            return None
        if not isinstance(node.object_expr, Variable):
            return None
        receiver_name = node.object_expr.name
        if receiver_name not in getattr(self.codegen, "_stack_class_locals", set()):
            return None
        if self.codegen.get_variable_class_type(receiver_name) != class_name:
            return None

        saved_this = getattr(self.codegen, "current_this", None)
        saved_class = getattr(self.codegen, "current_class", None)
        saved_inline_this = getattr(self.codegen, "_inline_this_stack_var", None)
        try:
            self.codegen.current_this = obj_ptr
            self.codegen.current_class = class_name
            self.codegen._inline_this_stack_var = receiver_name
            self.codegen._record_optimizer_decision(
                node,
                opt_kind="method_inline",
                target=f"{class_name}.{node.method_name}",
                decision="inlined",
                reason="single_return_stack_receiver",
                details={"receiver": receiver_name},
            )
            return self.generate_expr(ret_expr)
        finally:
            self.codegen.current_this = saved_this
            self.codegen.current_class = saved_class
            self.codegen._inline_this_stack_var = saved_inline_this

    def visit_NewExpr(self, node: NewExpr):
        """Create new instance of a record or class."""
        type_name = node.type_name
        record_type = self.codegen.record_types.get(type_name)
        fields = self.codegen.record_fields.get(type_name)

        if not record_type or fields is None:
            raise ExprGenError(f"Cannot instantiate unknown type: {type_name}")
        is_class = type_name in self.codegen.class_types
        if is_class:
            size = ir.Constant(ir.IntType(64), self.codegen.get_type_size(record_type))
            raw_ptr = self.codegen.checked_malloc(size, f"{type_name}_mem")
            instance_ptr = self.builder.bitcast(
                raw_ptr, record_type.as_pointer(), name=f"{type_name}_inst"
            )
        else:
            instance_ptr = self.builder.alloca(record_type, name=f"{type_name}_inst")

        # Check if this is a class with a constructor
        if type_name in self.codegen.class_methods:
            # Look for an 'init' constructor method
            constructor_name = f"{type_name}_init"
            if constructor_name in self.codegen.functions:
                # Initialize all fields to declaration defaults first. Hidden
                # bookkeeping fields still use their backend zero value.
                class_defaults = {
                    str(field_name): init_value
                    for _vis, field_name, _field_type, init_value in self.codegen.class_fields.get(
                        type_name, []
                    )
                    if init_value is not None
                }
                for index, (field_name, field_type) in enumerate(fields):
                    field_llvm_type = self.codegen.get_llvm_type(field_type)
                    default_expr = class_defaults.get(str(field_name))
                    if default_expr is not None:
                        default_val = self.generate_expr(default_expr)
                        default_val = cast_to_declared_int(
                            self.codegen, default_val, field_llvm_type, field_type
                        )
                    else:
                        default_val = self.codegen.default_value(field_llvm_type)
                    field_ptr = self.builder.gep(
                        instance_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), index),
                        ],
                        name=f"field_{field_name}_ptr",
                    )
                    self.builder.store(default_val, field_ptr)

                    if default_expr is not None and _is_string_type(field_type):
                        hidden_name = _string_len_name(str(field_name))
                        try:
                            hidden_idx, _ = self.codegen.get_field_info(
                                type_name, hidden_name
                            )
                        except Exception:
                            hidden_idx = -1
                        if hidden_idx >= 0:
                            hidden_ptr = self.builder.gep(
                                instance_ptr,
                                [
                                    ir.Constant(ir.IntType(32), 0),
                                    ir.Constant(ir.IntType(32), hidden_idx),
                                ],
                                name=f"{field_name}_default_len_ptr",
                            )
                            self.builder.store(
                                self._emit_string_len_for_expr(
                                    default_expr, default_val
                                ),
                                hidden_ptr,
                            )

                init_method = next(
                    (
                        candidate
                        for candidate in self.codegen.class_methods.get(type_name, [])
                        if candidate.name == "init"
                    ),
                    None,
                )

                # Call the constructor with 'this' as first argument
                constructor = self.codegen.functions[constructor_name]
                call_args = [instance_ptr]
                params = getattr(init_method, "params", []) if init_method else []
                for index, arg_expr in enumerate(node.args):
                    arg_value = self.generate_expr(arg_expr)
                    param = params[index] if index < len(params) else None
                    if isinstance(param, tuple) and len(param) >= 2:
                        param_type = param[1]
                        expected_type = self.codegen.get_llvm_type(param_type)
                        arg_value = cast_to_declared_int(
                            self.codegen, arg_value, expected_type, param_type
                        )
                    call_args.append(arg_value)
                    if isinstance(param, tuple) and len(param) >= 2:
                        if _is_string_type(param[1]):
                            call_args.append(
                                self._emit_string_len_for_expr(arg_expr, arg_value)
                            )
                self.builder.call(constructor, call_args)
                return instance_ptr

        # No constructor - use positional initialization (record-style), with
        # declaration defaults filling an omitted trailing suffix.
        visible_fields = [
            field for field in fields if not str(field[0]).startswith("__ailang_")
        ]
        if is_class:
            defaults = {
                str(field_name): init_value
                for _vis, field_name, _field_type, init_value in self.codegen.class_fields.get(
                    type_name, []
                )
                if init_value is not None
            }
        else:
            defaults = self.codegen.record_field_defaults.get(type_name, {})

        if len(node.args) > len(visible_fields):
            raise ExprGenError(
                f"{type_name} expects at most {len(visible_fields)} constructor arguments, "
                f"got {len(node.args)}"
            )

        init_exprs = list(node.args)
        for field_info in visible_fields[len(init_exprs) :]:
            field_name = str(field_info[0])
            default_expr = defaults.get(field_name)
            if default_expr is None:
                raise ExprGenError(
                    f"{type_name} requires constructor argument for field '{field_name}'"
                )
            init_exprs.append(default_expr)

        for index, (field_info, arg_expr) in enumerate(
            zip(visible_fields, init_exprs, strict=False)
        ):
            _, field_type_name = field_info
            field_type = self.codegen.get_llvm_type(field_type_name)
            arg_value = self.generate_expr(arg_expr)
            arg_value = cast_to_declared_int(
                self.codegen, arg_value, field_type, field_type_name
            )
            field_ptr = self.builder.gep(
                instance_ptr,
                [
                    ir.Constant(ir.IntType(32), 0),
                    ir.Constant(
                        ir.IntType(32),
                        self.codegen.get_field_info(type_name, field_info[0])[0],
                    ),
                ],
                name=f"field_{index}_ptr",
            )
            self.builder.store(arg_value, field_ptr)
            if _is_string_type(field_type_name):
                hidden_name = _string_len_name(field_info[0])
                try:
                    hidden_idx, _ = self.codegen.get_field_info(type_name, hidden_name)
                except Exception:
                    hidden_idx = -1
                if hidden_idx >= 0:
                    hidden_ptr = self.builder.gep(
                        instance_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), hidden_idx),
                        ],
                        name=f"{field_info[0]}_len_ptr",
                    )
                    self.builder.store(
                        self._emit_string_len_for_expr(arg_expr, arg_value), hidden_ptr
                    )
        if is_class:
            return instance_ptr
        return self.builder.load(instance_ptr, name=f"{type_name}_value")

    def visit_ThisExpr(self, _node: ThisExpr):
        """Return the 'this' pointer for the current class method."""
        # Check if we're in a method context
        if self.codegen.current_this is not None:
            return self.codegen.current_this
        if "this" in self.codegen.locals:
            return self.codegen.locals["this"]
        raise ExprGenError("'this' used outside of method context")

    def visit_EnumConstruct(self, node: EnumConstruct):
        """Construct a data-carrying enum variant: AST.Number(42)"""
        enum_name = node.enum_name
        variant_name = node.variant_name

        # Check if this is a data-carrying enum
        if enum_name not in self.codegen.data_enums:
            # Simple enum - just return the tag value as integer
            full_name = f"{enum_name}.{variant_name}"
            if full_name in self.codegen.enum_values:
                tag = self.codegen.enum_values[full_name]
                return ir.Constant(ir.IntType(64), tag)
            raise ExprGenError(f"Unknown enum variant: {full_name}")

        # Data-carrying enum
        variant_data = self.codegen.data_enums[enum_name]
        tag_values = self.codegen.data_enum_tags[enum_name]
        enum_type = self.codegen.data_enum_types[enum_name]

        if variant_name not in tag_values:
            raise ExprGenError(f"Unknown variant {variant_name} in enum {enum_name}")

        tag = tag_values[variant_name]
        fields = variant_data.get(variant_name, [])

        enum_ptr = self.builder.alloca(enum_type, name=f"{enum_name}_{variant_name}")

        tag_ptr = self.builder.gep(
            enum_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
            name="tag_ptr",
        )
        self.builder.store(ir.Constant(ir.IntType(32), tag), tag_ptr)

        if fields and node.args:
            data_ptr = self.builder.gep(
                enum_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
                name="data_ptr",
            )

            offset = 0
            for (field_name, field_type), arg in zip(fields, node.args, strict=False):
                arg_val = self.generate_expr(arg)
                field_llvm_type = self.codegen.get_llvm_type(field_type)
                arg_val = cast_to_declared_int(
                    self.codegen, arg_val, field_llvm_type, field_type
                )

                field_ptr = self.builder.gep(
                    data_ptr,
                    [
                        ir.Constant(ir.IntType(32), 0),
                        ir.Constant(ir.IntType(32), offset),
                    ],
                    name=f"field_{field_name}_byte_ptr",
                )

                typed_ptr = self.builder.bitcast(
                    field_ptr,
                    field_llvm_type.as_pointer(),
                    name=f"field_{field_name}_ptr",
                )
                self.builder.store(arg_val, typed_ptr)

                offset += self.codegen.get_type_size(field_llvm_type)

        return enum_ptr

    def visit_EnumFieldAccess(self, node: EnumFieldAccess):
        """Access a field from a data-carrying enum variant."""
        enum_val = self.generate_expr(node.expr)
        field_name = node.field_name

        # This requires type inference - for now, check if it's a known enum ptr
        if not isinstance(enum_val.type, ir.PointerType):
            raise ExprGenError(
                f"Cannot access field '{field_name}' on non-pointer enum value"
            )

        # Try to find which enum this is by checking registered types
        enum_name = None
        for name, enum_type in self.codegen.data_enum_types.items():
            if enum_type == enum_val.type.pointee:
                enum_name = name
                break

        if enum_name is None:
            raise ExprGenError(
                f"Cannot determine enum type for field access '.{field_name}'"
            )

        # Find the field in any variant that has it
        variant_data = self.codegen.data_enums[enum_name]
        field_offset = 0
        field_type = None

        for fields in variant_data.values():
            offset = 0
            for fname, ftype in fields:
                if fname == field_name:
                    field_offset = offset
                    field_type = ftype
                    break
                offset += self.codegen.get_type_size(self.codegen.get_llvm_type(ftype))
            if field_type:
                break

        if field_type is None:
            raise ExprGenError(f"Field '{field_name}' not found in enum {enum_name}")

        # Get pointer to data array
        data_ptr = self.builder.gep(
            enum_val,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
            name="data_ptr",
        )

        # Get pointer to specific field
        field_ptr = self.builder.gep(
            data_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_offset)],
            name=f"field_{field_name}_byte_ptr",
        )

        # Cast to appropriate type and load
        field_llvm_type = self.codegen.get_llvm_type(field_type)
        typed_ptr = self.builder.bitcast(
            field_ptr, field_llvm_type.as_pointer(), name=f"field_{field_name}_ptr"
        )
        return self.builder.load(typed_ptr, name=field_name)

    def visit_MatchPattern(self, node: MatchPattern):
        """MatchPattern is handled inside visit_Match, not as standalone expr."""
        raise ExprGenError(
            "MatchPattern should be handled inside Match statement, not as expression"
        )

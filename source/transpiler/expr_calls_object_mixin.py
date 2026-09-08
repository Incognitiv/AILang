"""Extracted responsibilities for :class:`ExprCallEmitter`."""

from __future__ import annotations

from parser.ast import (
    EnumConstruct,
    EnumFieldAccess,
    FieldAccess,
    MatchPattern,
    MethodCall,
    NewExpr,
    Return,
    SafeFieldAccess,
    ThisExpr,
    Variable,
)
from typing import Any

from ast_access import body_at
from llvmlite import ir
from transpiler.expr_common import ExprGenError
from transpiler.llvm_fixed_int_casts import (
    cast_to_declared_int,
)

from .expr_call_names import is_string_type as _is_string_type
from .expr_call_names import string_len_name as _string_len_name


class ExprCallObjectMixin:
    def visit_FieldAccess(self: Any, node: FieldAccess):
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

    def visit_SafeFieldAccess(self: Any, node: SafeFieldAccess):
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

    def visit_MethodCall(self: Any, node: MethodCall):
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
        self: Any,
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

    def visit_NewExpr(self: Any, node: NewExpr):
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

    def visit_ThisExpr(self: Any, _node: ThisExpr):
        """Return the 'this' pointer for the current class method."""
        # Check if we're in a method context
        if self.codegen.current_this is not None:
            return self.codegen.current_this
        if "this" in self.codegen.locals:
            return self.codegen.locals["this"]
        raise ExprGenError("'this' used outside of method context")

    def visit_EnumConstruct(self: Any, node: EnumConstruct):
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

    def visit_EnumFieldAccess(self: Any, node: EnumFieldAccess):
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

    def visit_MatchPattern(self: Any, node: MatchPattern):
        """MatchPattern is handled inside visit_Match, not as standalone expr."""
        raise ExprGenError(
            "MatchPattern should be handled inside Match statement, not as expression"
        )

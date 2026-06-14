"""LLVM stack-class lowering helpers."""

from __future__ import annotations

from parser.ast import ASTNode, NewExpr, VarDecl, parsed_type_to_str

from llvmlite import ir
from transpiler import optimizer_decisions as opt

from .drop_plan import DropKind, constructor_field_drop_plan, expr_produces_owned_value
from .emit_statements_common import StmtGenError
from .virtual_array_fields import (
    class_array_field_uses_are_stack_safe,
    constructor_body_replayable_with_stack_arrays,
    constructor_stack_array_fields,
    emit_stack_array_constructor_llvm,
    function_stack_array_field_direct_scalar_reads,
    function_stack_array_field_method_scalar_reads,
    function_stack_array_field_uses_are_safe,
)


def _is_string_type(type_name: object) -> bool:
    return str(type_name).strip().lower() in {"string", "str"}


def _try_emit_stack_class_vardecl(self, node: VarDecl, llvm_type: ir.Type) -> bool:
    """Stack-lower proven non-escaping local class construction."""
    if not isinstance(node.init_value, NewExpr):
        return False
    class_name = parsed_type_to_str(node.type_name)
    if class_name != node.init_value.type_name:
        return False
    if class_name not in self.codegen.class_types:
        return False
    if node.var_name not in getattr(self.codegen, "_stack_class_locals", set()):
        return False

    record_type = self.codegen.class_types[class_name]
    instance_ptr = self.codegen.alloca_in_entry_block(
        record_type, f"{node.var_name}_stack"
    )
    var_ptr = self.codegen.alloca_in_entry_block(llvm_type, node.var_name)
    fields = self.codegen.record_fields.get(class_name, [])

    for index, (field_name, field_type) in enumerate(fields):
        field_llvm_type = self.codegen.get_llvm_type(field_type)
        default_val = self.codegen.default_value(field_llvm_type)
        field_ptr = self.builder.gep(
            instance_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), index)],
            name=f"field_{field_name}_ptr",
        )
        self.builder.store(default_val, field_ptr)

    init_method = next(
        (
            candidate
            for candidate in self.codegen.class_methods.get(class_name, [])
            if candidate.name == "init"
        ),
        None,
    )
    stack_array_fields_for_cleanup: set[str] = set()
    constructor_name = f"{class_name}_init"
    if constructor_name in self.codegen.functions:
        constructor = self.codegen.functions[constructor_name]
        params = getattr(init_method, "params", []) if init_method else []
        stack_array_plan = constructor_stack_array_fields(
            class_name, init_method, self.codegen.record_fields
        )
        current_body = getattr(self.codegen, "_current_function_body", []) or []
        stack_array_scalar_fields: set[str] = set()
        if stack_array_plan:
            planned_fields = set(stack_array_plan)
            stack_array_scalar_fields = function_stack_array_field_direct_scalar_reads(
                current_body,
                node.var_name,
                planned_fields,
            )
            stack_array_scalar_fields.update(
                function_stack_array_field_method_scalar_reads(
                    current_body,
                    node.var_name,
                    self.codegen.class_methods.get(class_name, []),
                    planned_fields,
                )
            )
        replayed_stack_arrays = (
            stack_array_plan
            and init_method is not None
            and constructor_body_replayable_with_stack_arrays(
                init_method,
                stack_array_plan,
            )
            and class_array_field_uses_are_stack_safe(
                self.codegen.class_methods.get(class_name, []), set(stack_array_plan)
            )
            and function_stack_array_field_uses_are_safe(
                current_body,
                node.var_name,
                set(stack_array_plan),
            )
            and emit_stack_array_constructor_llvm(
                self,
                class_name,
                node.var_name,
                node.init_value,
                instance_ptr,
                init_method,
                stack_array_plan,
            )
        )
        if replayed_stack_arrays:
            stack_array_fields_for_cleanup = set(stack_array_plan)
            opt.record_stack_array_fields(
                self.codegen,
                node.init_value,
                node.var_name,
                class_name,
                stack_array_plan,
                stack_array_scalar_fields,
            )
        else:
            call_args = [instance_ptr]
            for index, arg_expr in enumerate(node.init_value.args):
                call_emitter = self.codegen.expr_generator.call_emitter
                if call_emitter._can_elide_virtual_string_arg(
                    class_name, "init", index, arg_expr
                ):
                    opt.record_virtual_string_arg(
                        self.codegen,
                        arg_expr,
                        class_name,
                        "init",
                        index,
                        node.var_name,
                        "constructor_needs_length_not_bytes",
                    )
                    arg_value = ir.Constant(ir.IntType(8).as_pointer(), None)
                else:
                    arg_value = self.codegen.generate_expr(arg_expr)
                call_args.append(arg_value)
                if index < len(params):
                    param = params[index]
                    if len(param) >= 2 and _is_string_type(param[1]):
                        call_args.append(
                            call_emitter._emit_string_len_for_expr(arg_expr, arg_value)
                        )
            self.builder.call(constructor, call_args)
    else:
        visible_fields = [
            field for field in fields if not str(field[0]).startswith("__ailang_")
        ]
        if len(node.init_value.args) != len(visible_fields):
            raise StmtGenError(
                f"{class_name} expects {len(visible_fields)} constructor arguments, "
                f"got {len(node.init_value.args)}"
            )
        for index, ((_, field_type), arg_expr) in enumerate(
            zip(visible_fields, node.init_value.args, strict=False)
        ):
            field_name = visible_fields[index][0]
            field_llvm_type = self.codegen.get_llvm_type(field_type)
            arg_value = self.codegen.generate_expr(arg_expr)
            if arg_value.type != field_llvm_type:
                arg_value = self.codegen.cast_value(arg_value, field_llvm_type)
            field_idx, _ = self.codegen.get_field_info(class_name, field_name)
            field_ptr = self.builder.gep(
                instance_ptr,
                [
                    ir.Constant(ir.IntType(32), 0),
                    ir.Constant(ir.IntType(32), field_idx),
                ],
                name=f"field_{index}_ptr",
            )
            self.builder.store(arg_value, field_ptr)
            if _is_string_type(field_type):
                hidden_name = f"__ailang_{field_name}_len"
                try:
                    hidden_idx, _ = self.codegen.get_field_info(class_name, hidden_name)
                except Exception:
                    hidden_idx = -1
                if hidden_idx >= 0:
                    hidden_ptr = self.builder.gep(
                        instance_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), hidden_idx),
                        ],
                        name=f"{field_name}_len_ptr",
                    )
                    self.builder.store(
                        self.codegen.expr_generator.call_emitter._emit_string_len_for_expr(
                            arg_expr, arg_value
                        ),
                        hidden_ptr,
                    )
    opt.record_stack_class(self.codegen, node.init_value, node.var_name, class_name)

    self.builder.store(instance_ptr, var_ptr)
    self.codegen.locals[node.var_name] = var_ptr
    self.codegen.local_decl_types[node.var_name] = class_name
    self.codegen.var_signedness[node.var_name] = True
    self.codegen.set_signedness(var_ptr, True)
    self.codegen.register_for_cleanup(node.var_name, class_name, instance_ptr)
    call_emitter = self.codegen.expr_generator.call_emitter

    def _stack_constructor_arg_materializes(
        arg_class_name: str,
        method_name: str,
        param_index: int,
        arg: ASTNode,
        kind: DropKind,
        field_type: object,
    ) -> bool:
        if (
            kind == DropKind.OWNED_STRING
            and call_emitter._can_elide_virtual_string_arg(
                arg_class_name, method_name or "init", param_index, arg
            )
        ):
            return False

        return expr_produces_owned_value(
            arg,
            kind,
            field_type,
            getattr(self.codegen, "classes", None),
            None,
        )

    plan = constructor_field_drop_plan(
        class_name,
        node.init_value,
        self.codegen.class_methods,
        self.codegen.record_fields,
        getattr(self.codegen, "classes", None),
        is_materialized_constructor_arg=_stack_constructor_arg_materializes,
    )
    if stack_array_fields_for_cleanup:
        plan = type(plan)(
            plan.type_name,
            tuple(
                field
                for field in plan.fields
                if field.name not in stack_array_fields_for_cleanup
            ),
            plan.user_destructor,
            plan.free_storage,
        )
    if plan.fields:
        self.codegen._stack_class_cleanup_plans[node.var_name] = {
            "class": class_name,
            "ptr": instance_ptr,
            "plan": plan,
        }
        cleanup_stack = getattr(self.codegen, "_loop_stack_class_cleanup", [])
        if cleanup_stack and node.var_name not in cleanup_stack[-1]:
            cleanup_stack[-1].append(node.var_name)
    else:
        self.codegen._stack_class_cleanup_plans.pop(node.var_name, None)
    self.codegen.array_metadata.pop(node.var_name, None)
    return True

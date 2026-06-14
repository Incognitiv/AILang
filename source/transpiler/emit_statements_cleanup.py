from __future__ import annotations

from llvmlite import ir

from .drop_plan import DropFieldPlan, DropKind, DropPlan


def _emit_stack_class_cleanup(self, var_name: str) -> None:
    plan = getattr(self.codegen, "_stack_class_cleanup_plans", {}).get(var_name)
    if not plan:
        return
    class_name = plan["class"]
    instance_ptr = plan["ptr"]
    drop_plan = plan.get("plan")
    if isinstance(drop_plan, DropPlan):
        drop_fields: list[DropFieldPlan] = list(drop_plan.fields)
    else:
        # Compatibility for older in-memory plan shapes.
        drop_fields = []
        for field_name, field_type in self.codegen.record_fields.get(class_name, []):
            if field_name in set(plan.get("strings", set())):
                drop_fields.append(
                    DropFieldPlan(field_name, field_type, DropKind.OWNED_STRING)
                )
            elif field_name in set(plan.get("arrays", set())):
                drop_fields.append(
                    DropFieldPlan(field_name, field_type, DropKind.DYNAMIC_ARRAY)
                )
    if not drop_fields:
        return

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    i64_ptr = ir.IntType(64).as_pointer()
    free_fn = self.codegen._get_free()
    for field in reversed(drop_fields):
        field_name, kind = field.name, field.kind
        field_idx, _ = self.codegen.get_field_info(class_name, field_name)
        field_ptr = self.builder.gep(
            instance_ptr,
            [ir.Constant(i32, 0), ir.Constant(i32, field_idx)],
            name=f"{field_name}_cleanup_ptr",
        )
        if kind == DropKind.DYNAMIC_ARRAY:
            data_ptr = self.builder.load(field_ptr, name=f"{field_name}_cleanup_data")
            if str(data_ptr.type) != "i64*":
                data_ptr = self.builder.bitcast(data_ptr, i64_ptr)
            is_null = self.builder.icmp_unsigned(
                "==", data_ptr, ir.Constant(data_ptr.type, None)
            )
            free_block = self.func.append_basic_block(f"{field_name}_free_array")
            done_block = self.func.append_basic_block(f"{field_name}_free_array_done")
            self.builder.cbranch(is_null, done_block, free_block)
            self.builder.position_at_end(free_block)
            raw_base = self.builder.gep(
                data_ptr, [ir.Constant(i32, -2)], name=f"{field_name}_raw_base"
            )
            self.builder.call(free_fn, [self.builder.bitcast(raw_base, i8_ptr)])
            self.builder.store(ir.Constant(data_ptr.type, None), field_ptr)
            self.builder.branch(done_block)
            self.builder.position_at_end(done_block)
        elif kind == DropKind.OWNED_STRING:
            text_ptr = self.builder.load(field_ptr, name=f"{field_name}_cleanup_text")
            # LLVM routes main-scope string_alloc through the function arena.
            # Those pointers are released by arena_destroy, not individual free().
            if getattr(self.codegen, "_string_arena", None) is None:
                self.builder.call(free_fn, [text_ptr])
            self.builder.store(ir.Constant(text_ptr.type, None), field_ptr)

"""Conservative stack-backed dynamic-array field planning.

This module only recognizes constructor-local arrays whose normal dynamic-array
header can be represented by stack storage without changing read semantics.
It deliberately rejects mutating/exposing uses outside the constructor.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any, Iterable

from ast_access import arg_at, body_at
from transpiler.optimizer_decisions import record_virtual_string_arg

try:
    ir: Any = importlib.import_module("llvmlite.ir")
except ModuleNotFoundError:  # C backend can run on hosts without LLVM bindings.
    ir = None


@dataclass(frozen=True)
class StackArrayFieldPlan:
    field_name: str
    capacity: int
    pushes: tuple[A.ASTNode, ...]


def _node_children(node: A.ASTNode) -> Iterable[A.ASTNode]:
    for value in vars(node).values():
        if isinstance(value, A.ASTNode):
            yield value
        elif isinstance(value, (list, tuple)):
            yield from (item for item in value if isinstance(item, A.ASTNode))


def _node_uses_this(node: A.ASTNode) -> bool:
    if isinstance(node, A.ThisExpr):
        return True
    return any(_node_uses_this(child) for child in _node_children(node))


def _is_this_field(node: A.ASTNode, field_name: str) -> bool:
    return (
        isinstance(node, A.FieldAccess)
        and isinstance(node.object_expr, A.ThisExpr)
        and node.field_name == field_name
    )


def _is_var_field(node: A.ASTNode, var_name: str, field_name: str) -> bool:
    return (
        isinstance(node, A.FieldAccess)
        and isinstance(node.object_expr, A.Variable)
        and node.object_expr.name == var_name
        and node.field_name == field_name
    )


def _is_array_new(expr: A.ASTNode) -> int | None:
    if not isinstance(expr, A.Call) or expr.name != "array_new" or len(expr.args) != 1:
        return None
    cap_expr = arg_at(expr, 0)
    if not isinstance(cap_expr, A.Number) or cap_expr.is_float:
        return None
    capacity = int(cap_expr.value)
    return capacity if capacity > 0 else None


def _is_this_field_push(expr: A.ASTNode, field_name: str) -> A.ASTNode | None:
    if not isinstance(expr, A.Call) or expr.name != "array_push" or len(expr.args) != 2:
        return None
    if not _is_this_field(arg_at(expr, 0), field_name):
        return None
    return arg_at(expr, 1)


def _array_field_names(
    class_name: str,
    record_fields: dict[str, list[tuple[str, Any]]],
) -> set[str]:
    fields = record_fields.get(class_name, [])
    return {
        field_name
        for field_name, field_type in fields
        if parsed_type_to_str(field_type).strip().lower() == "array"
    }


def constructor_stack_array_fields(
    class_name: str,
    init_method: A.Function | None,
    record_fields: dict[str, list[tuple[str, Any]]],
) -> dict[str, StackArrayFieldPlan]:
    """Return array fields that can be stack-backed during constructor replay."""
    if init_method is None:
        return {}
    array_fields = _array_field_names(class_name, record_fields)
    if not array_fields:
        return {}

    capacities: dict[str, int] = {}
    pushes: dict[str, list[A.ASTNode]] = {}
    for stmt in init_method.body:
        if not isinstance(stmt, A.FieldAssign):
            continue
        if not isinstance(stmt.object_expr, A.ThisExpr):
            continue
        field_name = stmt.field_name
        if field_name not in array_fields:
            continue
        capacity = _is_array_new(stmt.value)
        if capacity is not None:
            capacities[field_name] = capacity
            pushes[field_name] = []
            continue
        pushed = _is_this_field_push(stmt.value, field_name)
        if pushed is not None and field_name in capacities:
            pushes[field_name].append(pushed)

    plans: dict[str, StackArrayFieldPlan] = {}
    for field_name, capacity in capacities.items():
        field_pushes = tuple(pushes.get(field_name, []))
        if field_pushes and len(field_pushes) <= capacity:
            plans[field_name] = StackArrayFieldPlan(field_name, capacity, field_pushes)
    return plans


def constructor_body_replayable_with_stack_arrays(
    init_method: A.Function | None,
    plans: dict[str, StackArrayFieldPlan],
) -> bool:
    if init_method is None or not plans:
        return False
    plan_fields = set(plans)
    for stmt in init_method.body:
        if not isinstance(stmt, A.FieldAssign):
            return False
        if not isinstance(stmt.object_expr, A.ThisExpr):
            return False
        if stmt.field_name not in plan_fields:
            # Simple field transfers such as `this.label = label_arg` are replayable.
            if _node_uses_this(stmt.value):
                return False
            continue
        if _is_array_new(stmt.value) is not None:
            continue
        if _is_this_field_push(stmt.value, stmt.field_name) is not None:
            continue
        return False
    return True


def class_array_field_uses_are_stack_safe(
    methods: list[A.Function],
    field_names: set[str],
) -> bool:
    """Reject methods that expose/mutate planned stack-backed array fields."""
    for method in methods:
        if method.name == "init":
            continue
        for field_name in field_names:
            if not _node_uses_this_array_field_safely(method, field_name):
                return False
    return True


def _node_uses_this_array_field_safely(node: A.ASTNode, field_name: str) -> bool:
    if isinstance(node, A.FieldAssign) and _is_this_field(node.object_expr, field_name):
        return False
    if isinstance(node, A.Call) and node.args:
        if _is_this_field(arg_at(node, 0), field_name):
            if node.name not in {"array_get", "array_len"}:
                return False
            return all(
                _node_uses_this_array_field_safely(arg, field_name)
                for arg in node.args[1:]
            )
    if _is_this_field(node, field_name):
        return False
    return all(
        _node_uses_this_array_field_safely(child, field_name)
        for child in _node_children(node)
    )


def function_stack_array_field_uses_are_safe(
    body: list[A.ASTNode],
    var_name: str,
    field_names: set[str],
) -> bool:
    for stmt in body:
        for field_name in field_names:
            if not _node_uses_var_array_field_safely(stmt, var_name, field_name):
                return False
    return True


def function_stack_array_field_direct_scalar_reads(
    body: list[A.ASTNode],
    var_name: str,
    field_names: set[str],
) -> set[str]:
    """Return planned fields read directly by scalarizable array builtins.

    Stack backing is profitable for constructor-local field arrays even when
    the only use is through an ordinary method call. Per-element C temporaries
    are a narrower optimization: emit them only when the current function
    directly reads `array_get(var.field, constant)` or `array_len(var.field)`.
    """
    direct: set[str] = set()
    for stmt in body:
        for field_name in field_names:
            if _node_directly_reads_var_array_field(stmt, var_name, field_name):
                direct.add(field_name)
    return direct


def _node_directly_reads_var_array_field(
    node: A.ASTNode, var_name: str, field_name: str
) -> bool:
    if isinstance(node, A.Call) and node.args:
        if _is_var_field(arg_at(node, 0), var_name, field_name):
            if node.name == "array_len":
                return True
            if (
                node.name == "array_get"
                and len(node.args) >= 2
                and isinstance(arg_at(node, 1), A.Number)
                and not arg_at(node, 1).is_float
            ):
                return True
    return any(
        _node_directly_reads_var_array_field(child, var_name, field_name)
        for child in _node_children(node)
    )


def function_stack_array_field_method_scalar_reads(
    body: list[A.ASTNode],
    var_name: str,
    methods: list[A.Function],
    field_names: set[str],
) -> set[str]:
    """Return planned fields read by inlineable calls on a stack local."""
    by_name = {method.name: method for method in methods}
    direct: set[str] = set()
    for stmt in body:
        direct.update(
            _node_stack_array_method_scalar_reads(stmt, var_name, by_name, field_names)
        )
    return direct


def _node_stack_array_method_scalar_reads(
    node: A.ASTNode,
    var_name: str,
    methods: dict[str, A.Function],
    field_names: set[str],
) -> set[str]:
    direct: set[str] = set()
    if (
        isinstance(node, A.MethodCall)
        and isinstance(node.object_expr, A.Variable)
        and node.object_expr.name == var_name
        and not node.args
    ):
        method = methods.get(node.method_name)
        if _method_single_return_expr(method) and method is not None:
            returned = body_at(method, 0)
            if isinstance(returned, A.Return):
                value = returned.value
                for field_name in field_names:
                    if value is not None and _node_directly_reads_this_array_field(
                        value, field_name
                    ):
                        direct.add(field_name)
    for child in _node_children(node):
        direct.update(
            _node_stack_array_method_scalar_reads(child, var_name, methods, field_names)
        )
    return direct


def _node_directly_reads_this_array_field(node: A.ASTNode, field_name: str) -> bool:
    if isinstance(node, A.Call) and node.args:
        if _is_this_field(arg_at(node, 0), field_name):
            if node.name == "array_len":
                return True
            if (
                node.name == "array_get"
                and len(node.args) >= 2
                and isinstance(arg_at(node, 1), A.Number)
                and not arg_at(node, 1).is_float
            ):
                return True
    return any(
        _node_directly_reads_this_array_field(child, field_name)
        for child in _node_children(node)
    )


def _method_single_return_expr(method: A.Function | None) -> bool:
    return (
        method is not None
        and method.name != "init"
        and not method.params
        and len(method.body) == 1
        and isinstance(body_at(method, 0), A.Return)
        and body_at(method, 0).value is not None
    )


def _node_uses_var_array_field_safely(
    node: A.ASTNode, var_name: str, field_name: str
) -> bool:
    if isinstance(node, A.FieldAssign) and _is_var_field(
        node.object_expr, var_name, field_name
    ):
        return False
    if isinstance(node, A.Call) and node.args:
        if _is_var_field(arg_at(node, 0), var_name, field_name):
            if node.name not in {"array_get", "array_len"}:
                return False
            return all(
                _node_uses_var_array_field_safely(arg, var_name, field_name)
                for arg in node.args[1:]
            )
    if _is_var_field(node, var_name, field_name):
        return False
    return all(
        _node_uses_var_array_field_safely(child, var_name, field_name)
        for child in _node_children(node)
    )


def emit_stack_array_constructor_llvm(
    stmtgen: Any,
    class_name: str,
    var_name: str,
    new_expr: A.NewExpr,
    instance_ptr: ir.Value,
    init_method: A.Function,
    plans: dict[str, StackArrayFieldPlan],
) -> bool:
    """Replay a simple constructor, backing planned array fields by stack storage."""
    cg = stmtgen.codegen
    call_emitter = cg.expr_generator.call_emitter
    saved_locals = cg.locals.copy()
    saved_types = cg.local_decl_types.copy()
    saved_this = getattr(cg, "current_this", None)
    try:
        cg.current_this = instance_ptr
        cg.locals["this"] = instance_ptr
        for index, param in enumerate(init_method.params or []):
            if index >= len(new_expr.args) or len(param) < 2:
                return False
            pname, ptype = param[0], param[1]
            arg_expr = new_expr.args[index]
            if call_emitter._can_elide_virtual_string_arg(
                class_name, "init", index, arg_expr
            ):
                record_virtual_string_arg(
                    cg,
                    arg_expr,
                    class_name,
                    "init",
                    index,
                    var_name,
                    "constructor_replay_needs_length_not_bytes",
                )
                arg_value = ir.Constant(ir.IntType(8).as_pointer(), None)
            else:
                arg_value = cg.generate_expr(arg_expr)
            cg.locals[pname] = arg_value
            cg.local_decl_types[pname] = parsed_type_to_str(ptype)
            if parsed_type_to_str(ptype).strip().lower() in {"string", "str"}:
                cg.locals[f"__ailang_{pname}_len"] = (
                    call_emitter._emit_string_len_for_expr(arg_expr, arg_value)
                )

        for stmt in init_method.body:
            if not isinstance(stmt, A.FieldAssign):
                return False
            field_name = stmt.field_name
            if field_name in plans:
                plan = plans[field_name]
                if _is_array_new(stmt.value) is not None:
                    _emit_stack_array_field_init_llvm(
                        stmtgen, class_name, var_name, instance_ptr, plan
                    )
                else:
                    pushed = _is_this_field_push(stmt.value, field_name)
                    if pushed is None:
                        return False
                    _emit_stack_array_field_push_llvm(
                        stmtgen,
                        class_name,
                        var_name,
                        instance_ptr,
                        plan,
                        pushed,
                    )
                continue
            stmtgen.generate_stmt(stmt)
        return True
    finally:
        cg.locals = saved_locals
        cg.local_decl_types = saved_types
        cg.current_this = saved_this


def _emit_stack_array_field_init_llvm(
    stmtgen: Any,
    class_name: str,
    var_name: str,
    instance_ptr: ir.Value,
    plan: StackArrayFieldPlan,
) -> None:
    i64 = ir.IntType(64)
    i32 = ir.IntType(32)
    storage = stmtgen.codegen.alloca_in_entry_block(
        ir.ArrayType(i64, plan.capacity + 2),
        f"{var_name}_{plan.field_name}_stack_array",
    )
    header_len = stmtgen.builder.gep(
        storage,
        [ir.Constant(i32, 0), ir.Constant(i32, 0)],
        name=f"{plan.field_name}_len_hdr",
    )
    header_cap = stmtgen.builder.gep(
        storage,
        [ir.Constant(i32, 0), ir.Constant(i32, 1)],
        name=f"{plan.field_name}_cap_hdr",
    )
    data_ptr = stmtgen.builder.gep(
        storage,
        [ir.Constant(i32, 0), ir.Constant(i32, 2)],
        name=f"{plan.field_name}_stack_data",
    )
    stmtgen.builder.store(ir.Constant(i64, 0), header_len)
    stmtgen.builder.store(ir.Constant(i64, plan.capacity), header_cap)
    field_idx, _ = stmtgen.codegen.get_field_info(class_name, plan.field_name)
    field_ptr = stmtgen.builder.gep(
        instance_ptr,
        [ir.Constant(i32, 0), ir.Constant(i32, field_idx)],
        name=f"{plan.field_name}_ptr",
    )
    stmtgen.builder.store(data_ptr, field_ptr)
    stmtgen.codegen._stack_array_field_values[(var_name, plan.field_name)] = ()


def _emit_stack_array_field_push_llvm(
    stmtgen: Any,
    class_name: str,
    var_name: str,
    instance_ptr: ir.Value,
    plan: StackArrayFieldPlan,
    value_expr: A.ASTNode,
) -> None:
    i64 = ir.IntType(64)
    i32 = ir.IntType(32)
    field_idx, _ = stmtgen.codegen.get_field_info(class_name, plan.field_name)
    field_ptr = stmtgen.builder.gep(
        instance_ptr,
        [ir.Constant(i32, 0), ir.Constant(i32, field_idx)],
        name=f"{plan.field_name}_ptr",
    )
    data_ptr = stmtgen.builder.load(field_ptr, name=f"{plan.field_name}_stack_loaded")
    hdr = stmtgen.builder.gep(
        data_ptr,
        [ir.Constant(i32, -2)],
        name=f"{plan.field_name}_stack_hdr",
    )
    length = stmtgen.builder.load(hdr, name=f"{plan.field_name}_stack_len")
    dest = stmtgen.builder.gep(data_ptr, [length], name=f"{plan.field_name}_stack_dest")
    value = stmtgen.codegen.ensure_int64(stmtgen.codegen.generate_expr(value_expr))
    stmtgen.builder.store(value, dest)
    key = (var_name, plan.field_name)
    existing = stmtgen.codegen._stack_array_field_values.get(key, ())
    stmtgen.codegen._stack_array_field_values[key] = (*existing, value)
    next_len = stmtgen.builder.add(
        length,
        ir.Constant(i64, 1),
        name=f"{plan.field_name}_stack_len_next",
    )
    stmtgen.builder.store(next_len, hdr)


def emit_stack_array_c_declarations(
    emitter: Any,
    var_name: str,
    plans: dict[str, StackArrayFieldPlan],
    scalar_fields: set[str] | None = None,
) -> None:
    """Declare backing buffers in the caller scope, not constructor block scope."""
    var = emitter._mangle_var(var_name)
    scalar_fields = scalar_fields or set()
    for field_name, plan in plans.items():
        data_name = f"__ailang_stack_{var}_{field_name}_data"
        emitter.emit(f"int64_t {data_name}[{plan.capacity}];")
        if field_name in scalar_fields:
            for index, _pushed in enumerate(plan.pushes):
                value_name = f"__ailang_stack_{var}_{field_name}_{index}"
                emitter.emit(f"int64_t {value_name};")


def emit_stack_array_constructor_c(
    emitter: Any,
    var_name: str,
    class_name: str,
    new_expr: A.NewExpr,
    init_method: A.Function,
    plans: dict[str, StackArrayFieldPlan],
    scalar_fields: set[str] | None = None,
) -> bool:
    """Replay a simple C stack-class constructor with stack-backed arrays."""
    from transpiler.class_field_ownership import (
        auto_owned_field_kind,
        is_auto_owned_field_type,
        is_auto_owned_param,
        is_string_type,
        owned_field_flag_name,
        owned_param_flag_name,
        string_len_field_name,
        string_len_param_name,
    )

    var = emitter._mangle_var(var_name)
    storage = f"__ailang_stack_{var}"
    scalar_fields = scalar_fields or set()
    for index, param in enumerate(init_method.params or []):
        if index >= len(new_expr.args) or len(param) < 2:
            return False
        pname, ptype = param[0], param[1]
        pname_c = emitter._mangle_var(pname)
        arg = new_expr.args[index]
        can_elide_virtual = emitter._can_elide_virtual_string_arg(
            class_name, "init", index, arg
        )
        if can_elide_virtual:
            record_virtual_string_arg(
                emitter,
                arg,
                class_name,
                "init",
                index,
                var_name,
                "constructor_replay_needs_length_not_bytes",
            )
        arg_expr = "NULL" if can_elide_virtual else emitter.expr(arg)
        emitter.emit(f"  {emitter._ailang_type_to_c(ptype)} {pname_c} = {arg_expr};")
        if is_string_type(ptype):
            len_name = string_len_param_name(pname)
            emitter.emit(
                f"  int64_t {len_name} = {emitter._emit_known_strlen(arg, arg_expr)};"
            )
            emitter.declared_vars.add(len_name)
        if is_auto_owned_param(param, emitter.classes):
            kind = auto_owned_field_kind(ptype, emitter.classes)
            param_owned = (
                False
                if can_elide_virtual or kind is None
                else emitter._expr_produces_owned_value(arg, kind, ptype)
            )
            emitter.emit(
                f"  int {owned_param_flag_name(pname)} = " f"{1 if param_owned else 0};"
            )

    for stmt in init_method.body:
        if not isinstance(stmt, A.FieldAssign):
            return False
        field_name = stmt.field_name
        if field_name in plans:
            plan = plans[field_name]
            if _is_array_new(stmt.value) is not None:
                data_name = f"__ailang_stack_{var}_{field_name}_data"
                emitter.emit(f"  {storage}.{field_name}.data = {data_name};")
                emitter.emit(f"  {storage}.{field_name}.length = 0;")
                emitter.emit(f"  {storage}.{field_name}.capacity = {plan.capacity};")
                emitter.emit(f"  {storage}.{owned_field_flag_name(field_name)} = 0;")
                continue
            pushed = _is_this_field_push(stmt.value, field_name)
            if pushed is None:
                return False
            pushed_expr = emitter.expr(pushed)
            if field_name in scalar_fields:
                key = (var_name, field_name)
                index = len(emitter._stack_array_field_values.get(key, ()))
                value_name = f"__ailang_stack_{var}_{field_name}_{index}"
                emitter.emit(f"  {value_name} = {pushed_expr};")
                pushed_expr = value_name
                existing = emitter._stack_array_field_values.get(key, ())
                emitter._stack_array_field_values[key] = (*existing, value_name)
            emitter.emit(
                f"  {storage}.{field_name}.data[{storage}.{field_name}.length++] = "
                f"{pushed_expr};"
            )
            continue

        field_type = emitter._field_ailang_type(class_name, field_name)
        val = emitter.expr(stmt.value)
        emitter.emit(f"  {storage}.{field_name} = {val};")
        if is_string_type(field_type):
            emitter.emit(
                f"  {storage}.{string_len_field_name(field_name)} = "
                f"{emitter._emit_known_strlen(stmt.value, val)};"
            )
        if is_auto_owned_field_type(field_type, emitter.classes):
            kind = auto_owned_field_kind(field_type, emitter.classes)
            field_owned: str | int = 0
            if isinstance(stmt.value, A.Variable):
                flag = owned_param_flag_name(stmt.value.name)
                field_owned = flag
            elif kind is not None and emitter._expr_produces_owned_value(
                stmt.value, kind, field_type
            ):
                field_owned = 1
            emitter.emit(
                f"  {storage}.{owned_field_flag_name(field_name)} = {field_owned};"
            )
    return True

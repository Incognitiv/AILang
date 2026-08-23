"""Small helpers that keep optimizer report call sites compact."""

from __future__ import annotations

from parser import ast as A
from typing import Any


def record_stack_array_fields(
    emitter: Any,
    node: A.ASTNode,
    var_name: str,
    class_name: str,
    plans: dict[str, Any],
    scalar_fields: set[str],
) -> None:
    for field_name, plan in plans.items():
        emitter._record_optimizer_decision(
            node,
            opt_kind="stack_array_field",
            target=f"{var_name}.{field_name}",
            decision="scalarized" if field_name in scalar_fields else "stack_backed",
            reason="fixed_constructor_push_chain_non_escaping",
            details={
                "class": class_name,
                "capacity": int(getattr(plan, "capacity", 0) or 0),
                "push_count": len(getattr(plan, "pushes", ())),
            },
        )


def record_stack_class(
    emitter: Any, node: A.ASTNode, var_name: str, class_name: str
) -> None:
    emitter._record_optimizer_decision(
        node,
        opt_kind="stack_class",
        target=var_name,
        decision="stack_lowered",
        reason="non_escaping_owned_class_local",
        details={"class": class_name},
    )


def record_virtual_string_arg(
    emitter: Any,
    node: A.ASTNode,
    class_name: str,
    method_name: str,
    index: int,
    receiver: str,
    reason: str,
) -> None:
    emitter._record_optimizer_decision(
        node,
        opt_kind="virtual_string",
        target=f"{class_name}.{method_name}.arg{index}",
        decision="elided_materialization",
        reason=reason,
        details={"class": class_name, "receiver": receiver},
    )

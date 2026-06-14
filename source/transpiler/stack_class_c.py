"""C helpers for stack-lowered class locals."""

from __future__ import annotations

from typing import Any

from transpiler.class_field_ownership import (
    auto_owned_fields,
    owned_field_flag_name,
    string_len_field_name,
)


def emit_stack_class_zero_init(emitter: Any, class_name: str, target: str) -> None:
    for field_name, _field_type, kind in auto_owned_fields(
        emitter.classes.get(class_name), emitter.classes
    ):
        if kind in {"string", "class", "dict"}:
            emitter.emit(f"{target}.{field_name} = NULL;")
        elif kind in {"array", "str_array"}:
            emitter.emit(f"{target}.{field_name}.data = NULL;")
            emitter.emit(f"{target}.{field_name}.length = 0;")
            emitter.emit(f"{target}.{field_name}.capacity = 0;")
        if kind == "string":
            emitter.emit(f"{target}.{string_len_field_name(field_name)} = 0;")
        emitter.emit(f"{target}.{owned_field_flag_name(field_name)} = 0;")

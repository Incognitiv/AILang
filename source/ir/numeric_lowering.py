"""Typed-IR planning for fixed numeric binary return expressions."""

from __future__ import annotations

from type_semantics import (
    ConversionKind,
    canonical_type_name,
    classify_conversion,
    is_implicit_conversion,
    join_numeric_types,
)

from .model import Binary, Block, Convert, Instruction, Return, Value


class IRLoweringError(TypeError):
    """The frontend contract cannot be represented by this typed-IR slice."""


def _coerce(
    value: Value,
    target_type: str,
    instructions: list[Instruction],
) -> Value:
    target = canonical_type_name(target_type)
    kind = classify_conversion(value.type_name, target)
    if kind is ConversionKind.IDENTITY:
        return value
    if not is_implicit_conversion(kind):
        raise IRLoweringError(
            f"typed IR requires an explicit conversion from "
            f"{value.type_name} to {target}"
        )

    result = Value(f"%t{len(instructions)}", target)
    instructions.append(Convert(source=value, result=result, kind=kind))
    return result


def lower_numeric_binary_return(
    operator: str,
    left_type: str,
    right_type: str,
    return_type: str,
) -> Block:
    """Lower one numeric binary expression followed by a typed return.

    Every implicit conversion is materialized as an IR node. This makes the
    binary instruction itself type-uniform and leaves no narrowing/widening
    decision for C, LLVM, JIT, or future native backends to invent.
    """

    left = Value("%arg0", canonical_type_name(left_type))
    right = Value("%arg1", canonical_type_name(right_type))
    joined = join_numeric_types(left.type_name, right.type_name)
    if joined is None:
        raise IRLoweringError(
            f"no lossless numeric join for {left.type_name} and {right.type_name}"
        )

    instructions: list[Instruction] = []
    typed_left = _coerce(left, joined, instructions)
    typed_right = _coerce(right, joined, instructions)
    result = Value(f"%t{len(instructions)}", joined)
    instructions.append(
        Binary(operator=operator, left=typed_left, right=typed_right, result=result)
    )
    returned = _coerce(result, return_type, instructions)
    instructions.append(Return(value=returned))
    return Block(instructions=tuple(instructions))

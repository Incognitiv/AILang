"""Backend-neutral typed IR nodes.

The IR records language decisions that have already been made. Backends may
choose how to encode an instruction, but must not change its value type or
conversion kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from type_semantics import ConversionKind, canonical_type_name


@dataclass(frozen=True, slots=True)
class Value:
    """A named SSA-like value with one canonical AILang type."""

    name: str
    type_name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("IR value name cannot be empty")
        canonical = canonical_type_name(self.type_name)
        if not canonical:
            raise ValueError("IR value type cannot be empty")
        object.__setattr__(self, "type_name", canonical)


@dataclass(frozen=True, slots=True)
class Convert:
    """An explicit language-level conversion between two typed values."""

    source: Value
    result: Value
    kind: ConversionKind


@dataclass(frozen=True, slots=True)
class Binary:
    """A binary operation whose operands already have the result type."""

    operator: str
    left: Value
    right: Value
    result: Value

    def __post_init__(self) -> None:
        operand_type = self.result.type_name
        if self.left.type_name != operand_type or self.right.type_name != operand_type:
            raise ValueError("binary IR operands must match the result type")


@dataclass(frozen=True, slots=True)
class Return:
    """Return one fully converted value."""

    value: Value


Instruction = Convert | Binary | Return


@dataclass(frozen=True, slots=True)
class Block:
    """Straight-line typed IR block used by the first lowering slice."""

    instructions: tuple[Instruction, ...]


def render_instruction(instruction: Instruction) -> str:
    """Render deterministic human-readable IR for golden tests and diagnostics."""

    if isinstance(instruction, Convert):
        return (
            f"{instruction.result.name}:{instruction.result.type_name} = "
            f"convert.{instruction.kind.value} "
            f"{instruction.source.name}:{instruction.source.type_name}"
        )
    if isinstance(instruction, Binary):
        return (
            f"{instruction.result.name}:{instruction.result.type_name} = "
            f"{instruction.operator} {instruction.left.name}, {instruction.right.name}"
        )
    return f"ret {instruction.value.name}:{instruction.value.type_name}"


def render_block(block: Block) -> str:
    """Render one instruction per line."""

    return "\n".join(render_instruction(instruction) for instruction in block.instructions)

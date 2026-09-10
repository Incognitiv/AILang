"""Backend-neutral typed IR nodes.

The IR records language decisions that have already been made. Backends may
choose how to encode an instruction, but must not change its value type or
conversion kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from type_semantics import ConversionKind, canonical_type_name, classify_conversion


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

    def __post_init__(self) -> None:
        expected = classify_conversion(self.source.type_name, self.result.type_name)
        if self.kind is not expected:
            raise ValueError(
                "IR conversion kind does not match canonical type semantics: "
                f"{self.source.type_name} -> {self.result.type_name} is "
                f"{expected.value}, not {self.kind.value}"
            )
        if expected is ConversionKind.IDENTITY:
            raise ValueError("identity conversions must reuse the existing IR value")


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

    def __post_init__(self) -> None:
        if not self.instructions:
            raise ValueError("IR block cannot be empty")
        returns = [
            index
            for index, instruction in enumerate(self.instructions)
            if isinstance(instruction, Return)
        ]
        if returns != [len(self.instructions) - 1]:
            raise ValueError("straight-line IR block must end with exactly one return")


@dataclass(frozen=True, slots=True)
class FunctionIR:
    """Typed IR for one function with a single straight-line entry block."""

    name: str
    parameters: tuple[Value, ...]
    return_type: str
    entry: Block

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("IR function name cannot be empty")
        object.__setattr__(self, "return_type", canonical_type_name(self.return_type))
        returned = self.entry.instructions[-1]
        if not isinstance(returned, Return):
            raise ValueError("IR function entry block must terminate with return")
        if returned.value.type_name != self.return_type:
            raise ValueError("IR function return value must match declared return type")


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

    return "\n".join(
        render_instruction(instruction) for instruction in block.instructions
    )


def render_function(function: FunctionIR) -> str:
    """Render a deterministic function header followed by its entry block."""

    params = ", ".join(
        f"{parameter.name}:{parameter.type_name}" for parameter in function.parameters
    )
    body = render_block(function.entry)
    return f"func {function.name}({params}) -> {function.return_type}\n{body}"

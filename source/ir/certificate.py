"""Stable data-only certificate format for external Typed IR verification.

The certificate intentionally contains no executable code.  It is a compact,
versioned serialization of one ``FunctionIR`` that an independent checker can
parse and validate without trusting Python's IR constructors.
"""

from __future__ import annotations

from .model import Binary, Convert, FunctionIR, Return

_CERTIFICATE_TAG = "AILANG_TYPED_IR_CERTIFICATE"
_CERTIFICATE_VERSION = "1"


def _field(value: str) -> str:
    if not value or "\t" in value or "\n" in value or "\r" in value:
        raise ValueError("IR certificate fields must be non-empty single-line values")
    return value


def serialize_function_certificate(function: FunctionIR) -> str:
    """Serialize one function into the data format consumed by Lean.

    Types are repeated on instruction operands deliberately.  The Lean checker
    validates those repetitions against its own SSA environment, so tampering
    with either a name or a type cannot silently change the graph being checked.
    """

    lines = [f"{_CERTIFICATE_TAG}\t{_CERTIFICATE_VERSION}"]
    lines.append("\t".join(("F", _field(function.name), _field(function.return_type))))

    for parameter in function.parameters:
        lines.append(
            "\t".join(("P", _field(parameter.name), _field(parameter.type_name)))
        )

    for instruction in function.entry.instructions:
        if isinstance(instruction, Convert):
            lines.append(
                "\t".join(
                    (
                        "C",
                        _field(instruction.source.name),
                        _field(instruction.source.type_name),
                        _field(instruction.result.name),
                        _field(instruction.result.type_name),
                        _field(instruction.kind.value),
                    )
                )
            )
        elif isinstance(instruction, Binary):
            lines.append(
                "\t".join(
                    (
                        "B",
                        _field(instruction.operator),
                        _field(instruction.left.name),
                        _field(instruction.left.type_name),
                        _field(instruction.right.name),
                        _field(instruction.right.type_name),
                        _field(instruction.result.name),
                        _field(instruction.result.type_name),
                    )
                )
            )
        elif isinstance(instruction, Return):
            lines.append(
                "\t".join(
                    (
                        "R",
                        _field(instruction.value.name),
                        _field(instruction.value.type_name),
                    )
                )
            )
        else:  # pragma: no cover - the union is closed, keep serialization fail-closed.
            raise TypeError(f"unsupported IR instruction {type(instruction).__name__}")

    return "\n".join(lines) + "\n"

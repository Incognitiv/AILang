"""Lower validated AILang AST into backend-neutral typed IR.

This first frontend slice is intentionally narrow and fail-closed: one function,
one valued return, and one numeric binary expression over function parameters.
Unsupported syntax is rejected rather than being silently reinterpreted by a
backend.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str

from type_semantics import canonical_type_name

from .model import FunctionIR, Value
from .numeric_lowering import IRLoweringError, lower_numeric_binary_values

_BINARY_OPERATORS = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
}


def _type_name(parsed_type: object) -> str:
    return canonical_type_name(parsed_type_to_str(parsed_type))


def _parameter_values(
    function: A.Function,
) -> tuple[tuple[Value, ...], dict[str, Value]]:
    parameters = tuple(
        Value(f"%arg{index}", _type_name(param_type))
        for index, (_, param_type, _) in enumerate(function.params)
    )
    by_name = {
        param_name: parameter
        for (param_name, _, _), parameter in zip(
            function.params, parameters, strict=True
        )
    }
    return parameters, by_name


def _parameter_operand(expr: A.ASTNode, values: dict[str, Value]) -> Value:
    if not isinstance(expr, A.Variable):
        raise IRLoweringError("typed IR frontend currently requires parameter operands")
    try:
        return values[expr.name]
    except KeyError as exc:
        raise IRLoweringError(
            f"typed IR frontend does not know local value '{expr.name}'"
        ) from exc


def lower_function(function: A.Function) -> FunctionIR:
    """Lower the first supported validated-function shape into typed IR."""

    if len(function.body) != 1:
        raise IRLoweringError(
            "typed IR frontend currently requires one straight-line return"
        )
    (returned,) = function.body
    if not isinstance(returned, A.Return):
        raise IRLoweringError(
            "typed IR frontend currently requires one straight-line return"
        )
    if returned.value is None:
        raise IRLoweringError("typed IR frontend requires a valued return")
    if not isinstance(returned.value, A.BinaryOp):
        raise IRLoweringError("typed IR frontend currently requires a binary return")

    operator = _BINARY_OPERATORS.get(returned.value.op)
    if operator is None:
        raise IRLoweringError(
            f"typed IR frontend does not support binary operator {returned.value.op!r}"
        )

    parameters, values = _parameter_values(function)
    left = _parameter_operand(returned.value.left, values)
    right = _parameter_operand(returned.value.right, values)
    return_type = _type_name(function.return_type)
    entry = lower_numeric_binary_values(operator, left, right, return_type)
    return FunctionIR(
        name=function.name,
        parameters=parameters,
        return_type=return_type,
        entry=entry,
    )


def lower_program(program: list[A.ASTNode]) -> tuple[FunctionIR, ...]:
    """Lower top-level functions supported by the current typed-IR frontend."""

    functions = [node for node in program if isinstance(node, A.Function)]
    if len(functions) != len(program):
        raise IRLoweringError(
            "typed IR frontend stage 3 accepts top-level functions only"
        )
    return tuple(lower_function(function) for function in functions)

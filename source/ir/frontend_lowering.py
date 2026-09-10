"""Lower validated AILang AST into backend-neutral typed IR.

Stage 5 remains deliberately fail-closed. It accepts straight-line fixed-numeric
functions with typed local declarations, numeric literals, nested arithmetic,
and one final valued return. Mutation, calls, control flow, and non-numeric
expressions remain outside this slice.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str

from type_semantics import FLOAT_PRECISION_BITS, canonical_type_name

from .model import Block, Constant, FunctionIR, Instruction, Return, Value
from .numeric_lowering import (
    IRLoweringError,
    coerce_value,
    emit_numeric_binary,
    fresh_value,
)

_BINARY_OPERATORS = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
}

_FLOAT_LITERAL_TYPES = {
    "f": "f32",
    "d": "f64",
    "q": "f128",
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


def _is_contextual_float(expr: A.ASTNode) -> bool:
    return isinstance(expr, A.Number) and expr.is_float and not expr.precision_explicit


def _constant_type(expr: A.Number, context_type: str | None) -> str:
    if not expr.is_float:
        return "i64"
    if expr.precision_explicit:
        return _FLOAT_LITERAL_TYPES.get(expr.precision, "f64")
    if context_type in FLOAT_PRECISION_BITS:
        return str(context_type)
    return "f64"


def _constant_text(expr: A.Number) -> str:
    """Return the exact numeric token payload without a type suffix.

    ``Number.value`` is intentionally not consulted here. In particular, an
    f128/quad literal must not round through Python's binary64 ``float`` before
    it reaches Typed IR. Integer base spelling and floating exponent spelling
    are preserved exactly; only the source-level precision/long suffix is
    removed because the IR result type already records that information.
    """

    source_text = expr.source_text
    if expr.is_float and expr.precision_explicit:
        return source_text[:-1]
    if not expr.is_float and expr.is_long:
        return source_text[:-1]
    return source_text


def _emit_constant(
    expr: A.Number,
    instructions: list[Instruction],
    context_type: str | None = None,
) -> Value:
    type_name = _constant_type(expr, context_type)
    result = fresh_value(instructions, type_name)
    literal_kind = "float" if expr.is_float else "int"
    instructions.append(
        Constant(
            literal_kind=literal_kind,
            value_text=_constant_text(expr),
            result=result,
        )
    )
    return result


def _lookup_variable(expr: A.Variable, values: dict[str, Value]) -> Value:
    try:
        return values[expr.name]
    except KeyError as exc:
        raise IRLoweringError(
            f"typed IR frontend does not know local value '{expr.name}'"
        ) from exc


def _lower_binary(
    expr: A.BinaryOp,
    values: dict[str, Value],
    instructions: list[Instruction],
) -> Value:
    operator = _BINARY_OPERATORS.get(expr.op)
    if operator is None:
        raise IRLoweringError(
            f"typed IR frontend does not support binary operator {expr.op!r}"
        )

    left_is_contextual = _is_contextual_float(expr.left)
    right_is_contextual = _is_contextual_float(expr.right)

    if left_is_contextual and not right_is_contextual:
        right = _lower_expr(expr.right, values, instructions)
        context = right.type_name if right.type_name in FLOAT_PRECISION_BITS else None
        left = _lower_expr(expr.left, values, instructions, context_type=context)
    else:
        left = _lower_expr(expr.left, values, instructions)
        context = (
            left.type_name
            if right_is_contextual and left.type_name in FLOAT_PRECISION_BITS
            else None
        )
        right = _lower_expr(expr.right, values, instructions, context_type=context)

    return emit_numeric_binary(operator, left, right, instructions)


def _lower_expr(
    expr: A.ASTNode,
    values: dict[str, Value],
    instructions: list[Instruction],
    context_type: str | None = None,
) -> Value:
    if isinstance(expr, A.Variable):
        return _lookup_variable(expr, values)
    if isinstance(expr, A.Number):
        return _emit_constant(expr, instructions, context_type)
    if isinstance(expr, A.BinaryOp):
        return _lower_binary(expr, values, instructions)
    raise IRLoweringError(
        f"typed IR frontend does not support expression {type(expr).__name__}"
    )


def _bind_local(
    declaration: A.VarDecl,
    values: dict[str, Value],
    instructions: list[Instruction],
) -> None:
    if declaration.var_name in values:
        raise IRLoweringError(
            f"typed IR frontend does not allow local shadowing for "
            f"'{declaration.var_name}'"
        )
    if declaration.init_value is None:
        raise IRLoweringError(
            f"typed IR local '{declaration.var_name}' requires an initializer"
        )

    initialized = _lower_expr(declaration.init_value, values, instructions)
    declared_type = _type_name(declaration.type_name)
    values[declaration.var_name] = coerce_value(
        initialized, declared_type, instructions
    )


def lower_function(function: A.Function) -> FunctionIR:
    """Lower one supported straight-line fixed-numeric function."""

    if not function.body:
        raise IRLoweringError("typed IR frontend requires a final valued return")

    *statements, returned = function.body
    if not isinstance(returned, A.Return) or returned.value is None:
        raise IRLoweringError("typed IR frontend requires one final valued return")

    parameters, values = _parameter_values(function)
    instructions: list[Instruction] = []

    for statement in statements:
        if not isinstance(statement, A.VarDecl):
            raise IRLoweringError(
                "typed IR frontend accepts typed local declarations before return only"
            )
        _bind_local(statement, values, instructions)

    result = _lower_expr(returned.value, values, instructions)
    return_type = _type_name(function.return_type)
    result = coerce_value(result, return_type, instructions)
    instructions.append(Return(value=result))

    return FunctionIR(
        name=function.name,
        parameters=parameters,
        return_type=return_type,
        entry=Block(instructions=tuple(instructions)),
    )


def lower_program(program: list[A.ASTNode]) -> tuple[FunctionIR, ...]:
    """Lower top-level functions supported by the current typed-IR frontend."""

    functions = [node for node in program if isinstance(node, A.Function)]
    if len(functions) != len(program):
        raise IRLoweringError(
            "typed IR frontend stage 5 accepts top-level functions only"
        )
    return tuple(lower_function(function) for function in functions)

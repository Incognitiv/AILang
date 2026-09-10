"""Backend-neutral typed intermediate representation."""

from .certificate import serialize_function_certificate
from .frontend_lowering import lower_function, lower_program
from .model import (
    Binary,
    Block,
    Constant,
    Convert,
    FunctionIR,
    Return,
    Value,
    render_block,
    render_function,
)
from .numeric_lowering import (
    IRLoweringError,
    coerce_value,
    emit_numeric_binary,
    lower_numeric_binary_return,
    lower_numeric_binary_values,
)

__all__ = [
    "Binary",
    "Block",
    "Constant",
    "Convert",
    "FunctionIR",
    "IRLoweringError",
    "Return",
    "Value",
    "coerce_value",
    "emit_numeric_binary",
    "lower_function",
    "lower_numeric_binary_return",
    "lower_numeric_binary_values",
    "lower_program",
    "render_block",
    "render_function",
    "serialize_function_certificate",
]

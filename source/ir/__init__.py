"""Backend-neutral typed intermediate representation."""

from .frontend_lowering import lower_function, lower_program
from .model import (
    Binary,
    Block,
    Convert,
    FunctionIR,
    Return,
    Value,
    render_block,
    render_function,
)
from .numeric_lowering import (
    IRLoweringError,
    lower_numeric_binary_return,
    lower_numeric_binary_values,
)

__all__ = [
    "Binary",
    "Block",
    "Convert",
    "FunctionIR",
    "IRLoweringError",
    "Return",
    "Value",
    "lower_function",
    "lower_numeric_binary_return",
    "lower_numeric_binary_values",
    "lower_program",
    "render_block",
    "render_function",
]

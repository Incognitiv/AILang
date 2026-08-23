"""Expression and module AST nodes."""

from __future__ import annotations

from typing import Any, Optional

from .ast_base import ASTNode


class Import(ASTNode):
    """Import statement: import [target] module_name [as alias]"""

    def __init__(
        self,
        module_path: str,
        alias: Optional[str] = None,
        target_os: Optional[str] = None,
    ) -> None:
        self.module_path: str = module_path  # e.g., "bitwise" or "utils.helpers"
        self.alias: Optional[str] = alias  # e.g., "bits" in "import bitwise as bits"
        self.target_os: Optional[str] = target_os


class FromImport(ASTNode):
    """From-import statement: from module import name1, name2"""

    def __init__(self, module_path: str, names: list[str]) -> None:
        self.module_path: str = module_path
        self.names: list[str] = names  # Functions/records to import


class Use(ASTNode):
    """Use statement for standard library: use std.io, use std.math"""

    def __init__(self, module_path: str, names: Optional[list[str]] = None) -> None:
        self.module_path: str = (
            module_path  # e.g., "std.io", "std.math", "freestanding"
        )
        self.names: Optional[list[str]] = names  # Specific items, or None for all


class Library(ASTNode):
    """Library declaration: @library(\"name\")"""

    def __init__(self, name: str) -> None:
        self.name: str = name


# ============================================================================
# Literals
# ============================================================================
class Number(ASTNode):
    value: Any  # Can be int or float
    is_float: bool
    is_long: bool
    precision: str

    def __init__(
        self, value: str, is_long: bool = False, is_float: bool = False
    ) -> None:
        if is_float:
            self.value = float(value.rstrip("fFdDqQ"))
            self.is_float = True
            # Determine precision from suffix
            suffix = value[-1].lower() if value and value[-1].isalpha() else "d"
            self.precision = suffix  # 'f', 'd', or 'q'
            self.is_long = False
        else:
            val_str = value.rstrip("lL")
            self.value = int(val_str, 0)  # auto-detect base (0x,0b,0o)
            self.is_long = is_long
            self.is_float = False
            self.precision = ""


class Bool(ASTNode):
    value: bool

    def __init__(self, value: bool) -> None:
        self.value: bool = value  # True or False


class Null(ASTNode):
    """Represents null/nil literal value."""

    ...


class StringLit(ASTNode):
    def __init__(self, value: str) -> None:
        # Remove quotes and handle ALL escape sequences
        s = value[1:-1]
        # Process escapes in correct order (backslash-backslash first)
        s = s.replace("\\\\", "\x00BACKSLASH\x00")  # Temp placeholder
        s = s.replace("\\n", "\n")
        s = s.replace("\\t", "\t")
        s = s.replace("\\r", "\r")
        s = s.replace("\\0", "\0")
        s = s.replace('\\"', '"')
        s = s.replace("\\'", "'")
        s = s.replace("\x00BACKSLASH\x00", "\\")  # Restore backslash
        self.value: str = s


class InterpolatedString(ASTNode):
    """String with embedded expressions: "Hello #{name}, count: #{count}"
    parts is a list of either:
      - str: literal text portions
      - ASTNode: expressions to be evaluated and converted to string
    """

    def __init__(self, parts: list[Any]) -> None:
        self.parts: list[Any] = parts  # [str, expr, str, expr, ...]


class ArrayLit(ASTNode):
    def __init__(self, elements: list[Any]) -> None:
        self.elements: list[Any] = elements


class TupleLit(ASTNode):
    """Tuple literal: (a, b, c) or a, b, c"""

    def __init__(self, elements: list[Any]) -> None:
        self.elements: list[Any] = elements


class TupleAccess(ASTNode):
    """Tuple element access: tuple.0, tuple.1, etc."""

    def __init__(self, tuple_expr: Any, index: int) -> None:
        self.tuple_expr: Any = tuple_expr
        self.index: int = index


class ListComprehension(ASTNode):
    """List comprehension: [expr for var in iterable] or [expr for var in iterable if cond]"""

    def __init__(
        self,
        expr: Any,
        var_name: str,
        iterable: Any,
        condition: Optional[Any] = None,
    ) -> None:
        self.expr: Any = expr  # Expression to evaluate for each element
        self.var_name: str = var_name  # Loop variable name
        self.iterable: Any = iterable  # Range or array to iterate over
        self.condition: Optional[Any] = condition  # Optional filter condition


class Range(ASTNode):
    """Range expression: 1..10 (inclusive) or 1...10 (exclusive)"""

    def __init__(self, start: Any, end: Any, inclusive: bool = True) -> None:
        self.start: Any = start
        self.end: Any = end
        self.inclusive: bool = inclusive  # True for .., False for ...


class DictLit(ASTNode):
    """Dictionary literal: {key1: val1, key2: val2, ...}"""

    def __init__(self, pairs: list[tuple[Any, Any]]) -> None:
        self.pairs: list[tuple[Any, Any]] = pairs  # [(key_expr, value_expr), ...]


class DictAccess(ASTNode):
    """Dictionary access: dict[key]"""

    def __init__(self, dict_expr: Any, key_expr: Any) -> None:
        self.dict_expr: Any = dict_expr
        self.key_expr: Any = key_expr


class DictAssign(ASTNode):
    """Dictionary assignment: dict[key] = value or dict[key, unsafe] = value"""

    def __init__(
        self, dict_expr: Any, key_expr: Any, value_expr: Any, unsafe: bool = False
    ) -> None:
        self.dict_expr: Any = dict_expr
        self.key_expr: Any = key_expr
        self.value_expr: Any = value_expr
        self.unsafe: bool = unsafe  # If True, bypasses bounds checking


# ============================================================================
# Expressions
# ============================================================================
class Variable(ASTNode):
    def __init__(self, name: str) -> None:
        self.name: str = name


class BinaryOp(ASTNode):
    def __init__(self, op: str, left: ASTNode, right: ASTNode) -> None:
        self.op: str = op
        self.left: ASTNode = left
        self.right: ASTNode = right


class UnaryOp(ASTNode):
    def __init__(self, op: str, operand: ASTNode) -> None:
        self.op: str = op
        self.operand: ASTNode = operand


class TernaryOp(ASTNode):
    def __init__(self, cond: ASTNode, true_expr: ASTNode, false_expr: ASTNode) -> None:
        self.cond: ASTNode = cond
        self.true_expr: ASTNode = true_expr
        self.false_expr: ASTNode = false_expr


class Call(ASTNode):
    def __init__(self, name: str, args: list[ASTNode], unsafe: bool = False) -> None:
        self.name: str = name
        self.args: list[ASTNode] = args
        self.unsafe: bool = unsafe  # If True, bypasses safety checks with user consent
        self.generic_base: Optional[str] = None
        self.generic_type_args: list[str] = []


class ArrayAccess(ASTNode):
    def __init__(self, array: ASTNode, index: ASTNode, unsafe: bool = False) -> None:
        self.array: ASTNode = array
        self.index: ASTNode = index
        self.unsafe: bool = (
            unsafe  # If True, bypasses bounds checking with user consent
        )


class StringSlice(ASTNode):
    """String/array slicing: s[start:end]
    Returns substring from start (inclusive) to end (exclusive).
    If end is None, slices to end of string.
    """

    def __init__(
        self, target: ASTNode, start: ASTNode, end: Optional[ASTNode] = None
    ) -> None:
        self.target: ASTNode = target
        self.start: ASTNode = start
        self.end: Optional[ASTNode] = end


# ============================================================================
# Statements
# ============================================================================

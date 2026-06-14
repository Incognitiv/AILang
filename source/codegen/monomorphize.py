"""
Monomorphization - Generic specialization for AILang

This module handles the instantiation of generic types and functions
by creating specialized versions for each unique type argument combination.

Example:
    Generic: record Pair[T, U] then T first; U second end
    Usage:   Pair[int, string] p = new Pair[int, string](1, "hello")
    Creates: record Pair_int_string then int first; string second end
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from parser.naming import mangle_generic_name
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    pass

__all__ = ["MonomorphizationError", "Monomorphizer", "mangle_generic_name"]


class MonomorphizationError(Exception):
    """Exception raised during generic specialization."""


def substitute_type(type_name: str, substitutions: dict[str, str]) -> str:
    """Substitute type parameters with concrete types.

    Example: substitute_type("T", {"T": "int"}) -> "int"
    """
    return substitutions.get(type_name, type_name)


def substitute_in_expr(expr: A.ASTNode, substitutions: dict[str, str]) -> A.ASTNode:
    """Recursively substitute type parameters in an expression."""
    if isinstance(expr, A.Variable):
        # Variable names aren't types, don't substitute
        return expr

    if isinstance(expr, A.NewExpr):
        # Substitute type in new expression
        new_type = substitute_type(expr.type_name, substitutions)
        return A.NewExpr(new_type, expr.args)

    if isinstance(expr, A.Call):
        # Substitute in call arguments
        new_args = [substitute_in_expr(arg, substitutions) for arg in expr.args]
        return A.Call(expr.name, new_args)

    if isinstance(expr, A.BinaryOp):
        new_left = substitute_in_expr(expr.left, substitutions)
        new_right = substitute_in_expr(expr.right, substitutions)
        return A.BinaryOp(expr.op, new_left, new_right)

    if isinstance(expr, A.UnaryOp):
        new_operand = substitute_in_expr(expr.operand, substitutions)
        return A.UnaryOp(expr.op, new_operand)

    if isinstance(expr, A.FieldAccess):
        new_obj = substitute_in_expr(expr.object_expr, substitutions)
        return A.FieldAccess(new_obj, expr.field_name)

    if isinstance(expr, A.ArrayAccess):
        new_arr = substitute_in_expr(expr.array, substitutions)
        new_idx = substitute_in_expr(expr.index, substitutions)
        return A.ArrayAccess(new_arr, new_idx)

    # M15 fix: Handle additional expression types
    if isinstance(expr, A.MethodCall):
        new_obj = substitute_in_expr(expr.object_expr, substitutions)
        new_args = [substitute_in_expr(arg, substitutions) for arg in expr.args]
        return A.MethodCall(new_obj, expr.method_name, new_args)

    if isinstance(expr, A.TernaryOp):
        new_cond = substitute_in_expr(expr.cond, substitutions)
        new_true = substitute_in_expr(expr.true_expr, substitutions)
        new_false = substitute_in_expr(expr.false_expr, substitutions)
        return A.TernaryOp(new_cond, new_true, new_false)

    if isinstance(expr, A.ArrayLit):
        new_elements = [substitute_in_expr(e, substitutions) for e in expr.elements]
        return A.ArrayLit(new_elements)

    if isinstance(expr, A.Cast):
        new_expr = substitute_in_expr(expr.expr, substitutions)
        new_type = substitute_type(str(expr.target_type), substitutions)
        return A.Cast(new_type, new_expr)

    # For literals and other nodes, return as-is
    return expr


def substitute_in_stmt(stmt: A.ASTNode, substitutions: dict[str, str]) -> A.ASTNode:
    """Recursively substitute type parameters in a statement."""
    if isinstance(stmt, A.VarDecl):
        new_type = substitute_type(parsed_type_to_str(stmt.type_name), substitutions)
        new_init = substitute_in_expr(stmt.init_value, substitutions)
        return A.VarDecl(new_type, stmt.var_name, new_init)

    if isinstance(stmt, A.Assign):
        new_value = substitute_in_expr(stmt.value, substitutions)
        return A.Assign(stmt.var_name, new_value)

    if isinstance(stmt, A.Return):
        ret_value: Optional[A.ASTNode] = None
        if stmt.value:
            ret_value = substitute_in_expr(stmt.value, substitutions)
        return A.Return(ret_value)

    if isinstance(stmt, A.If):
        new_cond = substitute_in_expr(stmt.cond, substitutions)
        new_then = [substitute_in_stmt(s, substitutions) for s in stmt.then_body]
        new_else = [substitute_in_stmt(s, substitutions) for s in stmt.else_body]
        return A.If(new_cond, new_then, new_else)

    if isinstance(stmt, A.While):
        new_cond = substitute_in_expr(stmt.cond, substitutions)
        new_body = [substitute_in_stmt(s, substitutions) for s in stmt.body]
        return A.While(new_cond, new_body)

    # M15 fix: Handle Foreach
    if isinstance(stmt, A.Foreach):
        new_iterable = substitute_in_expr(stmt.iterable, substitutions)
        new_body = [substitute_in_stmt(s, substitutions) for s in stmt.body]
        return A.Foreach(stmt.var_name, new_iterable, new_body)

    # M15 fix: Handle Match
    if isinstance(stmt, A.Match):
        new_expr = substitute_in_expr(stmt.expr, substitutions)
        new_cases = []
        for pattern, body in stmt.cases:
            new_pattern = substitute_in_expr(pattern, substitutions)
            new_body = [substitute_in_stmt(s, substitutions) for s in body]
            new_cases.append((new_pattern, new_body))
        new_default = None
        if stmt.default_case:
            new_default = [
                substitute_in_stmt(s, substitutions) for s in stmt.default_case
            ]
        return A.Match(new_expr, new_cases, new_default)

    if isinstance(stmt, A.For):
        for_init: Optional[A.ASTNode] = None
        if stmt.init is not None:
            for_init = substitute_in_stmt(stmt.init, substitutions)
        new_cond = substitute_in_expr(stmt.cond, substitutions)
        for_step: Optional[A.ASTNode] = None
        if stmt.step is not None:
            for_step = substitute_in_stmt(stmt.step, substitutions)
        new_body = [substitute_in_stmt(s, substitutions) for s in stmt.body]
        return A.For(for_init, new_cond, for_step, new_body)

    # For expressions used as statements
    if hasattr(stmt, "args") or hasattr(stmt, "left"):
        return substitute_in_expr(stmt, substitutions)

    return stmt


def monomorphize_record(
    generic_record: A.GenericRecord,
    type_args: list[str],
) -> A.RecordDef:
    """Create a specialized record from a generic record definition.

    Example:
        Generic: record Pair[T, U] then T first; U second end
        Args:    [int, string]
        Result:  record Pair_int_string then int first; string second end
    """
    if len(type_args) != len(generic_record.type_params):
        raise MonomorphizationError(
            f"Generic record {generic_record.name} expects "
            f"{len(generic_record.type_params)} type arguments, "
            f"got {len(type_args)}"
        )

    # Build substitution map: T -> int, U -> string
    substitutions: dict[str, str] = {}
    for param, arg in zip(generic_record.type_params, type_args, strict=False):
        substitutions[param.name] = arg

    # Create mangled name
    mangled_name = mangle_generic_name(generic_record.name, type_args)

    # Substitute types in fields
    new_fields: list[tuple[str, str]] = []
    for field in generic_record.fields:
        if isinstance(field, tuple):
            field_type, field_name = field[0], field[1]
        else:
            # Handle other field representations
            field_type = getattr(field, "type_name", "int")
            field_name = getattr(field, "name", str(field))

        new_type = substitute_type(parsed_type_to_str(field_type), substitutions)
        new_fields.append((new_type, field_name))

    return A.RecordDef(mangled_name, new_fields)


def monomorphize_class(
    generic_class: A.GenericClass,
    type_args: list[str],
) -> A.ClassDef:
    """Create a specialized class from a generic class definition."""
    if len(type_args) != len(generic_class.type_params):
        raise MonomorphizationError(
            f"Generic class {generic_class.name} expects "
            f"{len(generic_class.type_params)} type arguments, "
            f"got {len(type_args)}"
        )

    # Build substitution map
    substitutions: dict[str, str] = {}
    for param, arg in zip(generic_class.type_params, type_args, strict=False):
        substitutions[param.name] = arg

    # Create mangled name
    mangled_name = mangle_generic_name(generic_class.name, type_args)

    # Substitute types in fields
    new_fields: list[Any] = []
    for field in generic_class.fields:
        if isinstance(field, tuple):
            visibility, field_type, field_name = field
            new_type = substitute_type(parsed_type_to_str(field_type), substitutions)
            new_fields.append((visibility, new_type, field_name))
        else:
            new_fields.append(field)

    # Substitute types in methods
    new_methods: list[A.Function] = []
    for method in generic_class.methods:
        new_method = monomorphize_function_body(method, substitutions)
        new_methods.append(new_method)

    return A.ClassDef(mangled_name, new_fields, new_methods)


def monomorphize_function_body(
    func: A.Function,
    substitutions: dict[str, str],
) -> A.Function:
    """Substitute type parameters in a function's body."""
    # Substitute in parameter types
    new_params: list[tuple[str, Any, Optional[Any]]] = []
    for param in func.params or []:
        if isinstance(param, tuple) and len(param) >= 2:
            param_name = param[0]
            param_type = param[1] if len(param) > 1 else "int"
            default_val = param[2] if len(param) > 2 else None
            new_type = substitute_type(str(param_type), substitutions)
            new_params.append((param_name, new_type, default_val))
        else:
            new_params.append(param)

    # Substitute in return type
    new_return_type: Any = func.return_type
    if func.return_type:
        new_return_type = substitute_type(
            parsed_type_to_str(func.return_type), substitutions
        )

    # Substitute in body
    new_body = [substitute_in_stmt(stmt, substitutions) for stmt in func.body]

    return A.Function(
        name=func.name,
        params=new_params,
        return_type=new_return_type,
        body=new_body,
        is_public=func.is_public,
        decorators=func.decorators,
        is_async=func.is_async,
        is_test=func.is_test,
    )


def monomorphize_generic_function(
    generic_func: A.GenericFunction,
    type_args: list[str],
) -> A.Function:
    """Create a specialized function from a generic function definition.

    Example:
        Generic: def swap[T](a: T, b: T): T ... end
        Args:    [int]
        Result:  def swap_int(a: int, b: int): int ... end
    """
    if len(type_args) != len(generic_func.type_params):
        raise MonomorphizationError(
            f"Generic function {generic_func.name} expects "
            f"{len(generic_func.type_params)} type arguments, "
            f"got {len(type_args)}"
        )

    # Build substitution map: T -> int
    substitutions: dict[str, str] = {}
    for type_param, arg in zip(generic_func.type_params, type_args, strict=False):
        substitutions[type_param.name] = arg

    # Create mangled name
    mangled_name = mangle_generic_name(generic_func.name, type_args)

    # Substitute in parameter types
    new_params: list[tuple[str, Any, Optional[Any]]] = []
    for func_param in generic_func.params or []:
        if isinstance(func_param, tuple) and len(func_param) >= 2:
            param_name = func_param[0]
            param_type = func_param[1] if len(func_param) > 1 else "int"
            default_val = func_param[2] if len(func_param) > 2 else None
            new_type = substitute_type(str(param_type), substitutions)
            new_params.append((param_name, new_type, default_val))
        else:
            new_params.append(func_param)

    # Substitute in return type
    new_return_type: Any = generic_func.return_type
    if generic_func.return_type:
        new_return_type = substitute_type(str(generic_func.return_type), substitutions)

    # Substitute in body
    new_body = [substitute_in_stmt(stmt, substitutions) for stmt in generic_func.body]

    return A.Function(
        name=mangled_name,
        params=new_params,
        return_type=new_return_type,
        body=new_body,
        is_public=True,
        decorators=generic_func.decorators,
        is_async=False,
        is_test=False,
    )


class Monomorphizer:
    """Manages generic type instantiation and caching."""

    def __init__(self) -> None:
        # Cache of already instantiated types: (base_name, type_args_tuple) -> mangled_name
        self.instantiated: dict[tuple[str, tuple[str, ...]], str] = {}

        # Generic definitions: base_name -> GenericRecord/GenericClass/GenericFunction
        self.generics: dict[str, A.ASTNode] = {}

        # Generated specialized definitions
        self.specialized: list[A.ASTNode] = []

    def register_generic(self, node: A.ASTNode) -> None:
        """Register a generic definition for later instantiation."""
        if isinstance(node, (A.GenericRecord, A.GenericClass, A.GenericFunction)):
            self.generics[node.name] = node

    def instantiate(self, base_name: str, type_args: list[str]) -> str:
        """Get or create a specialized version of a generic type.

        Returns the mangled name of the specialized type.
        """
        cache_key = (base_name, tuple(type_args))

        if cache_key in self.instantiated:
            return self.instantiated[cache_key]

        if base_name not in self.generics:
            raise MonomorphizationError(f"Unknown generic type: {base_name}")

        generic = self.generics[base_name]
        mangled_name = mangle_generic_name(base_name, type_args)

        specialized: A.ASTNode
        if isinstance(generic, A.GenericRecord):
            specialized = monomorphize_record(generic, type_args)
            self.specialized.append(specialized)
        elif isinstance(generic, A.GenericClass):
            specialized = monomorphize_class(generic, type_args)
            self.specialized.append(specialized)
        elif isinstance(generic, A.GenericFunction):
            # M16 fix: Actually instantiate generic functions
            specialized = monomorphize_generic_function(generic, type_args)
            self.specialized.append(specialized)

        self.instantiated[cache_key] = mangled_name
        return mangled_name

    def get_specialized_definitions(self) -> list[A.ASTNode]:
        """Get all generated specialized definitions."""
        return self.specialized

"""Named accessors for AST-style positional fields."""

from __future__ import annotations

from typing import Any


def arg_at(node: Any, index: int) -> Any:
    """Return the argument at `index` from an AST node with `.args`."""
    return node.args[index]


def body_at(node: Any, index: int) -> Any:
    """Return the body statement at `index` from an AST node with `.body`."""
    return node.body[index]


def param_at(node: Any, index: int) -> Any:
    """Return the parameter at `index` from an AST node with `.params`."""
    return node.params[index]


def target_at(node: Any, index: int) -> Any:
    """Return the assignment target at `index` from an AST node with `.targets`."""
    return node.targets[index]


def value_at(node: Any, index: int) -> Any:
    """Return the expression value at `index` from an AST node with `.values`."""
    return node.values[index]

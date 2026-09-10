"""Source-level visibility and metadata helpers for import lowering."""

from __future__ import annotations

from collections.abc import Iterator
from parser import ast as A
from typing import Any


def tag_source_file(nodes: list[A.ASTNode], filepath: str) -> None:
    """Attach source path metadata used by module-boundary checks."""
    if not filepath:
        return
    for node in nodes:
        if not node._source_file:
            node._source_file = filepath


def reject_private_selective_imports(
    nodes: list[A.ASTNode], requested: set[str], source_file: str
) -> None:
    """Reject explicit imports of functions declared private."""
    for node in nodes:
        if not isinstance(node, A.Function) or node.name not in requested:
            continue
        if not getattr(node, "is_public", True):
            raise ImportError(
                f"Cannot import private function '{node.name}' from '{source_file}'"
            )


def _walk_ast(value: Any) -> Iterator[A.ASTNode]:
    if isinstance(value, A.ASTNode):
        yield value
        for child in vars(value).values():
            yield from _walk_ast(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_ast(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_ast(child)


def validate_private_function_boundaries(nodes: list[A.ASTNode]) -> None:
    """Reject calls into a private function from a different source module."""
    private_functions = {
        node.name: node
        for node in nodes
        if isinstance(node, A.Function) and not getattr(node, "is_public", True)
    }
    if not private_functions:
        return
    for caller in nodes:
        if not isinstance(caller, A.Function):
            continue
        caller_source = getattr(caller, "_source_file", "")
        for child in _walk_ast(caller.body):
            if not isinstance(child, A.Call):
                continue
            target = private_functions.get(child.name)
            if target is None:
                continue
            target_source = getattr(target, "_source_file", "")
            if caller_source and target_source and caller_source != target_source:
                raise ValueError(
                    f"Private function '{child.name}' is not visible outside its module"
                )


def import_sort_key(node: A.ASTNode) -> int:
    """Order imported declarations before functions that reference them."""
    if isinstance(node, (A.RecordDef, A.EnumDef, A.ExternRecordDef)):
        return 0
    if isinstance(node, A.VarDecl):
        return 1
    if isinstance(node, A.ClassDef):
        return 2
    return 3

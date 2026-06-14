"""Structural copy-paste clone detection for verifier runs."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

from .common import read_file, validate_filepath

_CLONE_MIN_STATEMENTS = 8
_CLONE_MIN_CHARS = 320
_CLONE_MAX_ISSUES = 50


def _clone_location_sort_key(location: tuple[str, int]) -> tuple[int, str]:
    """Sort clone locations by source line, then body name."""
    body_name, line = location
    return (line, body_name)


def _project_location_sort_key(location: tuple[str, str, int]) -> tuple[str, int, str]:
    """Sort project clone locations by file, source line, then body name."""
    filename, body_name, line = location
    return (filename, line, body_name)


def _clone_cluster_key(
    locations: list[tuple[str, int]],
) -> tuple[str, ...]:
    """Collapse clone windows into one relationship per repeated body set."""
    return tuple(body_name for body_name, _line in locations)


def _project_clone_cluster_key(
    locations: list[tuple[str, str, int]],
) -> tuple[tuple[str, str], ...]:
    """Collapse cross-file clone windows into one relationship per body set."""
    return tuple((filename, body_name) for filename, body_name, _line in locations)


class _CloneNormalizer(ast.NodeTransformer):
    """Normalize syntax enough to catch copy-paste shape, not exact spelling."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="_name", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = "_arg"
        node.annotation = self.visit(node.annotation) if node.annotation else None
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if not isinstance(node.func, ast.Name):
            node.func = self.visit(node.func)
        node.args = [self.visit(arg) for arg in node.args]
        node.keywords = [self.visit(keyword) for keyword in node.keywords]
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value=node.value), node)
        marker = f"_{type(node.value).__name__}"
        return ast.copy_location(ast.Constant(value=marker), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        # Keep the attribute name: method/member names carry semantic weight.
        # Normalizing them made unrelated builder APIs look like copy-paste.
        self.generic_visit(node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.name = "_function"
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.name = "_function"
        return node


def _clone_statement_key(node: ast.stmt) -> str:
    cloned = copy.deepcopy(node)
    normalized = _CloneNormalizer().visit(cloned)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)


def _meaningful_clone_statements(body: list[ast.stmt]) -> list[ast.stmt]:
    meaningful: list[ast.stmt] = []
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Import, ast.ImportFrom)):
            continue
        if _is_simple_alias_assignment(stmt):
            continue
        if _is_literal_output_append(stmt):
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue
        meaningful.append(stmt)
    return meaningful


def _is_literal_output_append(stmt: ast.stmt) -> bool:
    """Ignore single-line generated-text emissions; they are data, not logic."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    if len(call.args) != 1 or call.keywords:
        return False
    (append_arg,) = call.args
    if not isinstance(append_arg, ast.Constant) or not isinstance(
        append_arg.value, str
    ):
        return False
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "append":
        return False
    if isinstance(func.value, ast.Name) and func.value.id in {"output", "out"}:
        return True
    if (
        isinstance(func.value, ast.Attribute)
        and func.value.attr == "_output"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
    ):
        return True
    return False


def _is_simple_alias_assignment(stmt: ast.stmt) -> bool:
    """Ignore assignment-only wiring blocks; they are declarations, not logic clones."""
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and isinstance(
            stmt.value, ast.Constant
        )
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return False
    (target,) = stmt.targets
    simple_target = isinstance(target, (ast.Name, ast.Attribute))
    simple_value = isinstance(stmt.value, (ast.Name, ast.Attribute, ast.Constant))
    return simple_target and simple_value


def _clone_bodies(tree: ast.AST) -> list[tuple[str, list[ast.stmt]]]:
    bodies: list[tuple[str, list[ast.stmt]]] = []
    if isinstance(tree, ast.Module):
        bodies.append(("<module>", tree.body))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bodies.append((node.name, node.body))
    return bodies


def _clone_windows(tree: ast.AST) -> dict[tuple[str, ...], list[tuple[str, int]]]:
    windows: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    for body_name, body in _clone_bodies(tree):
        statements = _meaningful_clone_statements(body)
        if len(statements) < _CLONE_MIN_STATEMENTS:
            continue
        keys = [_clone_statement_key(stmt) for stmt in statements]
        for start in range(0, len(keys) - _CLONE_MIN_STATEMENTS + 1):
            window = tuple(keys[start : start + _CLONE_MIN_STATEMENTS])
            if sum(len(part) for part in window) < _CLONE_MIN_CHARS:
                continue
            line = getattr(statements[start], "lineno", 0)
            windows.setdefault(window, []).append((body_name, line))
    return windows


def run_clone_check(filepath: str, actual_name: str) -> dict[str, Any]:
    """Detect high-confidence copy-paste clones inside a Python file."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}

    try:
        code = read_file(validated_path)
        tree = ast.parse(code)
    except (OSError, SyntaxError) as exc:
        return {"error": str(exc)}

    clone_clusters: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    for locations in _clone_windows(tree).values():
        unique = sorted(set(locations), key=_clone_location_sort_key)
        if len(unique) < 2:
            continue
        cluster_key = _clone_cluster_key(unique)
        if cluster_key in clone_clusters:
            continue
        clone_clusters[cluster_key] = unique

    issues: list[str] = []
    for unique in clone_clusters.values():
        first = ", ".join(f"{name}:{line}" for name, line in unique[:4])
        suffix = f" (+{len(unique) - 4} more)" if len(unique) > 4 else ""
        issues.append(
            f"{actual_name}: copy-paste clone window repeated at {first}{suffix}"
        )
        if len(issues) >= _CLONE_MAX_ISSUES:
            break

    return {
        "clone_count": len(issues),
        "issues": issues,
        "passed": True,
        "informational": True,
        "min_statements": _CLONE_MIN_STATEMENTS,
        "min_chars": _CLONE_MIN_CHARS,
    }


def run_project_clone_audit(filepaths: list[str], root: str) -> dict[str, Any]:
    """Detect high-confidence copy-paste clones across a verified directory."""
    root_path = Path(root).resolve()
    windows: dict[tuple[str, ...], list[tuple[str, str, int]]] = {}
    skipped: list[str] = []

    for filepath in filepaths:
        path = Path(filepath)
        try:
            display = str(path.resolve().relative_to(root_path))
        except ValueError:
            display = str(path)
        try:
            tree = ast.parse(read_file(str(path)))
        except (OSError, SyntaxError) as exc:
            skipped.append(f"{display}: {exc}")
            continue
        for window, body_locations in _clone_windows(tree).items():
            project_locations = [
                (display, body_name, line) for body_name, line in body_locations
            ]
            windows.setdefault(window, []).extend(project_locations)

    clone_clusters: dict[tuple[tuple[str, str], ...], list[tuple[str, str, int]]] = {}
    for locations in windows.values():
        unique = sorted(set(locations), key=_project_location_sort_key)
        distinct_files = {filename for filename, _body, _line in unique}
        if len(unique) < 2 or len(distinct_files) < 2:
            continue
        cluster_key = _project_clone_cluster_key(unique)
        if cluster_key in clone_clusters:
            continue
        clone_clusters[cluster_key] = unique

    issues: list[str] = []
    for unique in clone_clusters.values():
        first = ", ".join(
            f"{filename}:{body_name}:{line}" for filename, body_name, line in unique[:4]
        )
        suffix = f" (+{len(unique) - 4} more)" if len(unique) > 4 else ""
        issues.append(f"project clone window repeated at {first}{suffix}")
        if len(issues) >= _CLONE_MAX_ISSUES:
            break

    return {
        "clone_count": len(issues),
        "issues": issues,
        "skipped": skipped[:20],
        "passed": True,
        "informational": True,
        "min_statements": _CLONE_MIN_STATEMENTS,
        "min_chars": _CLONE_MIN_CHARS,
    }

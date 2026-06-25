"""Code quality tool runners: vulture, TODO checker, structural audits."""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from .common import read_file, validate_filepath


def run_todo_check(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Check for TODO, FIXME, HACK, XXX, and similar markers in code.

    These markers indicate incomplete implementations or technical debt
    that should be tracked and addressed.

    Returns:
        Dict with:
        - todo_count: Number of TODO markers found
        - fixme_count: Number of FIXME markers found
        - hack_count: Number of HACK markers found
        - total_count: Total markers found
        - issues: List of found markers with line numbers
        - passed: True if no markers found (informational, doesn't fail)
    """
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}

    try:
        code = read_file(validated_path)
    except (OSError, IOError) as exc:
        return {"error": str(exc)}

    lines = code.split("\n")
    issues: List[str] = []

    # Patterns to look for (case-insensitive)
    patterns = {
        "TODO": re.compile(r"#\s*TODO\b[:\s]*(.*)", re.IGNORECASE),
        "FIXME": re.compile(r"#\s*FIXME\b[:\s]*(.*)", re.IGNORECASE),
        "HACK": re.compile(r"#\s*HACK\b[:\s]*(.*)", re.IGNORECASE),
        "XXX": re.compile(r"#\s*XXX\b[:\s]*(.*)", re.IGNORECASE),
        "BUG": re.compile(r"#\s*BUG\b[:\s]*(.*)", re.IGNORECASE),
        "OPTIMIZE": re.compile(r"#\s*OPTIMIZE\b[:\s]*(.*)", re.IGNORECASE),
        "NOTE": re.compile(r"#\s*NOTE\b[:\s]*(.*)", re.IGNORECASE),
        "INCOMPLETE": re.compile(r"#\s*INCOMPLETE\b[:\s]*(.*)", re.IGNORECASE),
        "STUB": re.compile(r"#\s*STUB\b[:\s]*(.*)", re.IGNORECASE),
    }

    counts: Dict[str, int] = {key: 0 for key in patterns}

    for lineno, line in enumerate(lines, start=1):
        for marker, pattern in patterns.items():
            match = pattern.search(line)
            if match:
                counts[marker] += 1
                description = match.group(1).strip()
                if description:
                    issues.append(f"{actual_name}:{lineno}: [{marker}] {description}")
                else:
                    issues.append(
                        f"{actual_name}:{lineno}: [{marker}] (no description)"
                    )

    total = sum(counts.values())

    return {
        "todo_count": counts["TODO"],
        "fixme_count": counts["FIXME"],
        "hack_count": counts["HACK"],
        "note_count": counts["NOTE"],
        "total_count": total,
        "issues": issues[:100],  # Limit output
        "passed": True,  # Informational only - doesn't fail verification
        "counts_by_type": counts,
    }


def run_vulture(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Run vulture using its Python API."""
    try:
        vulture_mod = cast(Any, importlib.import_module("vulture"))
    except ImportError:
        return {"error": "vulture not installed"}
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    try:
        code = read_file(validated_path)
        vult = vulture_mod.Vulture()
        vult.scan(code, filename=validated_path)
        issues = []
        count = 0
        for item in vult.get_unused_code(min_confidence=80):
            count += 1
            issues.append(
                f"{actual_name}:{item.first_lineno}: unused {item.typ} '{item.name}'"
            )
        return {"dead_code_count": count, "issues": issues[:100], "passed": count == 0}
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


def run_vulture_project(
    filepath: str, _actual_name: str, related_files: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Run vulture across multiple related files to detect cross-module dead code.

    This is critical for detecting functions that exist but are never called
    from the main entry point (e.g., optimize_ir() in compiler.py).

    Args:
        filepath: Primary file being analyzed
        actual_name: Display name for the primary file
        related_files: Additional files to scan together (auto-detected if None)
    """
    try:
        vulture_mod = cast(Any, importlib.import_module("vulture"))
    except ImportError:
        return {"error": "vulture not installed"}

    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}

    try:
        vult = vulture_mod.Vulture()
        scanned_files: List[str] = []

        # Scan the primary file
        code = read_file(validated_path)
        vult.scan(code, filename=validated_path)
        scanned_files.append(validated_path)

        # Auto-detect related files if not provided
        if related_files is None:
            related_files = _find_related_python_files(validated_path)

        # Scan related files
        for rel_file in related_files:
            if rel_file == validated_path:
                continue
            try:
                rel_code = read_file(rel_file)
                vult.scan(rel_code, filename=rel_file)
                scanned_files.append(rel_file)
            except (OSError, IOError):
                continue  # Skip files we can't read

        # Collect issues - only report those from the primary file
        issues = []
        count = 0
        primary_path = Path(validated_path).resolve()

        for item in vult.get_unused_code(min_confidence=60):
            item_path = Path(item.filename).resolve()
            # Only report issues from the primary file or its directory
            if item_path == primary_path or item_path.parent == primary_path.parent:
                count += 1
                rel_name = item_path.name
                issues.append(
                    f"{rel_name}:{item.first_lineno}: unused {item.typ} '{item.name}' "
                    f"(cross-module check, {item.confidence}% confidence)"
                )

        return {
            "dead_code_count": count,
            "issues": issues[:100],
            "passed": count == 0,
            "files_scanned": len(scanned_files),
            "note": "Cross-module analysis enabled",
        }
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


def _find_related_python_files(filepath: str) -> List[str]:
    """Find Python files related to the given file.

    Looks in:
    1. Same directory
    2. Parent directory
    3. Subdirectories (packages)
    """
    related: List[str] = []
    file_path = Path(filepath).resolve()
    base_dir = file_path.parent

    # Check if we're in a package (has __init__.py)
    if (base_dir / "__init__.py").exists():
        # We're in a package - also scan parent and sibling packages
        package_root = base_dir.parent
        for py_file in package_root.glob("*.py"):
            related.append(str(py_file))
        for subdir in package_root.iterdir():
            if subdir.is_dir() and (subdir / "__init__.py").exists():
                for py_file in subdir.glob("*.py"):
                    related.append(str(py_file))
    else:
        # Not in a package - scan current directory and subdirs
        for py_file in base_dir.glob("*.py"):
            related.append(str(py_file))
        for subdir in base_dir.iterdir():
            if subdir.is_dir():
                for py_file in subdir.glob("*.py"):
                    related.append(str(py_file))

    return related


_STRUCTURED_POSITIONAL_NAMES = {
    "token",
    "tokens",
    "tok",
    "node",
    "item",
    "entry",
    "pair",
    "result",
    "match",
    "args",
    "params",
    "row",
    "record",
    "tuple",
    "coord",
    "point",
    "pos",
    "position",
    "loc",
    "location",
}

_STRUCTURED_POSITIONAL_ATTRS = _STRUCTURED_POSITIONAL_NAMES | {
    "args",
    "body",
    "comparators",
    "decorator_list",
    "elts",
    "fields",
    "finalbody",
    "handlers",
    "items",
    "keywords",
    "names",
    "ops",
    "orelse",
    "pairs",
    "targets",
    "values",
}

_INDEX_VAR_NAMES = {
    "i",
    "j",
    "k",
    "n",
    "idx",
    "index",
    "pos",
    "offset",
    "count",
    "next_pos",
    "prev_pos",
}


def _base_positional_name(name: str) -> str:
    """Normalize common plural structured names for positional checks."""
    return re.sub(r"s$", "", name.lower())


def _is_structured_positional_name(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in _STRUCTURED_POSITIONAL_NAMES
        or _base_positional_name(normalized) in _STRUCTURED_POSITIONAL_NAMES
    )


def _small_positional_constant(node: ast.Subscript) -> int | None:
    """Return a small non-negative literal index if this is positional access."""
    if isinstance(node.slice, ast.Slice):
        return None
    if not isinstance(node.slice, ast.Constant):
        return None
    if not isinstance(node.slice.value, int):
        return None
    if node.slice.value < 0 or node.slice.value > 2:
        return None
    return node.slice.value


def _source_or_fallback(code: str, node: ast.AST) -> str:
    """Return source text for a node when possible, else an AST fallback."""
    segment = ast.get_source_segment(code, node)
    if segment:
        return segment
    return ast.dump(node, include_attributes=False)


def run_magic_index_check(filepath: str, actual_name: str) -> Dict[str, Any]:
    """
    Detect magic indexing patterns that should use named fields/constants.

    Flags patterns like:
    - token[0], token[1] - should use named tuple or dataclass
    - args[0], args[1] - should use unpacking or named access
    - x[0] where x is clearly a tuple - should destructure

    Exceptions:
    - Loop iteration with index variable: items[i], arr[idx]
    - Slicing: x[0:5], x[1:]
    - Negative indexing for last element: x[-1]
    - Known safe patterns: range(len(x)), enumerate()
    """
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}

    try:
        code = read_file(validated_path)
        tree = ast.parse(code)
    except (OSError, SyntaxError) as exc:
        return {"error": str(exc)}

    issues: list[str] = []

    class MagicIndexVisitor(ast.NodeVisitor):
        """AST visitor to find magic indexing patterns."""

        def __init__(self) -> None:
            self.current_function: str = "<module>"
            self.loop_vars: set[str] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            old_func = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = old_func

        def visit_For(self, node: ast.For) -> None:
            # Track loop variables - these are safe index sources
            if isinstance(node.target, ast.Name):
                self.loop_vars.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        self.loop_vars.add(elt.id)
            self.generic_visit(node)
            # Clean up after loop
            if isinstance(node.target, ast.Name):
                self.loop_vars.discard(node.target.id)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            self.generic_visit(node)

            # Only check Name[something] patterns
            if not isinstance(node.value, ast.Name):
                return

            var_name = node.value.id.lower()

            # Skip if it's a slice (x[0:5])
            if isinstance(node.slice, ast.Slice):
                return

            idx_val = _small_positional_constant(node)
            if idx_val is not None and _is_structured_positional_name(var_name):
                issues.append(
                    f"{actual_name}:{node.lineno}: Magic index [{idx_val}] on "
                    f"'{node.value.id}' - consider using named tuple, "
                    f"dataclass, or destructuring"
                )

            # Check if index is a variable that's NOT a loop variable
            elif isinstance(node.slice, ast.Name):
                idx_name = node.slice.id.lower()
                # If it's a loop var or known index var, it's fine
                if idx_name in self.loop_vars or idx_name in _INDEX_VAR_NAMES:
                    return

    visitor = MagicIndexVisitor()
    visitor.visit(tree)

    return {
        "magic_index_count": len(issues),
        "issues": issues[:50],  # Limit output
        "passed": len(issues) == 0,
    }


def run_positional_access_audit(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Audit broad positional access on structured values.

    This intentionally reports more than the hard magic-index gate:
    - chained access like ``tokens[i][0]``
    - attribute access like ``func.args[0]`` or ``node.body[0]``

    It is informational for now because existing compiler code uses these
    idioms heavily. The audit gives concrete migration targets without making
    the default strict gate unusable before the refactor is scheduled.
    """
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}

    try:
        code = read_file(validated_path)
        tree = ast.parse(code)
    except (OSError, SyntaxError) as exc:
        return {"error": str(exc)}

    issues: list[str] = []
    seen: set[tuple[int, str]] = set()

    def add_issue(node: ast.Subscript, reason: str) -> None:
        expr = _source_or_fallback(code, node)
        key = (node.lineno, expr)
        if key in seen:
            return
        seen.add(key)
        issues.append(
            f"{actual_name}:{node.lineno}: positional access '{expr}' - {reason}"
        )

    def chain_root_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            return chain_root_name(node.value)
        return None

    class PositionalAccessVisitor(ast.NodeVisitor):
        """Find broad positional records without enforcing them as failures."""

        def visit_Subscript(self, node: ast.Subscript) -> None:
            self.generic_visit(node)
            idx_val = _small_positional_constant(node)
            if idx_val is None:
                return

            if isinstance(node.value, ast.Attribute):
                attr_name = node.value.attr.lower()
                if attr_name in _STRUCTURED_POSITIONAL_ATTRS:
                    add_issue(
                        node,
                        f"attribute '{node.value.attr}' is structured; use a named accessor",
                    )
                return

            if isinstance(node.value, ast.Subscript):
                root_name = chain_root_name(node.value)
                if root_name and _is_structured_positional_name(root_name):
                    add_issue(
                        node,
                        "chained indexing hides a structured positional record",
                    )
                return

            if isinstance(node.value, ast.Name) and _is_structured_positional_name(
                node.value.id
            ):
                add_issue(
                    node,
                    f"name '{node.value.id}' looks structured; use destructuring",
                )

    PositionalAccessVisitor().visit(tree)
    return {
        "positional_access_count": len(issues),
        "issues": issues[:100],
        "passed": True,
        "informational": True,
    }


def run_consistency_check(filepath: str, actual_name: str) -> Dict[str, Any]:
    """
    Check code consistency patterns.

    Detects:
    - Mixed naming conventions (snake_case vs camelCase)
    - Inconsistent comparison style (x == None vs x is None)
    - Inconsistent boolean checks (if x == True vs if x)
    """
    import ast

    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}

    try:
        code = read_file(validated_path)
        tree = ast.parse(code)
    except (OSError, SyntaxError) as exc:
        return {"error": str(exc)}

    issues: list[str] = []

    # Track naming styles
    snake_case_names: list[str] = []
    camel_case_names: list[str] = []

    # Track comparison issues
    none_equality: list[tuple[int, str]] = []  # x == None
    bool_equality: list[tuple[int, str]] = []  # x == True / x == False

    class ConsistencyVisitor(ast.NodeVisitor):
        """AST visitor for consistency checks."""

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            name = node.name
            if not name.startswith("_"):
                if "_" in name and name.islower():
                    snake_case_names.append(name)
                elif re.match(r"^[a-z]+[A-Z]", name):
                    camel_case_names.append(name)
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            name = node.id
            # Check variable names (not builtins, not CONSTANTS)
            if not name.startswith("_") and not name.isupper():
                if "_" in name and name.islower():
                    pass  # snake_case - don't track vars, too noisy
                elif re.match(r"^[a-z]+[A-Z]", name) and len(name) > 3:
                    camel_case_names.append(name)
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            # Check for x == None or x != None
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    if (
                        isinstance(comparator, ast.Constant)
                        and comparator.value is None
                    ):
                        none_equality.append(
                            (node.lineno, "==" if isinstance(op, ast.Eq) else "!=")
                        )
                    # Check for x == True / x == False
                    elif isinstance(comparator, ast.Constant) and isinstance(
                        comparator.value, bool
                    ):
                        bool_equality.append((node.lineno, f"== {comparator.value}"))
            self.generic_visit(node)

    visitor = ConsistencyVisitor()
    visitor.visit(tree)

    # Report naming inconsistency if both styles are used significantly
    if len(snake_case_names) > 2 and len(camel_case_names) > 2:
        issues.append(
            f"{actual_name}: Mixed naming conventions - "
            f"{len(snake_case_names)} snake_case, {len(camel_case_names)} camelCase. "
            f"Examples: {camel_case_names[:3]}"
        )

    # Report None comparison issues
    for lineno, op in none_equality:
        issues.append(
            f"{actual_name}:{lineno}: Use 'is None' or 'is not None' instead of '{op} None'"
        )

    # Report bool comparison issues
    for lineno, comparison in bool_equality:
        issues.append(
            f"{actual_name}:{lineno}: Redundant boolean comparison '{comparison}' - "
            f"use 'if x' or 'if not x' instead"
        )

    return {
        "consistency_issues": len(issues),
        "issues": issues[:30],
        "passed": len(issues) == 0,
    }

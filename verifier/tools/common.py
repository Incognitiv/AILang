"""Common utilities for tool runners."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def validate_filepath(filepath: str) -> Tuple[str, Optional[str]]:
    """Validate filepath for safe execution."""
    if not filepath:
        return "", "Empty filepath"
    try:
        path = Path(filepath).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return "", f"Invalid path: {exc}"
    if not path.is_file():
        return "", f"Not a file: {path}"
    if path.suffix.lower() != ".py":
        return "", f"Not a Python file: {path}"
    return str(path), None


def check_syntax(code: str) -> Dict[str, Any]:
    """Check Python syntax using ast module."""
    try:
        ast.parse(code)
        return {"valid": True, "errors": []}
    except SyntaxError as exc:
        return {"valid": False, "errors": [f"Line {exc.lineno}: {exc.msg}"]}


def read_file(path: str) -> str:
    """Read file content with UTF-8 encoding."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# Suppression patterns that indicate code quality shortcuts
SUPPRESSION_PATTERNS = [
    # Type checking suppressions
    (r"#\s*type:\s*ignore", "type: ignore"),
    # Pylint suppressions
    (r"#\s*pylint:\s*disable", "pylint: disable"),
    (r"#\s*pylint:\s*skip-file", "pylint: skip-file"),
    # Flake8/pycodestyle suppressions
    (r"#\s*noqa", "noqa"),
    # Mypy suppressions
    (r"#\s*mypy:\s*ignore", "mypy: ignore"),
    # Ruff suppressions
    (r"#\s*ruff:\s*noqa", "ruff: noqa"),
    # Bandit security suppressions
    (r"#\s*nosec", "nosec"),
    # Coverage suppressions
    (r"#\s*pragma:\s*no\s*cover", "pragma: no cover"),
    # Black formatting suppressions
    (r"#\s*fmt:\s*off", "fmt: off"),
    (r"#\s*fmt:\s*skip", "fmt: skip"),
    # isort suppressions
    (r"#\s*isort:\s*skip", "isort: skip"),
    (r"#\s*isort:\s*off", "isort: off"),
]


def detect_suppressions(code: str) -> Dict[str, Any]:
    """Detect suppression comments in code.

    Detects patterns like type-ignore, pylint-disable, noqa, nosec markers.

    Returns dict with:
      - total: total count of suppressions
      - by_type: dict mapping suppression type to list of line numbers
      - details: list of (line_num, line_text, suppression_type)
    """
    lines = code.split("\n")
    by_type: Dict[str, List[int]] = {}
    details: List[Tuple[int, str, str]] = []

    for line_num, line in enumerate(lines, 1):
        # Real suppression: there is non-whitespace, non-`#` CODE before the
        # suppression marker on the same line. Pure-comment lines that
        # mention suppression patterns (e.g. a docstring or developer
        # comment describing what the policy bans) are not suppressions
        # themselves -- they're documentation. Without this guard, the
        # detector flags its own policy text as if it were a violation.
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for pattern, suppression_name in SUPPRESSION_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                if suppression_name not in by_type:
                    by_type[suppression_name] = []
                by_type[suppression_name].append(line_num)
                details.append((line_num, line.strip(), suppression_name))
                break  # Only count each line once

    return {
        "total": len(details),
        "by_type": by_type,
        "details": details,
    }

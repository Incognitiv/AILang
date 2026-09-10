"""Tool runners package for Python code verification.

This package provides wrappers around various Python code analysis tools,
using their Python APIs directly instead of subprocess calls.
"""

from __future__ import annotations

from .clone import run_clone_check, run_project_clone_audit
from .common import check_syntax, detect_suppressions, validate_filepath
from .complexity import check_nesting_depth, run_cohesion, run_radon
from .formatters import run_black, run_isort
from .linters import run_bandit, run_mypy, run_pyflakes, run_pylint, run_ruff
from .quality import (
    run_consistency_check,
    run_magic_index_check,
    run_positional_access_audit,
    run_todo_check,
    run_vulture,
    run_vulture_project,
)
from .security import run_detect_secrets, run_pip_audit
from .strict_extras import run_strict_extras

__all__ = [
    "check_nesting_depth",
    "check_syntax",
    "detect_suppressions",
    "run_bandit",
    "run_black",
    "run_clone_check",
    "run_cohesion",
    "run_consistency_check",
    "run_detect_secrets",
    "run_isort",
    "run_magic_index_check",
    "run_mypy",
    "run_pip_audit",
    "run_positional_access_audit",
    "run_project_clone_audit",
    "run_pyflakes",
    "run_pylint",
    "run_radon",
    "run_ruff",
    "run_strict_extras",
    "run_todo_check",
    "run_vulture",
    "run_vulture_project",
    "validate_filepath",
]

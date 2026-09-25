"""Reject failed or malformed tool evidence before scoring verification."""

from __future__ import annotations

import math
from collections.abc import Iterable

# These are result-producing checks, not metadata or the per-file environment
# audit placeholder. Presence here does not change informational finding policy.
_TOOL_FIELDS: dict[str, tuple[str, ...]] = {
    "pyflakes": ("issues_count",),
    "strict_extras": ("issues_count",),
    "mypy": ("errors_count",),
    "bandit": ("high_severity", "medium_severity", "low_severity"),
    "radon": (),
    "black": (),
    "isort": (),
    "ruff": (),
    "vulture": (),
    "cohesion": (),
    "nesting": (),
    "clone": (),
    "magic_index": ("magic_index_count",),
    "positional_access": (),
    "consistency": (),
    "todo": (),
    "detect_secrets": (),
    "pylint": (),
}
_BOOLEAN_VERDICTS = frozenset({"black", "isort", "ruff", "vulture", "nesting"})


def perfect_score(value: object) -> bool:
    """Exactly 100; bool, strings, NaN, infinity and inflated scores are invalid."""
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and value == 100
    )


def _nonnegative_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _tool_problem(name: str, result: object) -> str | None:
    """Validate only fields consumed by scoring; findings keep their own policy."""
    if not isinstance(result, dict):
        return "result missing or not a dictionary"
    if result.get("error"):
        return f"tool error: {result['error']}"
    if "passed" in result and not isinstance(result["passed"], bool):
        return "passed must be a boolean"
    if name in _BOOLEAN_VERDICTS and "passed" not in result:
        return "missing passed verdict"
    for field in _TOOL_FIELDS.get(name, ()):
        value = result.get(field)
        if not isinstance(value, int) or not _nonnegative_number(value):
            return f"{field} must be a nonnegative integer"
    if name == "radon":
        value = result.get("max_complexity", result.get("avg_complexity"))
        if not _nonnegative_number(value):
            return "complexity must be a finite nonnegative number"
    if name == "detect_secrets" and not isinstance(result.get("issues", []), list):
        return "issues must be a list"
    return None


def tool_result_errors(results: dict, expected: Iterable[str] = ()) -> list[str]:
    """Missing requested checks and reported failures cannot produce a PASS."""
    names = set(expected) | (_TOOL_FIELDS.keys() & results.keys())
    errors: list[str] = []
    for name in sorted(names):
        problem = _tool_problem(name, results.get(name))
        if problem is not None:
            errors.append(f"{name}: {problem}")
    return errors

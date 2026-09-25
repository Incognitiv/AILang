"""Validate benchmark evidence independently of a successful process exit."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping


def measurement_errors(
    row: Mapping[str, object], run_count: int, *, check_output: bool
) -> list[str]:
    """Require actual finite samples, and a checksum when parity is requested."""
    errors: list[str] = []
    if row.get("status") != "ok":
        errors.append("measurement status is not ok")
    samples = row.get("runs_ms")
    if not isinstance(samples, list) or len(samples) != run_count:
        errors.append(f"expected {run_count} measured samples")
    elif any(not _valid_sample(value) for value in samples):
        errors.append("samples must be finite nonnegative numbers")
    checksum = row.get("checksum")
    if check_output and (not isinstance(checksum, int) or isinstance(checksum, bool)):
        errors.append("integer checksum is missing or invalid")
    return errors


def _valid_sample(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def matrix_errors(
    results: Mapping[str, Mapping[str, Mapping[str, object]]],
    cases: Iterable[str],
    implementations: Iterable[str],
    run_count: int,
    *,
    check_output: bool,
) -> list[str]:
    """Check the entire requested matrix, not merely the rows that appeared."""
    case_names = tuple(dict.fromkeys(cases))
    impl_names = tuple(dict.fromkeys(implementations))
    if not case_names or not impl_names or run_count < 1:
        return ["benchmark matrix must be nonempty and have positive run count"]
    errors: list[str] = []
    for case in case_names:
        rows = results.get(case, {})
        reference: int | None = None
        for implementation in impl_names:
            row = rows.get(implementation)
            label = f"{case}/{implementation}"
            if row is None:
                errors.append(f"{label}: requested measurement is missing")
                continue
            problems = measurement_errors(row, run_count, check_output=check_output)
            errors.extend(f"{label}: {problem}" for problem in problems)
            checksum = row.get("checksum")
            if check_output and not problems and isinstance(checksum, int):
                if reference is None:
                    reference = checksum
                elif checksum != reference:
                    errors.append(f"{label}: checksum mismatch")
    return errors

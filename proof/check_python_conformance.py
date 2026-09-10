#!/usr/bin/env python3
"""Exhaustively compare AILang's canonical Python semantics with Lean."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
PROOF = ROOT / "proof"

sys.path.insert(0, str(SOURCE))

from type_semantics import (  # noqa: E402
    INT_WIDTHS,
    classify_conversion,
    join_numeric_types,
)


def _numeric_types() -> list[str]:
    ints = [f"{sign}{width}" for sign in ("i", "u") for width in INT_WIDTHS]
    return ints + ["f32", "f64", "f128"]


def _scalar_types() -> list[str]:
    return ["bool", *_numeric_types()]


def _lean_rows() -> tuple[
    dict[tuple[str, str], str | None], dict[tuple[str, str], str]
]:
    proc = subprocess.run(
        ["lake", "exe", "ailangProofConformance"],
        cwd=PROOF,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, file=sys.stderr, end="")
        if proc.stderr:
            print(proc.stderr, file=sys.stderr, end="")
        raise SystemExit(f"Lean conformance executable failed with exit {proc.returncode}")

    joins: dict[tuple[str, str], str | None] = {}
    conversions: dict[tuple[str, str], str] = {}
    for line_no, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            raise SystemExit(f"unexpected Lean output at line {line_no}: {line!r}")
        kind, left, right, result = parts
        key = (left, right)
        if kind == "J":
            if key in joins:
                raise SystemExit(f"duplicate Lean join row: {left}, {right}")
            joins[key] = None if result == "none" else result
        elif kind == "C":
            if key in conversions:
                raise SystemExit(f"duplicate Lean conversion row: {left}, {right}")
            conversions[key] = result
        else:
            raise SystemExit(f"unknown Lean row kind at line {line_no}: {kind!r}")
    return joins, conversions


def _check_domain(
    label: str,
    actual: set[tuple[str, str]],
    expected: set[tuple[str, str]],
) -> bool:
    if actual == expected:
        return True
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    print(
        f"Lean/Python {label} domain mismatch: missing={len(missing)} extra={len(extra)}",
        file=sys.stderr,
    )
    for key in missing[:10]:
        print(f"  missing: {key[0]} -> {key[1]}", file=sys.stderr)
    for key in extra[:10]:
        print(f"  extra: {key[0]} -> {key[1]}", file=sys.stderr)
    return False


def main() -> int:
    numeric_types = _numeric_types()
    scalar_types = _scalar_types()
    join_keys = {(left, right) for left in numeric_types for right in numeric_types}
    conversion_keys = {
        (source, target) for source in scalar_types for target in scalar_types
    }
    lean_joins, lean_conversions = _lean_rows()

    if not _check_domain("join", set(lean_joins), join_keys):
        return 1
    if not _check_domain("conversion", set(lean_conversions), conversion_keys):
        return 1

    join_mismatches: list[tuple[str, str, str | None, str | None]] = []
    for left, right in sorted(join_keys):
        python_result = join_numeric_types(left, right)
        lean_result = lean_joins[(left, right)]
        if python_result != lean_result:
            join_mismatches.append((left, right, python_result, lean_result))

    if join_mismatches:
        print(
            f"Lean/Python fixed-numeric join mismatches: {len(join_mismatches)}",
            file=sys.stderr,
        )
        for left, right, python_result, lean_result in join_mismatches[:20]:
            print(
                f"  {left} + {right}: Python={python_result!r}, Lean={lean_result!r}",
                file=sys.stderr,
            )
        return 1

    conversion_mismatches: list[tuple[str, str, str, str]] = []
    for source, target in sorted(conversion_keys):
        python_result = classify_conversion(source, target).value
        lean_result = lean_conversions[(source, target)]
        if python_result != lean_result:
            conversion_mismatches.append((source, target, python_result, lean_result))

    if conversion_mismatches:
        print(
            f"Lean/Python conversion-classifier mismatches: {len(conversion_mismatches)}",
            file=sys.stderr,
        )
        for source, target, python_result, lean_result in conversion_mismatches[:20]:
            print(
                f"  {source} -> {target}: Python={python_result!r}, Lean={lean_result!r}",
                file=sys.stderr,
            )
        return 1

    print(
        "Lean/Python fixed-numeric join conformance: "
        f"{len(join_keys)}/{len(join_keys)} pairs matched"
    )
    print(
        "Lean/Python conversion conformance: "
        f"{len(conversion_keys)}/{len(conversion_keys)} pairs matched"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Exhaustively compare AILang's fixed-int join with the Lean model."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
PROOF = ROOT / "proof"

sys.path.insert(0, str(SOURCE))

from parser.return_type_inference import _INT_WIDTHS, _join_ints  # noqa: E402


def _ailang_types() -> list[str]:
    return [f"{sign}{width}" for sign in ("i", "u") for width in _INT_WIDTHS]


def _lean_rows() -> dict[tuple[str, str], str | None]:
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

    rows: dict[tuple[str, str], str | None] = {}
    for line_no, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise SystemExit(f"unexpected Lean output at line {line_no}: {line!r}")
        left, right, result = parts
        key = (left, right)
        if key in rows:
            raise SystemExit(f"duplicate Lean conformance row: {left}, {right}")
        rows[key] = None if result == "none" else result
    return rows


def main() -> int:
    types = _ailang_types()
    expected_keys = {(left, right) for left in types for right in types}
    lean = _lean_rows()

    lean_keys = set(lean)
    if lean_keys != expected_keys:
        missing = sorted(expected_keys - lean_keys)
        extra = sorted(lean_keys - expected_keys)
        print(
            f"Lean/Python domain mismatch: missing={len(missing)} extra={len(extra)}",
            file=sys.stderr,
        )
        for key in missing[:10]:
            print(f"  missing: {key[0]} + {key[1]}", file=sys.stderr)
        for key in extra[:10]:
            print(f"  extra: {key[0]} + {key[1]}", file=sys.stderr)
        return 1

    mismatches: list[tuple[str, str, str | None, str | None]] = []
    for left, right in sorted(expected_keys):
        python_result = _join_ints(left, right)
        lean_result = lean[(left, right)]
        if python_result != lean_result:
            mismatches.append((left, right, python_result, lean_result))

    if mismatches:
        print(f"Lean/Python fixed-int mismatches: {len(mismatches)}", file=sys.stderr)
        for left, right, python_result, lean_result in mismatches[:20]:
            print(
                f"  {left} + {right}: Python={python_result!r}, Lean={lean_result!r}",
                file=sys.stderr,
            )
        return 1

    print(
        "Lean/Python fixed-int conformance: "
        f"{len(expected_keys)}/{len(expected_keys)} pairs matched"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

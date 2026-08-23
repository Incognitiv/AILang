#!/usr/bin/env python3
"""
Fast iteration check — ruff (lint+format+isort+pyflakes-equivalent) plus
mypy with incremental cache. Sub-second on warm cache.

For full strict verification (pylint, bandit, radon, vulture, etc.), run:
    python verifier/cli.py -d source --preset strict

Use this script for the inner edit-verify loop. Use the full verifier for
"is this ready to commit" checks.

Usage:
    python tools/quickcheck.py            # check source/ + ailang.py
    python tools/quickcheck.py --fix      # apply ruff auto-fixes
    python tools/quickcheck.py path/...   # check specific paths
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = [str(ROOT / "source"), str(ROOT / "ailang.py")]


def run(cmd: list[str], label: str) -> tuple[int, float, str]:
    """Run a subprocess and return (exit_code, elapsed_seconds, stderr_snippet)."""
    start = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        elapsed = time.perf_counter() - start
        return 127, elapsed, f"{label}: tool not found ({exc.filename})"
    elapsed = time.perf_counter() - start
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, elapsed, output.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip())
    parser.add_argument(
        "targets",
        nargs="*",
        default=DEFAULT_TARGETS,
        help="paths to check (default: source/ and ailang.py)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="apply ruff auto-fixes (lint + format)",
    )
    parser.add_argument(
        "--no-mypy",
        action="store_true",
        help="skip mypy (just ruff)",
    )
    args = parser.parse_args()

    overall_ok = True
    total_start = time.perf_counter()

    # 1. Ruff lint (covers pyflakes + isort + many pylint rules in milliseconds).
    if args.fix:
        rc, t, out = run(
            [sys.executable, "-m", "ruff", "check", "--fix", *args.targets],
            "ruff --fix",
        )
    else:
        rc, t, out = run(
            [sys.executable, "-m", "ruff", "check", *args.targets],
            "ruff check",
        )
    print(f"[ruff]    {t * 1000:6.0f} ms   exit {rc}")
    if rc != 0:
        if out:
            print(out)
        overall_ok = False

    # 2. Mypy with incremental cache.
    # --ignore-missing-imports keeps third-party stub-less libs (llvmlite) quiet.
    if not args.no_mypy:
        rc, t, out = run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--ignore-missing-imports",
                *args.targets,
            ],
            "mypy",
        )
        print(f"[mypy]    {t * 1000:6.0f} ms   exit {rc}")
        if rc != 0:
            if out:
                # Trim to errors only (skip "annotation-unchecked" notes which
                # are informational, not failures).
                lines = [
                    line
                    for line in out.split("\n")
                    if " error:" in line or "Found " in line
                ]
                if lines:
                    print("\n".join(lines[:20]))
            overall_ok = False

    total_elapsed = time.perf_counter() - total_start
    status = "OK" if overall_ok else "FAIL"
    print(f"\n{status}  total {total_elapsed:.2f}s")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())

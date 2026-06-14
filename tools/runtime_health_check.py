#!/usr/bin/env python3
"""Runtime-health gate for the AILang syntax/runtime surface.

This orchestrates the checks that together provide a defensible "runtime is
healthy for the curated surface" signal:

1. Every lexer keyword spelling parses in a minimal program.
2. Runtime-bearing surface programs agree across C and LLVM.
3. Generated C for those programs is warning-clean under strict C23 flags.
4. The C backend reports zero live bytes at exit for those programs.
5. Optionally, WSL/Linux ASAN+UBSAN and Valgrind pass the same surface suite.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    required: bool = True


@dataclass(frozen=True)
class StepResult:
    name: str
    returncode: int
    required: bool


def _run_step(step: Step) -> StepResult:
    print(f"\n== {step.name} ==", flush=True)
    print(" ".join(step.command), flush=True)
    proc = subprocess.run(
        step.command,
        cwd=REPO_ROOT,
        text=True,
        check=False,
    )
    return StepResult(step.name, int(proc.returncode), step.required)


def _build_steps(args: argparse.Namespace) -> list[Step]:
    py = sys.executable
    steps = [
        Step(
            "keyword parser surface",
            [
                py,
                "tools/syntax_surface_audit.py",
                "--check-keywords",
                "--fail-on-keyword-fail",
            ],
        ),
        Step(
            "runtime surface C/LLVM differential",
            [
                py,
                "tools/backend_differential.py",
                "--no-corpus",
                "--generated",
                "0",
                "--surface-runtime",
            ],
        ),
        Step(
            "runtime surface strict generated C",
            [
                py,
                "tools/c_strict_compile.py",
                "--no-corpus",
                "--surface-runtime",
            ],
        ),
    ]
    if args.memory:
        memory_prefix = [py]
        if args.wsl:
            sanitizer_cmd = [
                py,
                "tools/sanitizer_smoke.py",
                "--wsl",
                "--no-corpus",
                "--surface-runtime",
            ]
            valgrind_cmd = [
                py,
                "tools/valgrind_smoke.py",
                "--wsl",
                "--no-corpus",
                "--surface-runtime",
            ]
        else:
            sanitizer_cmd = [
                *memory_prefix,
                "tools/sanitizer_smoke.py",
                "--no-corpus",
                "--surface-runtime",
            ]
            valgrind_cmd = [
                *memory_prefix,
                "tools/valgrind_smoke.py",
                "--no-corpus",
                "--surface-runtime",
            ]
        steps.extend(
            [
                Step("runtime surface ASAN/UBSAN", sanitizer_cmd),
                Step("runtime surface Valgrind", valgrind_cmd),
            ]
        )
    return steps


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--memory",
        action="store_true",
        help="Also run sanitizer and Valgrind surface checks",
    )
    parser.add_argument(
        "--wsl",
        action="store_true",
        help="Run memory checks through WSL from Windows",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    results = [_run_step(step) for step in _build_steps(args)]
    failures = [row for row in results if row.returncode != 0 and row.required]
    print("\nRuntime health summary:")
    for row in results:
        status = "pass" if row.returncode == 0 else f"fail({row.returncode})"
        print(f"- {row.name}: {status}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

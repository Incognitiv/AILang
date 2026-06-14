#!/usr/bin/env python3
"""Deterministic parser fuzz/property smoke for generated valid programs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser

from lexer.scan import tokenize
from validation_programs import generated_cases


def _parse_source(source: str) -> None:
    tokens = tokenize(source)
    nodes = Parser(tokens).parse_program()
    if not nodes:
        raise AssertionError("parser returned an empty AST")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=166)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional directory to save generated programs for triage.",
    )
    args = parser.parse_args()

    failures: list[str] = []
    for case in generated_cases(args.count, args.seed):
        try:
            _parse_source(case.source)
        except Exception as exc:  # noqa: BLE001 - fuzz runner must capture all cases.
            failures.append(f"{case.name}: {type(exc).__name__}: {exc}")
        if args.out_dir is not None:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            (args.out_dir / f"{case.name}.ail").write_text(
                case.source,
                encoding="utf-8",
            )

    print(f"parser fuzz seed={args.seed} count={args.count}")
    if failures:
        print(f"failures={len(failures)}")
        for failure in failures[:20]:
            print(f"- {failure}")
        return 1
    print("failures=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

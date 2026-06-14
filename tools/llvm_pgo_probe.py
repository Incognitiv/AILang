#!/usr/bin/env python3
"""Probe hosted LLVM IR PGO support for the current toolchain."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ruff: noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pgo.llvm_ir import llvm_pgo_probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--work-dir", type=Path, default=None, help="keep probe files here"
    )
    parser.add_argument(
        "--target",
        default=None,
        help="override clang target triple; default matches AILang LLVM AOT",
    )
    args = parser.parse_args()

    result = llvm_pgo_probe(args.work_dir, target=args.target)
    if args.json:
        print(result.to_json())
    else:
        status = "available" if result.ok else "unavailable"
        print(f"LLVM IR PGO: {status}")
        print(f"platform: {result.platform}")
        print(f"clang: {result.clang or 'missing'}")
        print(f"llvm-profdata: {result.llvm_profdata or 'missing'}")
        print(f"target: {result.target or 'native'}")
        print(f"work_dir: {result.work_dir}")
        if result.profraw_count:
            print(f"profraw files: {result.profraw_count}")
        if result.profdata:
            print(f"profdata: {result.profdata}")
        if result.error:
            print(f"error: {result.error}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

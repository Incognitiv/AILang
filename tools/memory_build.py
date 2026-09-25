#!/usr/bin/env python3
"""Opt-in Linux AILang build: LLVM IR, object, and linked image stay in RAM."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--opt", type=int, choices=range(4), default=3)
    parser.add_argument("--linker", default="clang")
    parser.add_argument("--dump-ir-on-error", type=Path)
    args = parser.parse_args()

    from cli.link_flags import _extract_ailang_link_flags, _merge_link_flags
    from cli.llvm_diagnostics import _detect_llvm_link_flags
    from codegen.fast_jit import compile_to_ir_fast
    from compiler.memory_link import (
        link_object_in_memory,
        memory_link_supported,
        publish_executable,
        validate_output_paths,
    )
    from compiler.memory_native import compile_ir_object

    ir_code: str | None = None
    try:
        if not memory_link_supported():
            raise RuntimeError(
                "this memory-build adapter requires Linux; no disk fallback"
            )
        validate_output_paths(args.source, args.output, args.dump_ir_on_error)
        source_path = args.source.resolve()
        source = source_path.read_text(encoding="utf-8")
        ir_code = compile_to_ir_fast(source, source_file=str(source_path), debug=False)
        flags = _merge_link_flags(
            _extract_ailang_link_flags(source),
            _extract_ailang_link_flags(ir_code),
            ["-lm"],
            _detect_llvm_link_flags(ir_code),
        )
        object_code = compile_ir_object(ir_code, opt_level=args.opt)
        image = link_object_in_memory(object_code, args.linker, tuple(flags))
        publish_executable(image, args.output)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"memory build failed: {exc}", file=sys.stderr)
        if args.dump_ir_on_error is not None and ir_code is not None:
            try:
                args.dump_ir_on_error.write_text(ir_code, encoding="utf-8")
            except OSError as dump_error:
                print(
                    f"IR diagnostic could not be saved: {dump_error}", file=sys.stderr
                )
        return 1
    print(f"Built {args.output} ({len(image)} bytes); intermediates were memory-backed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Strict compile gate for C-backend generated C.

This check is intentionally compile-only. It proves the emitted C translation
unit is warning-clean under a strict C23 compiler policy before linking or
runtime behavior can hide generator-quality issues.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cli.cinclude_diagnostics import collect_cinclude_include_dirs
from runtime.modes import CompilationContext, CompilationMode
from transpiler.core import transpile_file
from validation_programs import generated_cases, materialize_case, runtime_surface_cases

CORPUS_DIR = REPO_ROOT / "tests" / "corpus"
DEFAULT_PROGRAMS = ["01_hello", "02_factorial", "03_fibonacci", "04_string_concat"]


@dataclass(frozen=True)
class CompileResult:
    name: str
    status: str
    detail: str


def _resolve_compiler(preferred: str) -> str | None:
    if preferred != "auto":
        return shutil.which(preferred) or (
            preferred if Path(preferred).exists() else None
        )
    return shutil.which("clang") or shutil.which("gcc")


def _run(cmd: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _strict_flags(std: str, *, freestanding: bool = False) -> list[str]:
    flags = [f"-std={std}", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O2"]
    if freestanding:
        flags.extend(["-ffreestanding", "-DAILANG_FREESTANDING"])
    return flags


def _compile_generated_c(
    source_file: Path,
    tmp: Path,
    compiler: str,
    strict_flags: list[str],
    *,
    freestanding: bool = False,
) -> CompileResult:
    c_file = tmp / f"{source_file.stem}.c"
    obj_file = tmp / f"{source_file.stem}.o"
    previous_mode = CompilationContext.get_mode()
    previous_is_jit = CompilationContext.is_jit()
    try:
        CompilationContext.set_mode(
            CompilationMode.FREESTANDING if freestanding else CompilationMode.HOSTED
        )
        CompilationContext.set_jit(False)
        transpile_file(str(source_file), str(c_file))
    except Exception as exc:  # noqa: BLE001 - validation reports compiler exceptions.
        return CompileResult(source_file.stem, "fail", f"transpile failed: {exc}")
    finally:
        CompilationContext.set_mode(previous_mode)
        CompilationContext.set_jit(previous_is_jit)

    include_dirs = collect_cinclude_include_dirs(str(source_file))
    cmd = [
        compiler,
        *strict_flags,
        *(f"-I{include_dir}" for include_dir in include_dirs),
        "-c",
        str(c_file),
        "-o",
        str(obj_file),
    ]
    proc = _run(cmd, timeout=240)
    if proc.returncode != 0:
        return CompileResult(
            source_file.stem,
            "fail",
            (proc.stdout + proc.stderr)[-1800:],
        )
    return CompileResult(source_file.stem, "pass", "warning-clean")


def _runtime_surface_sources(tmp: Path) -> list[CompileResult | Path]:
    sources: list[CompileResult | Path] = []
    for case in runtime_surface_cases():
        try:
            sources.append(materialize_case(case, tmp))
        except Exception as exc:  # noqa: BLE001 - validation reports all cases.
            sources.append(CompileResult(case.name, "fail", str(exc)[-1200:]))
    return sources


def _generated_sources(
    count: int,
    seed: int,
    tmp: Path,
    compiler: str,
    strict_flags: list[str],
) -> list[CompileResult | Path]:
    sources: list[CompileResult | Path] = []
    for case in generated_cases(count, seed):
        try:
            sources.append(
                materialize_case(
                    case,
                    tmp,
                    helper_compiler=compiler,
                    helper_flags=tuple(strict_flags),
                )
            )
        except Exception as exc:  # noqa: BLE001 - validation reports all cases.
            sources.append(CompileResult(case.name, "fail", str(exc)[-1200:]))
    return sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="auto", help="auto, clang, gcc, or path")
    parser.add_argument("--std", default="c23", help="C standard, default: c23")
    parser.add_argument(
        "--freestanding",
        action="store_true",
        help="Compile generated C as freestanding object code.",
    )
    parser.add_argument("--program", action="append", default=[])
    parser.add_argument("--no-corpus", action="store_true")
    parser.add_argument("--generated", type=int, default=0)
    parser.add_argument("--seed", type=int, default=166)
    parser.add_argument(
        "--surface-runtime",
        action="store_true",
        help="Include curated runtime-bearing syntax surface cases",
    )
    args = parser.parse_args()

    compiler = _resolve_compiler(args.compiler)
    if compiler is None:
        print("strict C compile: no C compiler found")
        return 2

    strict_flags = _strict_flags(args.std, freestanding=args.freestanding)
    names = [] if args.no_corpus else (args.program or DEFAULT_PROGRAMS)
    with tempfile.TemporaryDirectory(
        prefix="ailang_strict_c_", dir=REPO_ROOT / "out"
    ) as td:
        tmp = Path(td)
        source_items: list[CompileResult | Path] = [
            CORPUS_DIR / f"{name}.ail" for name in names
        ]
        if args.surface_runtime:
            source_items.extend(_runtime_surface_sources(tmp))
        if args.generated:
            source_items.extend(
                _generated_sources(
                    args.generated, args.seed, tmp, compiler, strict_flags
                )
            )

        results: list[CompileResult] = []
        for item in source_items:
            if isinstance(item, CompileResult):
                results.append(item)
            else:
                results.append(
                    _compile_generated_c(
                        item,
                        tmp,
                        compiler,
                        strict_flags,
                        freestanding=args.freestanding,
                    )
                )

    failures = [row for row in results if row.status == "fail"]
    passes = [row for row in results if row.status == "pass"]
    print(
        f"strict C compile compiler={compiler} std={args.std} "
        f"freestanding={args.freestanding} "
        f"pass={len(passes)} fail={len(failures)}"
    )
    for row in results:
        print(f"- {row.name}: {row.status} {row.detail[:260]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

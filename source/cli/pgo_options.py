"""CLI helpers for PGO options and probes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pgo.llvm_ir import llvm_pgo_probe


@dataclass(frozen=True)
class PgoCliOptions:
    """Parsed PGO CLI options."""

    c_generate_dir: str = ""
    c_use_dir: str = ""
    llvm_generate_dir: str = ""
    llvm_use_dir: str = ""


def wants_llvm_pgo_probe(argv: list[str]) -> bool:
    """Return true when the process should run only the LLVM PGO probe."""
    return "--llvm-pgo-probe" in argv or "--llvm-pgo-probe-json" in argv


def run_llvm_pgo_probe_cli(argv: list[str]) -> int:
    """Run the hosted LLVM IR PGO probe and print text or JSON output."""
    result = llvm_pgo_probe()
    if "--llvm-pgo-probe-json" in argv:
        print(result.to_json())
    else:
        status = "available" if result.ok else "unavailable"
        print(f"LLVM IR PGO: {status}")
        print(f"platform: {result.platform}")
        print(f"clang: {result.clang or 'missing'}")
        print(f"llvm-profdata: {result.llvm_profdata or 'missing'}")
        print(f"target: {result.target or 'native'}")
        if result.profraw_count:
            print(f"profraw files: {result.profraw_count}")
        if result.profdata:
            print(f"profdata: {result.profdata}")
        if result.error:
            print(f"error: {result.error}")
    return 0 if result.ok else 1


def parse_pgo_cli_options(
    argv: list[str],
    *,
    source_file: str,
    default_dir: Callable[[str], Path],
) -> PgoCliOptions:
    """Parse C and hosted LLVM PGO flags."""
    c_generate_dir = ""
    c_use_dir = ""
    llvm_generate_dir = ""
    llvm_use_dir = ""
    for arg in argv:
        if arg == "--pgo-generate":
            c_generate_dir = str(default_dir(source_file))
        elif arg.startswith("--pgo-generate="):
            c_generate_dir = arg.split("=", 1)[1]
        elif arg == "--pgo-use":
            c_use_dir = str(default_dir(source_file))
        elif arg.startswith("--pgo-use="):
            c_use_dir = arg.split("=", 1)[1]
        elif arg == "--llvm-pgo-generate":
            llvm_generate_dir = str(default_dir(source_file))
        elif arg.startswith("--llvm-pgo-generate="):
            llvm_generate_dir = arg.split("=", 1)[1]
        elif arg == "--llvm-pgo-use":
            llvm_use_dir = str(default_dir(source_file))
        elif arg.startswith("--llvm-pgo-use="):
            llvm_use_dir = arg.split("=", 1)[1]
    return PgoCliOptions(
        c_generate_dir=c_generate_dir,
        c_use_dir=c_use_dir,
        llvm_generate_dir=llvm_generate_dir,
        llvm_use_dir=llvm_use_dir,
    )


def validate_pgo_cli_options(options: PgoCliOptions, *, use_c_backend: bool) -> str:
    """Return an error message for invalid PGO option combinations."""
    if options.c_generate_dir and options.c_use_dir:
        return "--pgo-generate and --pgo-use are mutually exclusive"
    if options.llvm_generate_dir and options.llvm_use_dir:
        return "--llvm-pgo-generate and --llvm-pgo-use are mutually exclusive"
    if use_c_backend and (options.llvm_generate_dir or options.llvm_use_dir):
        return "--llvm-pgo-* requires --backend=llvm"
    return ""

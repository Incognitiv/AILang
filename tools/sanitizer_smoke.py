#!/usr/bin/env python3
"""ASAN/UBSAN smoke suite for C-backend generated programs."""

from __future__ import annotations

import argparse
import os
import shlex
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
from cli.compilation import _extract_ailang_link_flags, _merge_link_flags
from transpiler.core import transpile_file
from validation_programs import generated_cases, materialize_case, runtime_surface_cases

CORPUS_DIR = REPO_ROOT / "tests" / "corpus"
DEFAULT_PROGRAMS = ["01_hello", "02_factorial", "03_fibonacci", "04_string_concat"]


@dataclass
class SmokeResult:
    name: str
    status: str
    detail: str


def _resolve_compiler(preferred: str) -> str | None:
    if preferred != "auto":
        return shutil.which(preferred)
    return shutil.which("clang") or shutil.which("gcc")


def _link_flags(source_file: Path, c_text: str) -> list[str]:
    explicit = _extract_ailang_link_flags(c_text)
    auto: list[str] = []
    if "sqlite3.h" in c_text or "sqlite3_open" in c_text:
        auto.append("-lsqlite3")
    if "pthread.h" in c_text and not sys.platform.startswith("win"):
        auto.append("-lpthread")
    if "winsock2.h" in c_text and sys.platform.startswith("win"):
        auto.append("-lws2_32")
    return _merge_link_flags(explicit, ["-lm"], auto)


def _run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 180):
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _run_wsl(args: argparse.Namespace) -> int:
    if shutil.which("wsl.exe") is None and shutil.which("wsl") is None:
        print("sanitizer smoke: wsl not found")
        return 2
    path_proc = subprocess.run(
        ["wsl", "wslpath", "-a", REPO_ROOT.as_posix()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if path_proc.returncode != 0:
        print(path_proc.stderr.strip() or "sanitizer smoke: wslpath failed")
        return 2
    repo_wsl = path_proc.stdout.strip()
    forwarded = ["python3", "tools/sanitizer_smoke.py"]
    if args.compiler != "auto":
        forwarded.extend(["--compiler", args.compiler])
    for name in args.program:
        forwarded.extend(["--program", name])
    if args.no_corpus:
        forwarded.append("--no-corpus")
    if args.surface_runtime:
        forwarded.append("--surface-runtime")
    if args.generated:
        forwarded.extend(["--generated", str(args.generated)])
    forwarded.extend(["--seed", str(args.seed)])
    command = (
        "cd "
        + shlex.quote(repo_wsl)
        + " && "
        + " ".join(shlex.quote(part) for part in forwarded)
    )
    proc = subprocess.run(["wsl", "bash", "-lc", command], check=False)
    return int(proc.returncode)


def _compile_and_run(source_file: Path, tmp: Path, compiler: str) -> SmokeResult:
    c_file = tmp / f"{source_file.stem}.c"
    exe = tmp / (f"{source_file.stem}.exe" if os.name == "nt" else source_file.stem)
    try:
        c_text = transpile_file(str(source_file), str(c_file))
    except Exception as exc:  # noqa: BLE001 - smoke reports compiler exceptions.
        return SmokeResult(source_file.stem, "fail", f"transpile failed: {exc}")

    include_dirs = collect_cinclude_include_dirs(str(source_file))
    cmd = [
        compiler,
        "-std=gnu23",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
        *(f"-I{include_dir}" for include_dir in include_dirs),
        str(c_file),
        "-o",
        str(exe),
        *_link_flags(source_file, c_text),
    ]
    compile_proc = _run(cmd, timeout=240)
    if compile_proc.returncode != 0:
        detail = (compile_proc.stdout + compile_proc.stderr)[-1400:]
        return SmokeResult(
            source_file.stem, "skip", f"sanitizer compile failed: {detail}"
        )

    env = dict(os.environ)
    env.update(
        {
            "AILANG_LEAK_REPORT": "1",
            "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
            "UBSAN_OPTIONS": "halt_on_error=1",
        }
    )
    run_proc = _run([str(exe)], env=env, timeout=180)
    combined = run_proc.stdout + "\n" + run_proc.stderr
    if run_proc.returncode != 0:
        return SmokeResult(source_file.stem, "fail", combined[-1400:])
    if "ERROR: AddressSanitizer" in combined or "runtime error:" in combined:
        return SmokeResult(source_file.stem, "fail", combined[-1400:])
    return SmokeResult(source_file.stem, "pass", "clean")


def _generated_sources(
    count: int,
    seed: int,
    tmp: Path,
    compiler: str,
) -> list[SmokeResult | Path]:
    sources: list[SmokeResult | Path] = []
    helper_flags = (
        "-std=gnu23",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
    )
    for case in generated_cases(count, seed):
        try:
            sources.append(
                materialize_case(
                    case,
                    tmp,
                    helper_compiler=compiler,
                    helper_flags=helper_flags,
                )
            )
        except Exception as exc:  # noqa: BLE001 - smoke reports all cases.
            sources.append(SmokeResult(case.name, "fail", str(exc)[-1200:]))
    return sources


def _runtime_surface_sources(tmp: Path) -> list[SmokeResult | Path]:
    sources: list[SmokeResult | Path] = []
    for case in runtime_surface_cases():
        try:
            sources.append(materialize_case(case, tmp))
        except Exception as exc:  # noqa: BLE001 - smoke reports all cases.
            sources.append(SmokeResult(case.name, "fail", str(exc)[-1200:]))
    return sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wsl", action="store_true", help="Delegate to WSL from Windows"
    )
    parser.add_argument("--compiler", default="auto", help="auto, clang, gcc, or path")
    parser.add_argument("--program", action="append", default=[])
    parser.add_argument("--no-corpus", action="store_true")
    parser.add_argument("--generated", type=int, default=0)
    parser.add_argument(
        "--surface-runtime",
        action="store_true",
        help="Include curated runtime-bearing syntax surface cases",
    )
    parser.add_argument("--seed", type=int, default=166)
    args = parser.parse_args()

    if args.wsl:
        return _run_wsl(args)

    compiler = _resolve_compiler(args.compiler)
    if compiler is None:
        print("sanitizer smoke: no C compiler found")
        return 2

    names = [] if args.no_corpus else (args.program or DEFAULT_PROGRAMS)
    with tempfile.TemporaryDirectory(
        prefix="ailang_sanitizer_", dir=REPO_ROOT / "out"
    ) as td:
        tmp = Path(td)
        source_items: list[SmokeResult | Path] = [
            CORPUS_DIR / f"{name}.ail" for name in names
        ]
        if args.surface_runtime:
            source_items.extend(_runtime_surface_sources(tmp))
        if args.generated:
            source_items.extend(
                _generated_sources(args.generated, args.seed, tmp, compiler)
            )
        results = []
        for item in source_items:
            if isinstance(item, SmokeResult):
                results.append(item)
            else:
                results.append(_compile_and_run(item, tmp, compiler))

    failures = [row for row in results if row.status == "fail"]
    passes = [row for row in results if row.status == "pass"]
    skips = [row for row in results if row.status == "skip"]
    print(
        f"sanitizer smoke compiler={compiler} "
        f"pass={len(passes)} skip={len(skips)} fail={len(failures)}"
    )
    for row in results:
        print(f"- {row.name}: {row.status} {row.detail[:240]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

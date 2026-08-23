#!/usr/bin/env python3
"""Run hard AILang/C/Rust benchmark kernels with output parity checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = REPO_ROOT / "benchmarks"
OUT_ROOT = BENCH_ROOT / "out" / "tri_language_gauntlet"
RESULTS_ROOT = BENCH_ROOT / "results"
AILANG = REPO_ROOT / "ailang.py"
DEFAULT_THRESHOLDS = BENCH_ROOT / "performance_thresholds.json"


@dataclass(frozen=True)
class Kernel:
    name: str
    description: str
    ops: int
    unit: str


@dataclass
class Build:
    impl: str
    kernel: str
    exe: Path
    compile_ms: float
    status: str = "ok"
    note: str = ""


@dataclass
class RunResult:
    impl: str
    kernel: str
    status: str
    compile_ms: float | None = None
    median_ms: float | None = None
    ns_per_op: float | None = None
    output: int | None = None
    leak_live_bytes: int | None = None
    runs_ms: list[float] | None = None
    note: str = ""


@dataclass
class ThresholdResult:
    impl: str
    kernel: str
    status: str
    max_ns_per_op: float | None = None
    actual_ns_per_op: float | None = None
    note: str = ""


KERNELS: dict[str, Kernel] = {
    "numeric": Kernel(
        "numeric",
        "branch-heavy integer recurrence; compiler optimizer + raw ALU throughput",
        8_000_000,
        "state updates",
    ),
    "protocol": Kernel(
        "protocol",
        "fixed compact-packet byte scanner; hot protocol parser shape",
        1_200_000,
        "packets scanned",
    ),
    "ownership": Kernel(
        "ownership",
        "owned string + dynamic array object churn; cleanup and allocator pressure",
        8_000_000,
        "objects created",
    ),
}

IMPLS = ("ailang_c", "ailang_llvm", "c", "c_erased", "rust", "rust_erased")
RESULT_RE = re.compile(r"[-+]?\d+")
LEAK_RE = re.compile(r"live at exit:\s*(\d+)\s*bytes", re.IGNORECASE)


def _exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


def _run_cmd(
    cmd: list[str],
    *,
    timeout: int = 300,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, float]:
    start = time.perf_counter()
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=proc_env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, "", str(exc), (time.perf_counter() - start) * 1000.0
    return (
        proc.returncode,
        proc.stdout,
        proc.stderr,
        (time.perf_counter() - start) * 1000.0,
    )


def _last_int(text: str) -> int | None:
    matches = RESULT_RE.findall(text.replace("\r", "\n"))
    return int(matches[-1]) if matches else None


def _compiler(name: str) -> str | None:
    return shutil.which(name)


def _c_std_flag(compiler: str) -> str:
    base = Path(compiler).name.lower()
    return "-std=c23" if "clang" in base else "-std=c2x"


def _source(impl: str, kernel: str) -> Path:
    if impl.startswith("ailang"):
        return BENCH_ROOT / "ailang" / f"gauntlet_{kernel}.ail"
    if impl in {"c", "c_erased"}:
        suffix = "_erased" if impl == "c_erased" else ""
        return BENCH_ROOT / "c" / f"gauntlet_{kernel}{suffix}.c"
    if impl in {"rust", "rust_erased"}:
        suffix = "_erased" if impl == "rust_erased" else ""
        return BENCH_ROOT / "rust" / f"gauntlet_{kernel}{suffix}.rs"
    raise ValueError(f"unknown impl: {impl}")


def _missing_source_build(impl: str, kernel: str, src: Path, exe: Path) -> Build | None:
    if src.exists():
        return None
    return Build(impl, kernel, exe, 0.0, "skip", f"source not found: {src}")


def _build(impl: str, kernel: str, native: bool) -> Build:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    src = _source(impl, kernel)
    stem = OUT_ROOT / f"{kernel}_{impl}"
    exe = _exe(stem)
    missing = _missing_source_build(impl, kernel, src, exe)
    if missing is not None:
        return missing
    if impl == "ailang_c":
        cmd = [sys.executable, str(AILANG), str(src), "--backend=c", "-o", str(stem)]
    elif impl == "ailang_llvm":
        cmd = [
            sys.executable,
            str(AILANG),
            str(src),
            "--backend=llvm",
            "-o",
            str(stem),
        ]
    elif impl in {"c", "c_erased"}:
        cc = _compiler("clang") or _compiler("gcc")
        if cc is None:
            return Build(impl, kernel, exe, 0.0, "skip", "no C compiler found")
        cmd = [
            cc,
            _c_std_flag(cc),
            "-O3",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            str(src),
            "-o",
            str(exe),
        ]
        if native:
            cmd.insert(2, "-march=native")
    elif impl in {"rust", "rust_erased"}:
        rustc = _compiler("rustc")
        if rustc is None:
            return Build(impl, kernel, exe, 0.0, "skip", "no rustc found")
        cmd = [rustc, str(src), "-O", "-C", "opt-level=3", "-o", str(exe)]
        if native:
            cmd[5:5] = ["-C", "target-cpu=native"]
    else:
        raise ValueError(f"unknown impl: {impl}")

    rc, stdout, stderr, elapsed = _run_cmd(cmd, timeout=420)
    if rc != 0:
        note = (stdout + stderr).strip()[-1200:]
        return Build(impl, kernel, exe, elapsed, "fail", note)
    return Build(impl, kernel, exe, elapsed)


def _leak_probe(build: Build) -> tuple[int | None, str | None]:
    if build.impl != "ailang_c":
        return None, None
    rc, stdout, stderr, _elapsed = _run_cmd(
        [str(build.exe)], timeout=120, env={"AILANG_LEAK_REPORT": "1"}
    )
    if rc != 0:
        return None, f"leak probe exited {rc}: {(stdout + stderr).strip()[-600:]}"
    match = LEAK_RE.search(stdout + stderr)
    if match is None:
        return None, "leak probe did not emit an AILang memory report"
    return int(match.group(1)), None


def _measure(build: Build, runs: int, warmup: int, check_leaks: bool) -> RunResult:
    if build.status != "ok":
        return RunResult(
            build.impl,
            build.kernel,
            build.status,
            compile_ms=build.compile_ms,
            note=build.note,
        )
    for _ in range(warmup):
        _run_cmd([str(build.exe)], timeout=120)
    timings: list[float] = []
    output: int | None = None
    for _ in range(runs):
        rc, stdout, stderr, elapsed = _run_cmd([str(build.exe)], timeout=120)
        if rc != 0:
            return RunResult(
                build.impl,
                build.kernel,
                "runtime_fail",
                compile_ms=build.compile_ms,
                note=(stdout + stderr).strip()[-1200:],
            )
        parsed = _last_int(stdout)
        if parsed is None:
            return RunResult(
                build.impl,
                build.kernel,
                "bad_output",
                compile_ms=build.compile_ms,
                note=stdout.strip()[-1200:],
            )
        output = parsed
        timings.append(elapsed)
    median_ms = statistics.median(timings)
    kernel = KERNELS[build.kernel]
    leak_live_bytes: int | None = None
    if check_leaks:
        leak_live_bytes, leak_note = _leak_probe(build)
        if leak_note is not None:
            return RunResult(
                build.impl,
                build.kernel,
                "leak_probe_fail",
                compile_ms=build.compile_ms,
                median_ms=median_ms,
                ns_per_op=(median_ms * 1_000_000.0) / kernel.ops,
                output=output,
                leak_live_bytes=leak_live_bytes,
                runs_ms=timings,
                note=leak_note,
            )
        if leak_live_bytes not in (None, 0):
            return RunResult(
                build.impl,
                build.kernel,
                "leak_fail",
                compile_ms=build.compile_ms,
                median_ms=median_ms,
                ns_per_op=(median_ms * 1_000_000.0) / kernel.ops,
                output=output,
                leak_live_bytes=leak_live_bytes,
                runs_ms=timings,
                note=f"AILang live bytes at exit: {leak_live_bytes}",
            )
    return RunResult(
        build.impl,
        build.kernel,
        "ok",
        compile_ms=build.compile_ms,
        median_ms=median_ms,
        ns_per_op=(median_ms * 1_000_000.0) / kernel.ops,
        output=output,
        leak_live_bytes=leak_live_bytes,
        runs_ms=timings,
    )


def _load_thresholds(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"threshold file is not an object: {path}")
    raw = payload.get("thresholds", {})
    if not isinstance(raw, dict):
        raise ValueError(f"thresholds must be an object in {path}")
    return raw


def _check_thresholds(
    results: list[RunResult], thresholds: dict[str, Any]
) -> list[ThresholdResult]:
    checked: list[ThresholdResult] = []
    for result in results:
        kernel_thresholds = thresholds.get(result.kernel, {})
        if not isinstance(kernel_thresholds, dict):
            continue
        config = kernel_thresholds.get(result.impl)
        if not isinstance(config, dict):
            continue
        max_ns = config.get("max_ns_per_op")
        if not isinstance(max_ns, (int, float)):
            continue
        actual = result.ns_per_op
        if result.status != "ok" or actual is None:
            checked.append(
                ThresholdResult(
                    result.impl,
                    result.kernel,
                    "fail",
                    float(max_ns),
                    actual,
                    f"result status is {result.status}",
                )
            )
            continue
        status = "pass" if actual <= float(max_ns) else "fail"
        note = "" if status == "pass" else f"{actual:.2f} > {float(max_ns):.2f}"
        checked.append(
            ThresholdResult(result.impl, result.kernel, status, float(max_ns), actual, note)
        )
    return checked


def _write_reports(
    results: list[RunResult],
    native: bool,
    thresholds: dict[str, Any],
    threshold_results: list[ThresholdResult],
) -> None:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "native_flags": native,
        "kernels": {name: kernel.__dict__ for name, kernel in KERNELS.items()},
        "results": [result.__dict__ for result in results],
        "thresholds": thresholds,
        "threshold_results": [row.__dict__ for row in threshold_results],
    }
    json_path = RESULTS_ROOT / "tri_language_gauntlet.json"
    md_path = RESULTS_ROOT / "tri_language_gauntlet.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Tri-Language Gauntlet",
        "",
        "Hard kernels for AILang/C/Rust parity and performance checks.",
        "",
        f"- Native CPU flags: `{native}`",
        "",
    ]
    for kernel_name, kernel in KERNELS.items():
        lines.extend(
            [
                f"## {kernel_name}",
                "",
                f"- Workload: {kernel.description}",
                f"- Operations: `{kernel.ops}` {kernel.unit}",
                "",
                "| Impl | Status | Compile ms | Median ms | ns/op | Output | Live bytes | Note |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        case_rows = [r for r in results if r.kernel == kernel_name]
        for row in sorted(case_rows, key=lambda r: IMPLS.index(r.impl)):
            lines.append(
                "| {impl} | {status} | {compile_ms} | {median_ms} | {ns_per_op} | {output} | {live} | {note} |".format(
                    impl=row.impl,
                    status=row.status,
                    compile_ms=(
                        f"{row.compile_ms:.2f}" if row.compile_ms is not None else ""
                    ),
                    median_ms=(
                        f"{row.median_ms:.2f}" if row.median_ms is not None else ""
                    ),
                    ns_per_op=(
                        f"{row.ns_per_op:.2f}" if row.ns_per_op is not None else ""
                    ),
                    output=row.output if row.output is not None else "",
                    live=(
                        row.leak_live_bytes if row.leak_live_bytes is not None else ""
                    ),
                    note=(row.note or "").replace("|", "\\|"),
                )
            )
        ok_outputs = {r.output for r in case_rows if r.status == "ok"}
        verdict = "pass" if len(ok_outputs) <= 1 else "FAIL"
        threshold_rows = [r for r in threshold_results if r.kernel == kernel_name]
        threshold_verdict = "not run"
        if threshold_rows:
            threshold_verdict = (
                "pass" if all(row.status == "pass" for row in threshold_rows) else "FAIL"
            )
        lines.extend(
            [
                "",
                f"Output parity: `{verdict}`",
                f"Threshold gate: `{threshold_verdict}`",
                "",
            ]
        )
        if threshold_rows:
            lines.extend(
                [
                    "| Threshold Impl | Status | Actual ns/op | Max ns/op | Note |",
                    "|---|---:|---:|---:|---|",
                ]
            )
            for row in sorted(threshold_rows, key=lambda r: IMPLS.index(r.impl)):
                lines.append(
                    "| {impl} | {status} | {actual} | {max_ns} | {note} |".format(
                        impl=row.impl,
                        status=row.status,
                        actual=(
                            f"{row.actual_ns_per_op:.2f}"
                            if row.actual_ns_per_op is not None
                            else ""
                        ),
                        max_ns=(
                            f"{row.max_ns_per_op:.2f}"
                            if row.max_ns_per_op is not None
                            else ""
                        ),
                        note=(row.note or "").replace("|", "\\|"),
                    )
                )
            lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _selected(
    values: list[str] | None, allowed: tuple[str, ...] | set[str]
) -> list[str]:
    if not values:
        return list(allowed)
    bad = [value for value in values if value not in allowed]
    if bad:
        raise SystemExit(f"unknown selection: {', '.join(bad)}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--case", action="append", choices=tuple(KERNELS))
    parser.add_argument("--impl", action="append", choices=IMPLS)
    parser.add_argument(
        "--native", action="store_true", help="Use native CPU flags for C/Rust."
    )
    parser.add_argument("--no-parity-fail", action="store_true")
    parser.add_argument(
        "--skip-leak-checks",
        action="store_true",
        help="Skip AILang C AILANG_LEAK_REPORT probes.",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=DEFAULT_THRESHOLDS,
        help="Performance threshold JSON path.",
    )
    parser.add_argument(
        "--enforce-thresholds",
        action="store_true",
        help="Fail if any configured ns/op threshold is exceeded.",
    )
    args = parser.parse_args()

    kernels = _selected(args.case, tuple(KERNELS))
    implicit_impls = not args.impl
    impls = _selected(args.impl, IMPLS)
    results: list[RunResult] = []
    for kernel in kernels:
        for impl in impls:
            if (
                implicit_impls
                and impl.endswith("_erased")
                and not _source(impl, kernel).exists()
            ):
                continue
            build = _build(impl, kernel, args.native)
            result = _measure(build, args.runs, args.warmup, not args.skip_leak_checks)
            results.append(result)
            status = result.status
            timing = (
                f"{result.median_ms:.2f} ms, {result.ns_per_op:.2f} ns/op"
                if result.median_ms is not None and result.ns_per_op is not None
                else result.note
            )
            if result.leak_live_bytes is not None:
                timing += f", live={result.leak_live_bytes}"
            print(f"{kernel:10s} {impl:11s} {status:13s} {timing}")

    thresholds = _load_thresholds(args.thresholds)
    threshold_results = _check_thresholds(results, thresholds)
    for row in threshold_results:
        if row.status != "pass":
            print(
                f"threshold failed for {row.kernel}/{row.impl}: "
                f"{row.note or row.status}"
            )

    _write_reports(results, args.native, thresholds, threshold_results)

    parity_failed = False
    failed = False
    for kernel in kernels:
        outputs = {r.output for r in results if r.kernel == kernel and r.status == "ok"}
        if len(outputs) > 1:
            print(f"output parity failed for {kernel}: {sorted(outputs)}")
            parity_failed = True
    leak_failures = [
        r
        for r in results
        if r.status in {"leak_fail", "leak_probe_fail"}
        or (r.leak_live_bytes is not None and r.leak_live_bytes != 0)
    ]
    for result in leak_failures:
        print(
            f"leak check failed for {result.kernel}/{result.impl}: "
            f"{result.leak_live_bytes} live bytes"
        )
        failed = True
    if args.enforce_thresholds and any(row.status != "pass" for row in threshold_results):
        failed = True
    print(f"reports: {RESULTS_ROOT / 'tri_language_gauntlet.md'}")
    if failed or (parity_failed and not args.no_parity_fail):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

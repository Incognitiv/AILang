#!/usr/bin/env python3
"""Cross-language benchmark runner for AILang vs C23/Python/Rust.

This script:
  - compiles AILang (AOT), C23, and Rust benchmark programs
  - runs JIT AILang, Python, and compiled executables
  - executes each implementation multiple times with warmup
  - checks output parity across implementations
  - writes a `benchmark_results.md` summary
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
AILEXEC = REPO_ROOT / "ailang.py"
OUT_DIR = ROOT / "out"

AilangTool = sys.executable


@dataclass
class Measurement:
    status: str
    compile_ms: Optional[float] = None
    runs_ms: Optional[list[float]] = None
    output: Optional[str] = None
    output_tokens: Optional[str] = None
    checksum: Optional[int] = None
    leak_alloc_bytes: Optional[int] = None
    leak_freed_bytes: Optional[int] = None
    leak_live_bytes: Optional[int] = None
    leak_check_status: Optional[bool] = None
    peak_rss_bytes: Optional[int] = None
    note: Optional[str] = None


@dataclass
class BenchmarkCase:
    name: str
    display_name: str
    iterations: int
    unit: str
    files: dict[str, Path]


LEAK_REPORT_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


def command_exists(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _run_cmd(
    cmd: list[str],
    timeout: int = 180,
    env: Optional[dict[str, str]] = None,
    monitor_memory: bool = False,
) -> tuple[int, str, str, float, Optional[int]]:
    start = time.perf_counter()
    run_env = None if env is None else {**os.environ, **env}
    if not monitor_memory:
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                env=run_env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            return 124, stdout, stderr, elapsed_ms, None
        except (FileNotFoundError, OSError) as exc:
            return 127, "", str(exc), 0.0, None
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return (
            completed.returncode,
            completed.stdout,
            completed.stderr,
            elapsed_ms,
            None,
        )

    peak_rss_bytes: Optional[int] = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=run_env,
        )
    except (FileNotFoundError, OSError) as exc:
        return 127, "", str(exc), 0.0, None

    p_handle = psutil.Process(proc.pid) if psutil is not None else None
    deadline = start + timeout
    while proc.poll() is None:
        if p_handle is not None:
            try:
                current_rss = p_handle.memory_info().rss
                peak_rss_bytes = (
                    current_rss
                    if peak_rss_bytes is None
                    else max(peak_rss_bytes, current_rss)
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):  # type: ignore[attr-defined]
                p_handle = None

        now = time.perf_counter()
        if now >= deadline:
            proc.kill()
            break
        time.sleep(0.001)

    stdout, stderr = proc.communicate()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return proc.returncode, stdout, stderr, elapsed_ms, peak_rss_bytes


def _extract_result_int(text: str) -> Optional[int]:
    tokens = re.findall(r"[-+]?\d+", text.replace("\r", "\n"))
    if not tokens:
        return None
    return int(tokens[-1])
    return None


def _parse_leak_report(text: str) -> Optional[tuple[int, int, int]]:
    """Parse AILANG_LEAK_REPORT output if present."""
    m = LEAK_REPORT_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _apply_leak_check(
    measurement: Measurement,
    check_leaks: bool,
    leak_threshold: int,
) -> Measurement:
    if not check_leaks:
        return measurement
    if measurement.leak_live_bytes is None:
        measurement.leak_check_status = None
        return measurement
    measurement.leak_check_status = measurement.leak_live_bytes <= leak_threshold
    if measurement.status != "ok":
        return measurement
    if not measurement.leak_check_status:
        note = (
            f"live leak bytes {measurement.leak_live_bytes} exceeds threshold "
            f"{leak_threshold}"
        )
        if measurement.note:
            measurement.note = f"{measurement.note}; {note}"
        else:
            measurement.note = note
        measurement.status = "fail"
    return measurement


def _coerce_optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _version(cmd: str, args: list[str]) -> str:
    try:
        code, stdout, stderr, _, _ = _run_cmd([cmd] + args, timeout=20)
        if code == 0:
            return (stdout.strip() or stderr.strip()).splitlines()[0]
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return "n/a"


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.median(values)


def _mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.mean(values)


def _maybe_clean_int(value: int) -> str:
    return f"{value:,}"


def _ext() -> str:
    return ".exe" if platform.system() == "Windows" else ""


def compile_ailang_aot(
    source: Path, case_id: str
) -> tuple[bool, float, Optional[Path], str]:
    exe = OUT_DIR / f"bench_{case_id}_ailang_aot{_ext()}"
    cmd = [AilangTool, str(AILEXEC), str(source), "-o", str(exe), "-O3"]
    rc, stdout, stderr, elapsed, _ = _run_cmd(cmd, timeout=300)
    if rc != 0:
        return False, elapsed, None, (stdout + stderr).strip()
    return True, elapsed, exe, ""


def compile_c23(source: Path, case_id: str) -> tuple[bool, float, Optional[Path], str]:
    compiler = command_exists("clang") or command_exists("gcc")
    if not compiler:
        return False, 0.0, None, "No C compiler found (gcc/clang)"
    exe = OUT_DIR / f"bench_{case_id}_c23{_ext()}"
    cmd = [
        compiler,
        "-std=c2x",
        "-O3",
        "-DNDEBUG",
        "-o",
        str(exe),
        str(source),
    ]
    rc, stdout, stderr, elapsed, _ = _run_cmd(cmd, timeout=300)
    if rc != 0:
        return False, elapsed, None, (stdout + stderr).strip()
    return True, elapsed, exe, ""


def compile_rust(source: Path, case_id: str) -> tuple[bool, float, Optional[Path], str]:
    compiler = command_exists("rustc")
    if not compiler:
        return False, 0.0, None, "rustc not found"
    exe = OUT_DIR / f"bench_{case_id}_rust{_ext()}"
    cmd = ["rustc", "-O", str(source), "-o", str(exe)]
    rc, stdout, stderr, elapsed, _ = _run_cmd(cmd, timeout=300)
    if rc != 0:
        return False, elapsed, None, (stdout + stderr).strip()
    return True, elapsed, exe, ""


def _build_measurements(
    command: list[str],
    run_count: int,
    warmup_count: int,
    timeout: int = 180,
    env: Optional[dict[str, str]] = None,
    capture_leaks: bool = False,
    check_leaks: bool = False,
    leak_threshold: int = 0,
    sample_memory: bool = False,
) -> Measurement:
    all_outputs = []
    run_samples: list[float] = []
    leak_alloc: Optional[int] = None
    leak_freed: Optional[int] = None
    leak_live: Optional[int] = None
    peak_rss_bytes: Optional[int] = None

    for idx in range(run_count + warmup_count):
        rc, stdout, stderr, elapsed, peak = _run_cmd(
            command, timeout=timeout, env=env, monitor_memory=False
        )
        if rc != 0:
            return Measurement(
                status="fail",
                runs_ms=run_samples,
                output=stdout.strip(),
                output_tokens=stderr.strip() or stdout.strip(),
                note=f"Runtime failed (exit={rc}): {(stdout + stderr).strip()}",
            )
        value = _extract_result_int(stdout)
        if value is None:
            return Measurement(
                status="fail",
                runs_ms=run_samples,
                output=stdout.strip(),
                output_tokens=stderr.strip() or stdout.strip(),
                note="No integer output parsed",
            )
        all_outputs.append(value)
        if idx >= warmup_count:
            run_samples.append(elapsed)

        if capture_leaks:
            parsed = _parse_leak_report(stdout + stderr)
            if parsed is not None:
                a, f, live = parsed
                if leak_alloc is None and leak_freed is None and leak_live is None:
                    leak_alloc, leak_freed, leak_live = a, f, live
                elif (a, f, live) != (leak_alloc, leak_freed, leak_live):
                    return Measurement(
                        status="fail",
                        runs_ms=run_samples,
                        output=stdout.strip(),
                        output_tokens=stderr.strip() or stdout.strip(),
                        note="Leak counters changed across measured runs",
                        leak_alloc_bytes=a,
                        leak_freed_bytes=f,
                        leak_live_bytes=live,
                    )
        if peak is not None:
            peak_rss_bytes = (
                peak if peak_rss_bytes is None else max(peak_rss_bytes, peak)
            )

    # Collect RSS in a dedicated non-timed sample run to avoid perturbing
    # benchmark timings with process polling overhead.
    if sample_memory:
        rc, stdout, stderr, _elapsed, peak = _run_cmd(
            command, timeout=timeout, env=env, monitor_memory=True
        )
        if rc == 0:
            sampled_value = _extract_result_int(stdout)
            expected_value = all_outputs[warmup_count] if all_outputs else None
            if sampled_value is not None and expected_value is not None:
                if sampled_value != expected_value:
                    return Measurement(
                        status="fail",
                        runs_ms=run_samples,
                        output=stdout.strip(),
                        output_tokens=stderr.strip() or stdout.strip(),
                        checksum=expected_value,
                        note=(
                            "Memory-sample run checksum mismatch "
                            f"(got {sampled_value}, expected {expected_value})"
                        ),
                        leak_alloc_bytes=leak_alloc,
                        leak_freed_bytes=leak_freed,
                        leak_live_bytes=leak_live,
                    )
            if peak is not None:
                peak_rss_bytes = peak
        else:
            # Keep timing result but record that RSS sample failed.
            sample_note = f"memory-sample run failed (exit={rc})"
            if stderr.strip():
                sample_note = f"{sample_note}: {stderr.strip()}"
            return Measurement(
                status="fail",
                runs_ms=run_samples,
                output=stdout.strip(),
                output_tokens=stderr.strip() or stdout.strip(),
                checksum=all_outputs[warmup_count] if all_outputs else None,
                note=sample_note,
                leak_alloc_bytes=leak_alloc,
                leak_freed_bytes=leak_freed,
                leak_live_bytes=leak_live,
            )

    if len(set(all_outputs[warmup_count:])) > 1:
        return Measurement(
            status="fail",
            runs_ms=run_samples,
            output=",".join(str(v) for v in all_outputs),
            output_tokens=str(all_outputs[warmup_count]),
            checksum=all_outputs[warmup_count],
            note="Non-deterministic output across measured runs",
        )
    result = Measurement(
        status="ok",
        runs_ms=run_samples,
        output=str(all_outputs[warmup_count]),
        checksum=all_outputs[warmup_count],
        output_tokens=all_outputs[warmup_count:][0:3].__str__(),
        leak_alloc_bytes=leak_alloc,
        leak_freed_bytes=leak_freed,
        leak_live_bytes=leak_live,
        peak_rss_bytes=peak_rss_bytes,
    )
    return _apply_leak_check(
        result, check_leaks=check_leaks, leak_threshold=leak_threshold
    )


def _build_measurements_from_json(
    command: list[str],
    timeout: int = 180,
    run_count: int = 1,
    warmup_count: int = 0,
    check_leaks: bool = False,
    leak_threshold: int = 0,
    sample_memory: bool = False,
) -> Measurement:
    rc, stdout, stderr, _elapsed, peak_rss_bytes = _run_cmd(
        command, timeout=timeout, monitor_memory=sample_memory
    )
    if rc != 0:
        return Measurement(
            status="fail",
            output=stdout.strip(),
            output_tokens=stderr.strip() or stdout.strip(),
            note=f"Runtime failed (exit={rc}): {(stdout + stderr).strip()}",
        )

    marker = "JIT_WARM_RESULT="
    lines = stdout.splitlines()
    marker_index = next(
        (idx for idx, line in enumerate(lines) if line.startswith(marker)), -1
    )
    if marker_index < 0:
        return Measurement(
            status="fail",
            output=stdout.strip(),
            output_tokens=stderr.strip() or stdout.strip(),
            note="No JIT result payload found.",
        )

    marker_line = lines[marker_index]
    payload = marker_line[len(marker) :].strip()

    post_marker = lines[marker_index + 1 :]
    value_lines = []
    for ln in post_marker:
        line = ln.strip()
        if line and re.fullmatch(r"[-+]?\d+", line):
            value_lines.append(int(line))
    parsed_checksum: Optional[int] = None
    if value_lines:
        expected_total = run_count + warmup_count
        tail = value_lines[-expected_total:] if expected_total > 0 else value_lines
        measured = tail[warmup_count : expected_total or None]
        if measured:
            first_value = measured[0]
            if all(v == first_value for v in measured):
                parsed_checksum = first_value

    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        return Measurement(
            status="fail",
            output=stdout.strip(),
            output_tokens=stderr.strip() or stdout.strip(),
            note=f"Failed to parse JIT result JSON: {exc}",
        )

    status = obj.get("status", "fail")
    compile_ms = obj.get("compile_ms")
    runs_ms = obj.get("runs_ms")
    checksum = obj.get("checksum")
    if checksum is None and parsed_checksum is not None:
        checksum = parsed_checksum
    note = obj.get("note")
    if not isinstance(runs_ms, list):
        runs_ms = []
    result = Measurement(
        status=status,
        compile_ms=compile_ms,
        runs_ms=[float(v) for v in runs_ms],
        checksum=checksum,
        output=stdout.strip(),
        output_tokens=stderr.strip() or stdout.strip(),
        note=note,
        leak_alloc_bytes=_coerce_optional_int(obj.get("leak_alloc_bytes")),
        leak_freed_bytes=_coerce_optional_int(obj.get("leak_freed_bytes")),
        leak_live_bytes=_coerce_optional_int(obj.get("leak_live_bytes")),
        peak_rss_bytes=peak_rss_bytes,
    )
    return _apply_leak_check(
        result,
        check_leaks=check_leaks,
        leak_threshold=leak_threshold,
    )


def gather_versions() -> dict[str, str]:
    versions = {
        "python": sys.version.splitlines()[0],
    }
    versions["ailang"] = "source entrypoint"
    c = command_exists("gcc") or command_exists("clang")
    versions["c23"] = _version(c or "gcc", ["--version"]) if c else "not found"
    versions["rust"] = _version("rustc", ["--version"])
    versions["os"] = platform.platform()
    versions["machine"] = platform.machine()
    return versions


def run_benchmarks(
    cases: Iterable[BenchmarkCase],
    run_count: int,
    warmup_count: int,
    implementations: Iterable[str],
    check_output: bool,
    check_leaks: bool,
    leak_threshold: int,
    sample_memory: bool,
) -> dict[str, dict[str, Measurement]]:
    _ensure_dir(OUT_DIR)
    results: dict[str, dict[str, Measurement]] = {}

    for case in cases:
        case_results: dict[str, Measurement] = {}
        print(f"\n[{case.name}]")
        files = case.files

        for impl in implementations:
            if impl == "ailang_aot":
                source = files["ailang"]
                ok, compile_ms, exe, note = compile_ailang_aot(source, case.name)
                if not ok or exe is None:
                    case_results[impl] = Measurement(
                        status="fail", compile_ms=compile_ms, note=note
                    )
                    print(f"  {impl}: compile fail ({note})")
                    continue
                runtime = _build_measurements(
                    [str(exe)],
                    run_count,
                    warmup_count,
                    env={"AILANG_LEAK_REPORT": "1"},
                    capture_leaks=True,
                    check_leaks=check_leaks,
                    leak_threshold=leak_threshold,
                    sample_memory=sample_memory,
                )
                runtime.compile_ms = compile_ms
                case_results[impl] = runtime
                print(
                    f"  {impl}: {runtime.status} runtime-median={_median(runtime.runs_ms or [])}"
                )
                continue

            if impl == "ailang_jit":
                source = files["ailang"]
                runtime = _build_measurements(
                    [AilangTool, str(AILEXEC), str(source)],
                    run_count,
                    warmup_count,
                    sample_memory=sample_memory,
                    check_leaks=check_leaks,
                    leak_threshold=leak_threshold,
                )
                case_results[impl] = runtime
                print(
                    f"  {impl}: {runtime.status} runtime-median={_median(runtime.runs_ms or [])}"
                )
                continue

            if impl == "ailang_jit_warm":
                source = files["ailang"]
                cmd = [
                    AilangTool,
                    str(AILEXEC),
                    str(source),
                    "--jit-repeat",
                    str(run_count),
                    "--jit-warmup",
                    str(warmup_count),
                    "--jit-json",
                ]
                runtime = _build_measurements_from_json(
                    cmd,
                    timeout=300,
                    run_count=run_count,
                    warmup_count=warmup_count,
                    check_leaks=check_leaks,
                    leak_threshold=leak_threshold,
                    sample_memory=sample_memory,
                )
                case_results[impl] = runtime
                print(
                    f"  {impl}: {runtime.status} runtime-median={_median(runtime.runs_ms or [])}"
                )
                continue

            if impl == "python":
                source = files["python"]
                runtime = _build_measurements(
                    [sys.executable, str(source)],
                    run_count,
                    warmup_count,
                    sample_memory=sample_memory,
                )
                case_results[impl] = runtime
                print(
                    f"  {impl}: {runtime.status} runtime-median={_median(runtime.runs_ms or [])}"
                )
                continue

            if impl == "c23":
                source = files["c"]
                ok, compile_ms, exe, note = compile_c23(source, case.name)
                if not ok or exe is None:
                    case_results[impl] = Measurement(
                        status="fail", compile_ms=compile_ms, note=note
                    )
                    print(f"  {impl}: compile fail ({note})")
                    continue
                runtime = _build_measurements(
                    [str(exe)],
                    run_count,
                    warmup_count,
                    env={"AILANG_LEAK_REPORT": "1"},
                    capture_leaks=True,
                    check_leaks=check_leaks,
                    leak_threshold=leak_threshold,
                    sample_memory=sample_memory,
                )
                runtime.compile_ms = compile_ms
                case_results[impl] = runtime
                print(
                    f"  {impl}: {runtime.status} runtime-median={_median(runtime.runs_ms or [])}"
                )
                continue

            if impl == "rust":
                source = files["rust"]
                ok, compile_ms, exe, note = compile_rust(source, case.name)
                if not ok or exe is None:
                    case_results[impl] = Measurement(
                        status="fail", compile_ms=compile_ms, note=note
                    )
                    print(f"  {impl}: compile fail ({note})")
                    continue
                runtime = _build_measurements([str(exe)], run_count, warmup_count)
                runtime = _apply_leak_check(
                    runtime,
                    check_leaks=check_leaks,
                    leak_threshold=leak_threshold,
                )
                runtime.compile_ms = compile_ms
                case_results[impl] = runtime
                print(
                    f"  {impl}: {runtime.status} runtime-median={_median(runtime.runs_ms or [])}"
                )
                continue

        # Validate output parity across successful implementations.
        reference = None
        for result in case_results.values():
            if result.status == "ok" and result.checksum is not None:
                reference = result.checksum
                break

        if check_output and reference is not None:
            for result in case_results.values():
                if result.status != "ok":
                    continue
                if result.checksum != reference:
                    suffix = f"checksum mismatch (got {result.checksum}, expected {reference})"
                    result.note = f"{result.note + '; ' if result.note else ''}{suffix}"
                    result.note = result.note.strip()
        results[case.name] = case_results

    return results


def generate_report(
    run_count: int,
    warmup_count: int,
    cases: Iterable[BenchmarkCase],
    results: dict[str, dict[str, Measurement]],
    output_path: Path,
) -> None:
    versions = gather_versions()

    lines = [
        "# Benchmark Results",
        "",
        f"- Date: {time.strftime(DATE_HUMAN_FMT)}",
        f"- OS: {versions['os']}",
        f"- Python: {versions['python']}",
        "- AILang entrypoint: `ailang.py`",
        f"- C compiler: {versions['c23']}",
        f"- Rust: {versions['rust']}",
        "",
        f"- Runtime repeats: {run_count} (plus {warmup_count} warmup run{'s' if warmup_count != 1 else ''})",
        "",
        "## Command Environment",
        "",
        "```",
        f"cwd = {REPO_ROOT}",
        "```",
        "",
    ]

    for case in cases:
        case_result = results.get(case.name, {})
        lines.extend(
            [
                f"## {case.display_name}",
                "",
                f"- Workload: `{case.unit}` with `{_maybe_clean_int(case.iterations)}` iterations",
                "",
                "| Implementation | Compile (ms) | Runtime median (ms) | Runtime mean (ms) | Throughput (M ops/s) | Checksum | Peak RSS (KiB) | Leak alloc (B) | Leak freed (B) | Leak live (B) | Leak pass | Status |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )

        for impl, result in case_result.items():
            compile_ms = (
                "n/a" if result.compile_ms is None else f"{result.compile_ms:.2f}"
            )
            run_samples = result.runs_ms if result.runs_ms is not None else []
            median_ms = _median(run_samples)
            mean_ms = _mean(run_samples)
            med_s = (median_ms or 0.0) / 1000.0 if median_ms else 0.0
            throughput = (case.iterations / med_s) / 1_000_000 if med_s else 0.0
            through_txt = f"{throughput:.2f}" if med_s > 0 else "n/a"
            checksum = "n/a" if result.checksum is None else str(result.checksum)
            peak_rss = (
                "n/a"
                if result.peak_rss_bytes is None
                else str(result.peak_rss_bytes // 1024)
            )
            leak_alloc = (
                "n/a"
                if result.leak_alloc_bytes is None
                else str(result.leak_alloc_bytes)
            )
            leak_freed = (
                "n/a"
                if result.leak_freed_bytes is None
                else str(result.leak_freed_bytes)
            )
            leak_live = (
                "n/a" if result.leak_live_bytes is None else str(result.leak_live_bytes)
            )
            leak_pass = (
                "n/a"
                if result.leak_check_status is None
                else ("pass" if result.leak_check_status else "fail")
            )
            status = result.status
            if result.note:
                status = f"{result.status}: {result.note}"
            med_txt = "n/a" if median_ms is None else f"{median_ms:.2f}"
            mean_txt = "n/a" if mean_ms is None else f"{mean_ms:.2f}"
            lines.append(
                f"| {impl} | {compile_ms} | {med_txt} | {mean_txt} | "
                f"{through_txt} | {checksum} | {peak_rss} | {leak_alloc} | {leak_freed} | {leak_live} | "
                f"{leak_pass} | {status} |"
            )
        lines.append("")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- C implementation is compiled as C23 (`-std=c2x`) with `-O3`.",
            "- Rust is compiled as optimized release (`rustc -O`).",
            "- `ailang_jit` uses cold-start invocation (`python ailang.py <source>` each run).",
            "- `ailang_jit_warm` compiles once per case and then runs warmup/measured "
            "iterations inside a single process.",
            "- AILang AOT mode uses `-O3`.",
            "- Leak telemetry is collected by setting `AILANG_LEAK_REPORT=1` in backends "
            "that emit leak summary output; values are parsed into `Leak * (B)` columns.",
            "- Peak RSS is sampled in-process via optional `psutil` support when "
            "`--sample-memory` is enabled.",
            "- Use `--check-leaks` with `--leak-threshold` (default 0) to fail "
            "entries whose `Leak live (B)` exceeds threshold.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")
    output_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "metadata": {
                    "os": versions["os"],
                    "python": versions["python"],
                    "rust": versions["rust"],
                    "c23": versions["c23"],
                    "machine": versions["machine"],
                    "run_count": run_count,
                    "warmup_count": warmup_count,
                    "cases": [
                        {"name": c.name, "iterations": c.iterations, "unit": c.unit}
                        for c in cases
                    ],
                },
                "results": {
                    case: {
                        impl: {
                            "status": m.status,
                            "compile_ms": m.compile_ms,
                            "runs_ms": m.runs_ms,
                            "checksum": m.checksum,
                            "peak_rss_bytes": m.peak_rss_bytes,
                            "leak_alloc_bytes": m.leak_alloc_bytes,
                            "leak_freed_bytes": m.leak_freed_bytes,
                            "leak_live_bytes": m.leak_live_bytes,
                            "leak_check_status": m.leak_check_status,
                            "note": m.note,
                        }
                        for impl, m in impls.items()
                    }
                    for case, impls in results.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run cross-language benchmark comparison (AILang, C23, Rust, Python)."
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of measured benchmark runs (default: 5).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Number of warmup runs (default: 1).",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "benchmark_results.md"),
        help="Markdown output path.",
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=[
            "loop_hash",
            "fib_mix",
            "file_io",
            "dict_ops",
            "records_bench",
            "format_print",
            "format_str_int",
            "format_hex",
            "format_interp",
            "fixed_array_sum",
            "slice_sum",
            "recursive_traversal",
        ],
        help="Run only selected case(s). Omit to run all.",
    )
    parser.add_argument(
        "--impl",
        action="append",
        choices=[
            "ailang_jit",
            "ailang_jit_warm",
            "ailang_aot",
            "c23",
            "rust",
            "python",
        ],
        help="Run only selected implementation(s). Omit to run all.",
    )
    parser.add_argument(
        "--check-output",
        action="store_true",
        help="Verify parsed numeric output matches across implementations; otherwise only report timing.",
    )
    parser.add_argument(
        "--sample-memory",
        action="store_true",
        help="Capture peak RSS (KiB) per implementation from a sample run.",
    )
    parser.add_argument(
        "--check-leaks",
        action="store_true",
        help="Fail entries when emitted leak counters indicate live bytes above threshold.",
    )
    parser.add_argument(
        "--leak-threshold",
        type=int,
        default=0,
        help="Allowed max live bytes for leak check when --check-leaks is enabled. "
        "(default: 0)",
    )
    return parser.parse_args()


def define_cases() -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            name="file_io",
            display_name="File I/O Roundtrip",
            iterations=36_000,
            unit="bytes-processed",
            files={
                "ailang": ROOT / "ailang" / "file_io.ail",
                "c": ROOT / "c" / "file_io.c",
                "rust": ROOT / "rust" / "file_io.rs",
                "python": ROOT / "python" / "file_io.py",
            },
        ),
        BenchmarkCase(
            name="dict_ops",
            display_name="Dictionary Updates",
            iterations=300_000,
            unit="updates",
            files={
                "ailang": ROOT / "ailang" / "dict_ops.ail",
                "c": ROOT / "c" / "dict_ops.c",
                "rust": ROOT / "rust" / "dict_ops.rs",
                "python": ROOT / "python" / "dict_ops.py",
            },
        ),
        BenchmarkCase(
            name="records_bench",
            display_name="Record Field Access",
            iterations=4_000_000,
            unit="updates",
            files={
                "ailang": ROOT / "ailang" / "records_bench.ail",
                "c": ROOT / "c" / "records_bench.c",
                "rust": ROOT / "rust" / "records_bench.rs",
                "python": ROOT / "python" / "records_bench.py",
            },
        ),
        BenchmarkCase(
            name="format_print",
            display_name="Print Formatting",
            iterations=100,
            unit="print-calls",
            files={
                "ailang": ROOT / "ailang" / "format_print.ail",
                "c": ROOT / "c" / "format_print.c",
                "rust": ROOT / "rust" / "format_print.rs",
                "python": ROOT / "python" / "format_print.py",
            },
        ),
        BenchmarkCase(
            name="format_str_int",
            display_name="str(int) Formatting",
            iterations=400_000,
            unit="conversions",
            files={
                "ailang": ROOT / "ailang" / "format_str_int.ail",
                "c": ROOT / "c" / "format_str_int.c",
                "rust": ROOT / "rust" / "format_str_int.rs",
                "python": ROOT / "python" / "format_str_int.py",
            },
        ),
        BenchmarkCase(
            name="format_hex",
            display_name="hex() Formatting",
            iterations=400_000,
            unit="conversions",
            files={
                "ailang": ROOT / "ailang" / "format_hex.ail",
                "c": ROOT / "c" / "format_hex.c",
                "rust": ROOT / "rust" / "format_hex.rs",
                "python": ROOT / "python" / "format_hex.py",
            },
        ),
        BenchmarkCase(
            name="format_interp",
            display_name="Interpolation Formatting",
            iterations=1_000,
            unit="conversions",
            files={
                "ailang": ROOT / "ailang" / "format_interp.ail",
                "c": ROOT / "c" / "format_interp.c",
                "rust": ROOT / "rust" / "format_interp.rs",
                "python": ROOT / "python" / "format_interp.py",
            },
        ),
        BenchmarkCase(
            name="fixed_array_sum",
            display_name="Fixed Array Sum",
            iterations=2_000_000,
            unit="element-adds",
            files={
                "ailang": ROOT / "ailang" / "fixed_array_sum.ail",
                "c": ROOT / "c" / "fixed_array_sum.c",
                "rust": ROOT / "rust" / "fixed_array_sum.rs",
                "python": ROOT / "python" / "fixed_array_sum.py",
            },
        ),
        BenchmarkCase(
            name="slice_sum",
            display_name="Slice/View Sum",
            iterations=2_000_000,
            unit="element-adds",
            files={
                "ailang": ROOT / "ailang" / "slice_sum.ail",
                "c": ROOT / "c" / "slice_sum.c",
                "rust": ROOT / "rust" / "slice_sum.rs",
                "python": ROOT / "python" / "slice_sum.py",
            },
        ),
        BenchmarkCase(
            name="recursive_traversal",
            display_name="Recursive Traversal",
            iterations=2_178_309,
            unit="calls",
            files={
                "ailang": ROOT / "ailang" / "recursive_traversal.ail",
                "c": ROOT / "c" / "recursive_traversal.c",
                "rust": ROOT / "rust" / "recursive_traversal.rs",
                "python": ROOT / "python" / "recursive_traversal.py",
            },
        ),
        BenchmarkCase(
            name="loop_hash",
            display_name="Loop Hash Mix",
            iterations=12_000_000,
            unit="operations",
            files={
                "ailang": ROOT / "ailang" / "loop_hash.ail",
                "c": ROOT / "c" / "loop_hash.c",
                "rust": ROOT / "rust" / "loop_hash.rs",
                "python": ROOT / "python" / "loop_hash.py",
            },
        ),
        BenchmarkCase(
            name="fib_mix",
            display_name="Fibonacci Mix",
            iterations=8_000_000,
            unit="iterations",
            files={
                "ailang": ROOT / "ailang" / "fib_mix.ail",
                "c": ROOT / "c" / "fib_mix.c",
                "rust": ROOT / "rust" / "fib_mix.rs",
                "python": ROOT / "python" / "fib_mix.py",
            },
        ),
    ]


def main() -> int:
    args = parse_args()
    all_cases = define_cases()

    selected_cases = [
        c for c in all_cases if args.case is None or c.name in set(args.case)
    ]
    if not selected_cases:
        print("No benchmark cases selected.")
        return 1

    selected_impls = (
        args.impl
        if args.impl is not None
        else ["ailang_jit_warm", "ailang_aot", "python", "c23", "rust"]
    )

    print("Benchmark configurations")
    print(f"  cases: {', '.join(c.name for c in selected_cases)}")
    print(f"  implementations: {', '.join(selected_impls)}")
    print(f"  runs/warmup: {args.runs}/{args.warmup}")

    results = run_benchmarks(
        selected_cases,
        args.runs,
        args.warmup,
        selected_impls,
        check_output=args.check_output,
        check_leaks=args.check_leaks,
        leak_threshold=args.leak_threshold,
        sample_memory=args.sample_memory,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generate_report(args.runs, args.warmup, selected_cases, results, output_path)

    print(f"\nResults written to {output_path}")
    print(f"JSON data written to {output_path.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

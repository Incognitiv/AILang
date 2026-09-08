#!/usr/bin/env python3
"""Cross-language benchmark runner for AILang vs C23/Python/Rust."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Optional

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from benchmark_cases import define_cases
from benchmark_report import generate_report
from benchmark_support import (
    AILEXEC,
    AilangTool,
    OUT_DIR,
    ROOT,
    BenchmarkCase,
    Measurement,
    _apply_leak_check,
    _coerce_optional_int,
    _ensure_dir,
    _extract_result_int,
    _parse_leak_report,
    _run_cmd,
    command_exists,
    compile_ailang_aot,
    compile_ailang_c_aot,
    compile_c23,
    compile_rust,
)


def _build_measurements(
    command: list[str],
    run_count: int,
    warmup_count: int,
    timeout: int = 180,
    env: Optional[dict[str, str]] = None,
    leak_env: Optional[dict[str, str]] = None,
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

        if peak is not None:
            peak_rss_bytes = (
                peak if peak_rss_bytes is None else max(peak_rss_bytes, peak)
            )

    # Leak telemetry is a correctness probe, not benchmark work. Keep it
    # outside the timed loop so AILANG_LEAK_REPORT stderr/env overhead does
    # not distort performance comparisons against C/Rust/Python baselines.
    if capture_leaks:
        rc, stdout, stderr, _elapsed, _peak = _run_cmd(
            command, timeout=timeout, env=leak_env or env, monitor_memory=False
        )
        if rc != 0:
            return Measurement(
                status="fail",
                runs_ms=run_samples,
                output=stdout.strip(),
                output_tokens=stderr.strip() or stdout.strip(),
                note=f"Leak probe failed (exit={rc}): {(stdout + stderr).strip()}",
            )
        parsed = _parse_leak_report(stdout + stderr)
        if parsed is not None:
            leak_alloc, leak_freed, leak_live = parsed

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
                    leak_env={"AILANG_LEAK_REPORT": "1"},
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

            if impl == "ailang_c_aot":
                source = files["ailang"]
                ok, compile_ms, exe, note = compile_ailang_c_aot(source, case.name)
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
                    leak_env={"AILANG_LEAK_REPORT": "1"},
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
                    leak_env={"AILANG_LEAK_REPORT": "1"},
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
                    result.status = "fail"
        results[case.name] = case_results

    return results


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
            "ailang_c_aot",
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
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit non-zero if any selected implementation fails to build, run, "
        "match output, or satisfy an enabled leak check.",
    )
    return parser.parse_args()


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
        else [
            # AILang itself is always reported first, with the two primary
            # execution modes kept distinct.  Backends/reference languages
            # are secondary comparison lanes, never substitutes for AOT/JIT.
            "ailang_aot",
            "ailang_jit_warm",
            "ailang_jit",
            "ailang_c_aot",
            "c23",
            "rust",
            "python",
        ]
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
    failures = [
        (case, impl, measurement)
        for case, impls in results.items()
        for impl, measurement in impls.items()
        if measurement.status != "ok"
    ]
    if args.fail_on_error and failures:
        for case, impl, measurement in failures:
            print(
                f"benchmark gate failure: {case}/{impl}: "
                f"{measurement.status} {measurement.note}"
            )
        return 1
    return 0

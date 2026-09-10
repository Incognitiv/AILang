"""Markdown/JSON report rendering for the benchmark runner."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path

from benchmark_support import (
    DATE_HUMAN_FMT,
    REPO_ROOT,
    BenchmarkCase,
    Measurement,
    _maybe_clean_int,
    _mean,
    _median,
    gather_versions,
)


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
            "- Primary AILang lanes are always reported first: `ailang_aot` and `ailang_jit_warm`.",
            "- `ailang_aot` is AILang native AOT at `-O3` (currently lowered through LLVM).",
            "- `ailang_jit_warm` is AILang JIT execution after one in-memory compilation; it compiles once per case and then runs warmup/measured iterations in-process.",
            "- `ailang_jit` is the cold-start JIT lane (`python ailang.py <source>` each run), so its wall time includes compiler/JIT startup and is not used as a substitute for warm JIT execution time.",
            "- `ailang_c_aot` is a secondary AILang backend comparison lane (AILang -> generated C -> native), not the canonical AILang AOT result.",
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

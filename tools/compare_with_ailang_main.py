#!/usr/bin/env python3
"""Compare current AILang repo against AILang-main (prototype) with gates.

Outputs:
  - JSON summary
  - Markdown report

Optional gates:
  - performance regression thresholds
  - C-backend leak probe thresholds
  - backend differential fuzzing (LLVM/JIT vs C backend per version)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD_PROTOTYPE_ROOT = REPO_ROOT.parent / "AILang-main" / "prototype"
DEFAULT_NEW_ENTRY = REPO_ROOT / "ailang.py"
DEFAULT_OUT_JSON = (
    REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.json"
)
DEFAULT_OUT_MD = REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.md"
LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)
INT_TOKEN_RE = re.compile(r"[-+]?\d+")
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from .compare_with_ailang_main_helpers import *
except ImportError:
    from compare_with_ailang_main_helpers import *

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compare current AILang repo with AILang-main/prototype."
    )
    p.add_argument(
        "--old-prototype-root",
        type=Path,
        default=DEFAULT_OLD_PROTOTYPE_ROOT,
        help="Path to old AILang prototype root (contains ailang.py + ailang/ + verifier/).",
    )
    p.add_argument(
        "--new-entry",
        type=Path,
        default=DEFAULT_NEW_ENTRY,
        help="Current repo entrypoint path.",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUT_JSON,
        help="JSON output path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUT_MD,
        help="Markdown output path.",
    )
    p.add_argument("--runs", type=int, default=3, help="Benchmark measured runs.")
    p.add_argument("--warmup", type=int, default=1, help="Benchmark warmup runs.")
    p.add_argument(
        "--case",
        action="append",
        default=[],
        choices=[
            "loop_hash",
            "fib_mix",
            "file_io",
            "dict_ops",
            "records_bench",
            "fixed_array_sum",
            "slice_sum",
            "recursive_traversal",
        ],
        help="Optional benchmark case filter (repeatable).",
    )
    p.add_argument(
        "--impl",
        action="append",
        default=[],
        choices=["ailang_jit", "ailang_aot"],
        help="Optional benchmark implementation filter (repeatable).",
    )
    p.add_argument(
        "--no-check-leaks",
        action="store_true",
        help="Disable leak threshold checks for benchmark records.",
    )
    p.add_argument(
        "--leak-threshold",
        type=int,
        default=0,
        help="Leak threshold bytes for gate checks (default 0).",
    )
    p.add_argument(
        "--leak-probe-count",
        type=int,
        default=6,
        help="How many tests/corpus NN_*.ail programs to probe for C leak reports.",
    )
    p.add_argument(
        "--perf-ratio-threshold",
        type=float,
        default=1.10,
        help="Current-vs-old median ratio threshold to flag perf regression.",
    )
    p.add_argument(
        "--perf-abs-ms-threshold",
        type=float,
        default=15.0,
        help="Absolute median ms delta threshold to flag perf regression.",
    )
    p.add_argument(
        "--fail-on-perf-regression",
        action="store_true",
        help="Exit non-zero when perf regression gate finds issues.",
    )
    p.add_argument(
        "--fuzz-cases",
        type=int,
        default=0,
        help="Number of backend differential fuzz cases per version (0 disables).",
    )
    p.add_argument(
        "--fuzz-seed",
        type=int,
        default=1337,
        help="Random seed for fuzz program generation.",
    )
    p.add_argument(
        "--fail-on-fuzz-mismatch",
        action="store_true",
        help="Exit non-zero when differential fuzz finds mismatches.",
    )
    return p.parse_args()
def main() -> int:
    args = parse_args()

    old_root = args.old_prototype_root.resolve()
    old_entry = old_root / "ailang.py"
    new_entry = args.new_entry.resolve()

    if not old_entry.exists():
        print(f"old entrypoint not found: {old_entry}")
        return 1
    if not new_entry.exists():
        print(f"current entrypoint not found: {new_entry}")
        return 1

    shared_bench_files = sorted((REPO_ROOT / "benchmarks" / "ailang").glob("*.ail"))
    if not shared_bench_files:
        print("no benchmark .ail files found")
        return 1

    corpus_files = sorted((REPO_ROOT / "tests" / "corpus").glob("[0-9][0-9]_*.ail"))
    check_files = shared_bench_files[:]
    for c in corpus_files:
        if c.name not in {p.name for p in check_files}:
            check_files.append(c)

    selected_cases = set(args.case)
    selected_impls = args.impl[:] if args.impl else ["ailang_jit", "ailang_aot"]
    leak_checks_enabled = not args.no_check_leaks

    new_ver = _run_verifier_summary(
        [
            sys.executable,
            "-m",
            "verifier.cli",
            "-d",
            "source",
            "--preset",
            "strict",
            "--json",
        ],
        cwd=REPO_ROOT,
    )
    old_ver_pkg = _run_verifier_summary(
        [
            sys.executable,
            "verifier/cli.py",
            "-d",
            "ailang",
            "--preset",
            "strict",
            "--json",
        ],
        cwd=old_root,
    )
    old_ver_entry = _run_verifier_summary(
        [
            sys.executable,
            "verifier/cli.py",
            "ailang.py",
            "--preset",
            "strict",
            "--json",
        ],
        cwd=old_root,
    )

    new_checks = _check_programs(new_entry, check_files)
    old_checks = _check_programs(old_entry, check_files)

    probe_files = corpus_files[: max(0, args.leak_probe_count)]
    leak_probe_current = _run_c_backend_leak_probe(new_entry, probe_files, "current")
    leak_probe_old = _run_c_backend_leak_probe(old_entry, probe_files, "oldmain")

    sample = REPO_ROOT / "benchmarks" / "ailang" / "fib_mix.ail"
    new_flags = _flag_support(new_entry, sample)
    old_flags = _flag_support(old_entry, sample)

    bench_new = _run_ailang_benchmarks(
        entry=new_entry,
        label="current",
        runs=args.runs,
        warmup=args.warmup,
        selected_cases=selected_cases,
        selected_impls=selected_impls,
        check_leaks=leak_checks_enabled,
        leak_threshold=args.leak_threshold,
    )
    bench_old = _run_ailang_benchmarks(
        entry=old_entry,
        label="oldmain",
        runs=args.runs,
        warmup=args.warmup,
        selected_cases=selected_cases,
        selected_impls=selected_impls,
        check_leaks=leak_checks_enabled,
        leak_threshold=args.leak_threshold,
    )

    perf_issues = _perf_regressions(
        bench_new,
        bench_old,
        ratio_threshold=args.perf_ratio_threshold,
        abs_ms_threshold=args.perf_abs_ms_threshold,
    )
    leak_issues_current = _leak_over_budget(leak_probe_current, args.leak_threshold)
    leak_issues_old = _leak_over_budget(leak_probe_old, args.leak_threshold)

    fuzz_current: dict[str, Any] | None = None
    fuzz_old: dict[str, Any] | None = None
    if args.fuzz_cases > 0:
        fuzz_current = _run_backend_fuzz_diff(
            new_entry, "current", args.fuzz_cases, args.fuzz_seed
        )
        fuzz_old = _run_backend_fuzz_diff(
            old_entry, "oldmain", args.fuzz_cases, args.fuzz_seed
        )

    payload: dict[str, Any] = {
        "timestamp": time.strftime(DATE_HUMAN_FMT),
        "timestamp_iso": time.strftime(DATE_ISO_FMT),
        "config": {
            "runs": args.runs,
            "warmup": args.warmup,
            "selected_cases": sorted(selected_cases),
            "selected_impls": selected_impls,
            "check_leaks": leak_checks_enabled,
            "leak_threshold": args.leak_threshold,
            "leak_probe_count": args.leak_probe_count,
            "perf_ratio_threshold": args.perf_ratio_threshold,
            "perf_abs_ms_threshold": args.perf_abs_ms_threshold,
            "fuzz_cases": args.fuzz_cases,
            "fuzz_seed": args.fuzz_seed,
        },
        "paths": {
            "current_repo": str(REPO_ROOT),
            "old_prototype_root": str(old_root),
            "current_entry": str(new_entry),
            "old_entry": str(old_entry),
        },
        "coverage": {
            "current": {
                "verifier": new_ver,
                "py_files": _count_py_files(REPO_ROOT / "source"),
            },
            "old": {
                "verifier_pkg": old_ver_pkg,
                "verifier_entry": old_ver_entry,
                "py_files_pkg": _count_py_files(old_root / "ailang"),
            },
        },
        "feature_flags": {"current": new_flags, "old": old_flags},
        "check_mode": {"current": new_checks, "old": old_checks},
        "c_backend_leak_probe": {"current": leak_probe_current, "old": leak_probe_old},
        "benchmarks": {"current": bench_new, "old": bench_old},
        "gates": {
            "perf_regressions": perf_issues,
            "perf_pass": len(perf_issues) == 0,
            "leak_budget_issues_current": leak_issues_current,
            "leak_budget_issues_old": leak_issues_old,
            "leak_budget_pass": not leak_issues_current and not leak_issues_old,
            "fuzz_current": fuzz_current,
            "fuzz_old": fuzz_old,
            "fuzz_pass": (
                True
                if args.fuzz_cases <= 0
                else bool(
                    (fuzz_current is not None and fuzz_current.get("pass"))
                    and (fuzz_old is not None and fuzz_old.get("pass"))
                )
            ),
        },
    }

    out_json = args.output_json.resolve()
    out_md = args.output_md.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    cur_ok, cur_total = _check_ok_count(new_checks)
    old_ok, old_total = _check_ok_count(old_checks)
    md_lines = [
        "# AILang Version Comparison",
        "",
        f"- Generated: {payload['timestamp']}",
        f"- Current repo: `{REPO_ROOT}`",
        f"- Old repo: `{old_root}`",
        "",
        "## Coverage / Verification",
        "",
        f"- Current strict verifier: {new_ver['passed']}/{new_ver['total']} (exit {new_ver['exit_code']})",
        f"- Old strict verifier (ailang package): {old_ver_pkg['passed']}/{old_ver_pkg['total']} (exit {old_ver_pkg['exit_code']})",
        f"- Old strict verifier (entrypoint): {old_ver_entry['passed']}/{old_ver_entry['total']} (exit {old_ver_entry['exit_code']})",
        f"- Python files (current source): {payload['coverage']['current']['py_files']}",
        f"- Python files (old package): {payload['coverage']['old']['py_files_pkg']}",
        "",
        "## Check-Mode Stability (shared .ail files)",
        "",
        f"- Current `--check`: {cur_ok}/{cur_total} pass",
        f"- Old `--check`: {old_ok}/{old_total} pass",
        "",
        "## Feature Parity Signals",
        "",
        f"- `--jit-json` support (current): {new_flags['jit_json']}",
        f"- `--jit-json` support (old): {old_flags['jit_json']}",
        "",
        "## Performance (AILang only, shared benchmark corpus)",
        "",
    ]
    md_lines.extend(_summarize_perf_rows(bench_new, bench_old))
    md_lines.extend(
        [
            "",
            "## Performance Gate",
            "",
            f"- ratio threshold: `{args.perf_ratio_threshold}`",
            f"- absolute delta threshold: `{args.perf_abs_ms_threshold} ms`",
            f"- pass: `{payload['gates']['perf_pass']}`",
            f"- regressions: `{len(perf_issues)}`",
        ]
    )
    if perf_issues:
        md_lines.extend(
            [
                "",
                "| case | impl | current ms | old ms | delta ms | ratio |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for issue in perf_issues:
            md_lines.append(
                f"| {issue['case']} | {issue['impl']} | "
                f"{issue['current_median_ms']:.2f} | {issue['old_median_ms']:.2f} | "
                f"{issue['delta_ms']:+.2f} | {issue['ratio']:.2f}x |"
            )

    md_lines.extend(["", "## C Backend Leak Probe (shared corpus)", ""])
    md_lines.extend(
        [
            "| program | current compile/run | old compile/run | current live leak B | old live leak B |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for name in sorted(set(leak_probe_current) | set(leak_probe_old)):
        c = leak_probe_current.get(name, {})
        o = leak_probe_old.get(name, {})
        c_state = f"{c.get('compile_ok')}/{c.get('run_ok')}"
        o_state = f"{o.get('compile_ok')}/{o.get('run_ok')}"
        md_lines.append(
            f"| {name} | {c_state} | {o_state} | "
            f"{c.get('leak_live_bytes')} | {o.get('leak_live_bytes')} |"
        )
    md_lines.extend(
        [
            "",
            f"- leak budget pass: `{payload['gates']['leak_budget_pass']}`",
            f"- current leak over-budget items: `{len(leak_issues_current)}`",
            f"- old leak over-budget items: `{len(leak_issues_old)}`",
        ]
    )

    md_lines.extend(["", "## Differential Fuzz (LLVM vs C backend)", ""])
    if args.fuzz_cases <= 0:
        md_lines.append("- disabled (`--fuzz-cases 0`)")
    else:
        md_lines.append(f"- cases: `{args.fuzz_cases}`, seed: `{args.fuzz_seed}`")
        md_lines.append(f"- pass: `{payload['gates']['fuzz_pass']}`")
        if fuzz_current is not None and fuzz_old is not None:
            md_lines.append(
                f"- current mismatches: `{len(fuzz_current.get('mismatches', []))}`"
            )
            md_lines.append(
                f"- old mismatches: `{len(fuzz_old.get('mismatches', []))}`"
            )

    md_lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- JSON: `{out_json}`",
            f"- Markdown: `{out_md}`",
        ]
    )
    out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"wrote: {out_json}")
    print(f"wrote: {out_md}")

    if args.fail_on_perf_regression and perf_issues:
        print("perf regression gate failed")
        return 2
    if (
        args.fail_on_fuzz_mismatch
        and args.fuzz_cases > 0
        and not payload["gates"]["fuzz_pass"]
    ):
        print("fuzz gate failed")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

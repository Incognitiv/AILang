#!/usr/bin/env python3
"""
Session change-cycle harness: enforce before/after routine snapshots.

Workflow:
  1) start  -> capture BEFORE snapshot (full stabilization routine)
  2) finish -> capture AFTER snapshot, run compare, and write delta summary

This keeps a strict discipline for each dev session:
  - comparison before changes
  - comparison after changes
  - leak/perf/reliability deltas with short causal hints
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_ROOT = REPO_ROOT / "benchmarks" / "sessions"
STATE_ROOT = SESSION_ROOT / "session_cycles"
STABILIZATION_TOOL = REPO_ROOT / "tools" / "stabilization_routine.py"
SESSION_TOOL = REPO_ROOT / "tools" / "session_benchmark.py"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run(cmd: list[str], timeout: int = 7200) -> int:
    print("$ " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        timeout=timeout,
    )
    return int(proc.returncode)


def _median(values: Any) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    nums: list[float] = []
    for v in values:
        if isinstance(v, (int, float)):
            nums.append(float(v))
    if not nums:
        return None
    return float(statistics.median(nums))


def _state_path(name: str) -> Path:
    return STATE_ROOT / f"{name}.json"


def _default_compare_output(before_label: str, after_label: str) -> Path:
    return SESSION_ROOT / f"compare_{before_label}_vs_{after_label}.md"


def _build_delta_summary(
    *,
    cycle_name: str,
    before_label: str,
    after_label: str,
    compare_output: Path,
    summary_output: Path,
    perf_ratio_threshold: float,
    perf_abs_ms_threshold: float,
) -> None:
    before_manifest = _read_json(SESSION_ROOT / before_label / "session.json")
    after_manifest = _read_json(SESSION_ROOT / after_label / "session.json")

    before_bench = _read_json(Path(before_manifest["paths"]["benchmark_json"]))
    after_bench = _read_json(Path(after_manifest["paths"]["benchmark_json"]))
    before_reg = _read_json(Path(before_manifest["paths"]["regression_json"]))
    after_reg = _read_json(Path(after_manifest["paths"]["regression_json"]))

    perf_issues: list[dict[str, Any]] = []
    b_results = before_bench.get("results", {})
    a_results = after_bench.get("results", {})
    for case in sorted(set(b_results) & set(a_results)):
        b_case = b_results.get(case, {})
        a_case = a_results.get(case, {})
        for impl in sorted(set(b_case) & set(a_case)):
            b_rec = b_case.get(impl, {})
            a_rec = a_case.get(impl, {})
            b_med = _median(b_rec.get("runs_ms"))
            a_med = _median(a_rec.get("runs_ms"))
            if b_med is None or a_med is None or b_med <= 0:
                continue
            delta_ms = a_med - b_med
            ratio = a_med / b_med
            if (
                ratio >= perf_ratio_threshold
                and delta_ms >= perf_abs_ms_threshold
                and b_rec.get("status") == "ok"
                and a_rec.get("status") == "ok"
            ):
                perf_issues.append(
                    {
                        "case": case,
                        "impl": impl,
                        "before_ms": b_med,
                        "after_ms": a_med,
                        "delta_ms": delta_ms,
                        "ratio": ratio,
                    }
                )

    leak_issues: list[dict[str, Any]] = []
    b_prog = {p.get("name"): p for p in before_reg.get("programs", []) if p.get("name")}
    a_prog = {p.get("name"): p for p in after_reg.get("programs", []) if p.get("name")}
    for name in sorted(set(b_prog) & set(a_prog)):
        for backend in ("llvm", "c"):
            b_back = b_prog[name].get(backend) or {}
            a_back = a_prog[name].get(backend) or {}
            b_live = b_back.get("leak_live_bytes")
            a_live = a_back.get("leak_live_bytes")
            if isinstance(b_live, int) and isinstance(a_live, int) and a_live > b_live:
                alloc = a_back.get("leak_alloc_bytes")
                freed = a_back.get("leak_freed_bytes")
                reason = (
                    f"live bytes increased ({b_live} -> {a_live}); "
                    f"allocated={alloc}, freed={freed}"
                )
                leak_issues.append(
                    {
                        "program": name,
                        "backend": backend,
                        "before_live": b_live,
                        "after_live": a_live,
                        "reason": reason,
                    }
                )

    reliability_issues: list[dict[str, Any]] = []
    for name in sorted(set(b_prog) & set(a_prog)):
        for backend in ("llvm", "c"):
            b_back = b_prog[name].get(backend) or {}
            a_back = a_prog[name].get(backend) or {}
            b_compile_ok = bool(b_back.get("compile_ok"))
            a_compile_ok = bool(a_back.get("compile_ok"))
            b_runtime_ok = bool(b_back.get("runtime_ok"))
            a_runtime_ok = bool(a_back.get("runtime_ok"))
            if b_compile_ok and not a_compile_ok:
                reliability_issues.append(
                    {
                        "program": name,
                        "backend": backend,
                        "kind": "compile_regression",
                        "reason": "compile_ok changed True -> False",
                    }
                )
            if b_runtime_ok and not a_runtime_ok:
                reliability_issues.append(
                    {
                        "program": name,
                        "backend": backend,
                        "kind": "runtime_regression",
                        "reason": "runtime_ok changed True -> False",
                    }
                )

    lines: list[str] = []
    lines.append(f"# Session Cycle Summary: `{cycle_name}`")
    lines.append("")
    lines.append(f"- Generated: {time.strftime(DATE_HUMAN_FMT)}")
    lines.append(f"- Before: `{before_label}`")
    lines.append(f"- After: `{after_label}`")
    lines.append(f"- Full compare: `{compare_output}`")
    lines.append("")
    lines.append("## Gate Exits")
    lines.append("")
    lines.append(
        f"- before: benchmark={before_manifest.get('exit_codes', {}).get('benchmark')}, "
        f"regression={before_manifest.get('exit_codes', {}).get('regression')}, "
        f"verifier={before_manifest.get('exit_codes', {}).get('verifier')}, "
        f"god-object={before_manifest.get('exit_codes', {}).get('god_object_audit')}"
    )
    lines.append(
        f"- after: benchmark={after_manifest.get('exit_codes', {}).get('benchmark')}, "
        f"regression={after_manifest.get('exit_codes', {}).get('regression')}, "
        f"verifier={after_manifest.get('exit_codes', {}).get('verifier')}, "
        f"god-object={after_manifest.get('exit_codes', {}).get('god_object_audit')}"
    )
    lines.append("")

    lines.append("## Performance Regressions")
    lines.append("")
    if not perf_issues:
        lines.append("- none")
    else:
        lines.append("| case | impl | before ms | after ms | delta ms | ratio |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for row in perf_issues:
            lines.append(
                f"| {row['case']} | {row['impl']} | {row['before_ms']:.3f} | "
                f"{row['after_ms']:.3f} | {row['delta_ms']:+.3f} | {row['ratio']:.2f}x |"
            )
    lines.append("")

    lines.append("## Leak Regressions and Why")
    lines.append("")
    if not leak_issues:
        lines.append("- none")
    else:
        lines.append("| program | backend | before live B | after live B | why |")
        lines.append("| --- | --- | ---: | ---: | --- |")
        for row in leak_issues:
            lines.append(
                f"| {row['program']} | {row['backend']} | {row['before_live']} | "
                f"{row['after_live']} | {row['reason']} |"
            )
    lines.append("")

    lines.append("## Reliability Regressions")
    lines.append("")
    if not reliability_issues:
        lines.append("- none")
    else:
        lines.append("| program | backend | kind | why |")
        lines.append("| --- | --- | --- | --- |")
        for row in reliability_issues:
            lines.append(
                f"| {row['program']} | {row['backend']} | {row['kind']} | {row['reason']} |"
            )
    lines.append("")
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _common_parser(name_required: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--name",
        required=name_required,
        help="Cycle name, used for labels and state file name.",
    )
    parser.add_argument(
        "--routine-arg",
        action="append",
        default=[],
        help="Raw argument forwarded to tools/stabilization_routine.py (repeatable).",
    )
    parser.add_argument(
        "--perf-ratio-threshold",
        type=float,
        default=1.05,
        help="Ratio threshold for performance regression summary (default: 1.05).",
    )
    parser.add_argument(
        "--perf-abs-ms-threshold",
        type=float,
        default=5.0,
        help="Absolute ms threshold for performance regression summary (default: 5ms).",
    )
    return parser


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Before/after session cycle for routine benchmarking and safety."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser(
        "start",
        parents=[_common_parser(name_required=True)],
        help="Capture BEFORE baseline snapshot for this cycle.",
    )
    start.add_argument(
        "--before-label",
        default=None,
        help="Optional explicit before label (default: <name>_before_<timestamp>).",
    )

    finish = sub.add_parser(
        "finish",
        parents=[_common_parser(name_required=True)],
        help="Capture AFTER snapshot, compare to BEFORE, and write delta summary.",
    )
    finish.add_argument(
        "--after-label",
        default=None,
        help="Optional explicit after label (default: <name>_after_<timestamp>).",
    )
    finish.add_argument(
        "--compare-output",
        default=None,
        help="Optional compare markdown output path.",
    )
    finish.add_argument(
        "--summary-output",
        default=None,
        help="Optional cycle summary markdown output path.",
    )
    return p.parse_args()


def _start_cycle(args: argparse.Namespace) -> int:
    name = args.name
    state_path = _state_path(name)
    if state_path.exists():
        state = _read_json(state_path)
        if state.get("status") == "started":
            print(
                f"cycle '{name}' already started (before={state.get('before_label')}). "
                "Run finish first or remove state file."
            )
            return 1

    before_label = (
        args.before_label or f"{name}_before_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    cmd = [
        sys.executable,
        str(STABILIZATION_TOOL),
        "--label",
        before_label,
    ]
    for raw in args.routine_arg:
        if raw:
            cmd.append(raw)
    rc = _run(cmd, timeout=10800)
    if rc != 0:
        print(f"before snapshot failed (exit={rc})")
        return rc

    payload = {
        "name": name,
        "status": "started",
        "started_human": time.strftime(DATE_HUMAN_FMT),
        "before_label": before_label,
        "routine_args": list(args.routine_arg),
    }
    _write_json(state_path, payload)
    print(f"cycle started: {name}")
    print(f"before label: {before_label}")
    print(f"state file: {state_path}")
    return 0


def _finish_cycle(args: argparse.Namespace) -> int:
    name = args.name
    state_path = _state_path(name)
    if not state_path.exists():
        print(f"no active state for cycle '{name}': {state_path}")
        return 1
    state = _read_json(state_path)
    if state.get("status") != "started":
        print(f"cycle '{name}' is not in started state")
        return 1

    before_label = str(state.get("before_label", "")).strip()
    if not before_label:
        print(f"cycle '{name}' state missing before_label")
        return 1
    after_label = args.after_label or f"{name}_after_{time.strftime('%Y%m%d_%H%M%S')}"

    routine_args = list(args.routine_arg or state.get("routine_args", []))
    cmd = [
        sys.executable,
        str(STABILIZATION_TOOL),
        "--label",
        after_label,
    ]
    for raw in routine_args:
        if raw:
            cmd.append(raw)
    rc = _run(cmd, timeout=10800)
    if rc != 0:
        print(f"after snapshot failed (exit={rc})")
        return rc

    compare_output = (
        Path(args.compare_output).resolve()
        if args.compare_output
        else _default_compare_output(before_label, after_label)
    )
    cmp_cmd = [
        sys.executable,
        str(SESSION_TOOL),
        "compare",
        "--before",
        before_label,
        "--after",
        after_label,
        "--output",
        str(compare_output),
    ]
    cmp_rc = _run(cmp_cmd, timeout=1800)
    if cmp_rc != 0:
        print(f"compare failed (exit={cmp_rc})")
        return cmp_rc

    summary_output = (
        Path(args.summary_output).resolve()
        if args.summary_output
        else SESSION_ROOT / f"cycle_{name}_summary.md"
    )
    _build_delta_summary(
        cycle_name=name,
        before_label=before_label,
        after_label=after_label,
        compare_output=compare_output,
        summary_output=summary_output,
        perf_ratio_threshold=args.perf_ratio_threshold,
        perf_abs_ms_threshold=args.perf_abs_ms_threshold,
    )

    state["status"] = "finished"
    state["finished_human"] = time.strftime(DATE_HUMAN_FMT)
    state["after_label"] = after_label
    state["compare_output"] = str(compare_output)
    state["summary_output"] = str(summary_output)
    _write_json(state_path, state)

    print(f"cycle finished: {name}")
    print(f"before: {before_label}")
    print(f"after:  {after_label}")
    print(f"compare: {compare_output}")
    print(f"summary: {summary_output}")
    return 0


def main() -> int:
    args = parse_args()
    if args.cmd == "start":
        return _start_cycle(args)
    return _finish_cycle(args)


if __name__ == "__main__":
    raise SystemExit(main())

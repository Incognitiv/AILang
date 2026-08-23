#!/usr/bin/env python3
"""Capture compiler phase timings across AILang benchmark sources."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_JSON = REPO_ROOT / "benchmarks" / "results" / "compile_phase_profile.json"
DEFAULT_OUT_MD = REPO_ROOT / "benchmarks" / "results" / "compile_phase_profile.md"
DEFAULT_SOURCES_ROOT = REPO_ROOT / "benchmarks" / "ailang"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"

PHASE_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.:-]+)\s+(?P<ms>\d+(?:\.\d+)?)\s*ms\s*$"
)
TOTAL_ROW_RE = re.compile(r"^\s*total\s+(?P<ms>\d+(?:\.\d+)?)\s*ms\s*$")


@dataclass
class PhaseRun:
    source: str
    backend: str
    command: list[str]
    exit_code: int
    elapsed_ms: float
    total_phase_ms: float | None
    phases: dict[str, float]
    error_head: str


def _run(
    cmd: list[str],
    timeout: int = 1200,
) -> tuple[int, str, str, float]:
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return proc.returncode, proc.stdout, proc.stderr, elapsed_ms


def _parse_phase_report(text: str) -> tuple[dict[str, float], float | None]:
    lines = text.splitlines()
    try:
        start_idx = lines.index("AILang compile profile:")
    except ValueError:
        return {}, None

    phases: dict[str, float] = {}
    total: float | None = None
    for line in lines[start_idx + 1 :]:
        stripped = line.rstrip()
        if not stripped:
            continue
        m_total = TOTAL_ROW_RE.match(stripped)
        if m_total:
            total = float(m_total.group("ms"))
            continue
        m_row = PHASE_ROW_RE.match(stripped)
        if not m_row:
            continue
        name = m_row.group("name")
        if name == "total":
            total = float(m_row.group("ms"))
            continue
        phases[name] = float(m_row.group("ms"))
    return phases, total


def _default_sources() -> list[Path]:
    return sorted(DEFAULT_SOURCES_ROOT.glob("*.ail"))


def _run_profile_once(
    source: Path,
    backend: str,
    opt_level: int,
    out_dir: Path,
    timeout: int,
) -> PhaseRun:
    if backend == "c":
        exe_path = out_dir / f"{source.stem}_phase.exe"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(source),
            "--backend=c",
            "-o",
            str(exe_path),
            f"-O{opt_level}",
            "--profile-phases",
        ]
    else:
        exe_path = out_dir / f"{source.stem}_phase_llvm.exe"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(source),
            "-o",
            str(exe_path),
            f"-O{opt_level}",
            "--profile-phases",
        ]

    rc, stdout, stderr, elapsed_ms = _run(cmd, timeout=timeout)
    phases, total_ms = _parse_phase_report(stdout + "\n" + stderr)
    return PhaseRun(
        source=str(source),
        backend=backend,
        command=cmd,
        exit_code=rc,
        elapsed_ms=elapsed_ms,
        total_phase_ms=total_ms,
        phases=phases,
        error_head="\n".join((stderr or "").strip().splitlines()[:4]),
    )


def _aggregate(runs: list[PhaseRun]) -> dict[str, Any]:
    phase_samples: dict[str, list[float]] = {}
    for run in runs:
        for phase, ms in run.phases.items():
            phase_samples.setdefault(phase, []).append(ms)

    phase_stats: list[dict[str, Any]] = []
    for phase, samples in phase_samples.items():
        phase_stats.append(
            {
                "phase": phase,
                "count": len(samples),
                "mean_ms": round(statistics.mean(samples), 4),
                "median_ms": round(statistics.median(samples), 4),
                "max_ms": round(max(samples), 4),
                "sum_ms": round(sum(samples), 4),
            }
        )
    phase_stats.sort(key=lambda x: float(x["sum_ms"]), reverse=True)

    totals = [
        r.total_phase_ms for r in runs if isinstance(r.total_phase_ms, (int, float))
    ]
    wall = [r.elapsed_ms for r in runs]
    return {
        "run_count": len(runs),
        "total_phase_mean_ms": round(statistics.mean(totals), 4) if totals else None,
        "total_phase_median_ms": (
            round(statistics.median(totals), 4) if totals else None
        ),
        "wall_mean_ms": round(statistics.mean(wall), 4) if wall else None,
        "wall_median_ms": round(statistics.median(wall), 4) if wall else None,
        "phase_stats": phase_stats,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_md(path: Path, payload: dict[str, Any]) -> None:
    agg = payload["aggregate"]
    rows = agg.get("phase_stats", [])
    lines = [
        "# Compiler Phase Profile",
        "",
        f"- Generated: {payload['timestamp_human']}",
        f"- Backend: `{payload['backend']}`",
        f"- Sources profiled: `{len(payload['sources'])}`",
        f"- Runs: `{payload['run_count']}`",
        f"- Total phase mean: `{agg.get('total_phase_mean_ms')} ms`",
        f"- Total wall mean: `{agg.get('wall_mean_ms')} ms`",
        "",
        "## Top Phases (by cumulative ms)",
        "",
        "| phase | samples | mean ms | median ms | max ms | sum ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:20]:
        lines.append(
            f"| {row['phase']} | {row['count']} | {row['mean_ms']:.3f} | "
            f"{row['median_ms']:.3f} | {row['max_ms']:.3f} | {row['sum_ms']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Per-Source Totals",
            "",
            "| source | exit | phase total ms | wall ms |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for run in payload["runs"]:
        lines.append(
            f"| {run['source']} | {run['exit_code']} | "
            f"{run.get('total_phase_ms')} | {run['elapsed_ms']:.3f} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Profile AILang compiler phases.")
    p.add_argument(
        "--backend",
        choices=["c", "llvm"],
        default="c",
        help="Backend compile path to profile (default: c).",
    )
    p.add_argument(
        "--source",
        action="append",
        default=[],
        help="Source file to profile (repeatable). Defaults to benchmarks/ailang/*.ail",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUT_JSON,
        help="JSON report output path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUT_MD,
        help="Markdown report output path.",
    )
    p.add_argument(
        "--opt-level",
        type=int,
        default=3,
        choices=[0, 1, 2, 3],
        help="Compilation optimization level passed to ailang.py.",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="Per-source timeout in seconds.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    sources = (
        [Path(s).resolve() for s in args.source] if args.source else _default_sources()
    )
    if not sources:
        print("no sources selected")
        return 1
    for source in sources:
        if not source.exists():
            print(f"source not found: {source}")
            return 1

    out_dir = REPO_ROOT / "out" / "phase_profile" / args.backend
    out_dir.mkdir(parents=True, exist_ok=True)

    runs: list[PhaseRun] = []
    for source in sources:
        run = _run_profile_once(
            source=source,
            backend=args.backend,
            opt_level=args.opt_level,
            out_dir=out_dir,
            timeout=args.timeout,
        )
        runs.append(run)
        print(
            f"[phase-profile] {source.name}: exit={run.exit_code}, "
            f"total_phase_ms={run.total_phase_ms}, wall_ms={run.elapsed_ms:.2f}"
        )

    payload: dict[str, Any] = {
        "timestamp_human": time.strftime(DATE_HUMAN_FMT),
        "backend": args.backend,
        "opt_level": args.opt_level,
        "run_count": len(runs),
        "sources": [str(s) for s in sources],
        "runs": [
            {
                "source": r.source,
                "backend": r.backend,
                "command": r.command,
                "exit_code": r.exit_code,
                "elapsed_ms": round(r.elapsed_ms, 4),
                "total_phase_ms": r.total_phase_ms,
                "phases": r.phases,
                "error_head": r.error_head,
            }
            for r in runs
        ],
        "aggregate": _aggregate(runs),
    }
    _write_json(args.output_json.resolve(), payload)
    _write_md(args.output_md.resolve(), payload)

    failed = [r for r in runs if r.exit_code != 0]
    if failed:
        print(f"phase profiling failed for {len(failed)} source(s)")
        return 1
    print(f"json: {args.output_json}")
    print(f"md: {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

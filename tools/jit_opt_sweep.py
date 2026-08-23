#!/usr/bin/env python3
"""Sweep JIT optimization levels and write a small timing report."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKER = "JIT_WARM_RESULT="


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _run_opt(
    source: Path,
    *,
    opt: int,
    runs: int,
    warmup: int,
    dump_dir: Path | None,
    timeout_s: int,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "ailang.py"),
        str(source),
        "--jit-json",
        "--jit-opt",
        str(opt),
        "--jit-repeat",
        str(max(1, runs)),
        "--jit-warmup",
        str(max(0, warmup)),
    ]
    if dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / f"{source.stem}_jit_o{opt}.ll"
        cmd.extend(["--jit-dump-ir", str(dump_path)])

    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
    )
    payload: dict[str, Any] | None = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(MARKER):
            payload = json.loads(line[len(MARKER) :].strip())
            break
    if payload is None:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-8:])
        payload = {
            "status": "fail",
            "jit_opt": opt,
            "compile_ms": None,
            "runs_ms": [],
            "checksum": None,
            "note": f"missing JIT JSON marker; exit={proc.returncode}; tail={tail}",
        }
    payload["exit_code"] = proc.returncode
    return payload


def _write_markdown(source: Path, rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    lines = [
        "# JIT Optimization Sweep",
        "",
        f"- Generated: {generated}",
        f"- Source: `{source}`",
        "",
        "| JIT opt | Status | Compile ms | Median run ms | Runs | Note |",
        "|---:|---|---:|---:|---:|---|",
    ]
    best: tuple[int, float] | None = None
    for row in rows:
        opt = int(row.get("jit_opt", -1))
        runs = [float(v) for v in row.get("runs_ms", [])]
        med = _median(runs)
        if med is not None and row.get("status") == "ok":
            if best is None or med < best[1]:
                best = (opt, med)
        compile_ms = row.get("compile_ms")
        compile_txt = "n/a" if compile_ms is None else f"{float(compile_ms):.3f}"
        med_txt = "n/a" if med is None else f"{med:.3f}"
        note = str(row.get("note") or "").replace("|", "\\|")
        lines.append(
            f"| O{opt} | {row.get('status')} | {compile_txt} | "
            f"{med_txt} | {len(runs)} | {note} |"
        )
    lines.append("")
    if best is None:
        lines.append("Recommendation: no successful measured JIT runs.")
    else:
        lines.append(
            f"Recommendation: `--jit-opt={best[0]}` had the lowest measured runtime "
            f"({best[1]:.3f} ms median)."
        )
    lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--dump-dir",
        type=Path,
        default=None,
        help="Optional directory for optimized per-O-level JIT IR dumps.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "jit_opt_sweep.md",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "jit_opt_sweep.json",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    rows = [
        _run_opt(
            source,
            opt=opt,
            runs=max(1, args.runs),
            warmup=max(0, args.warmup),
            dump_dir=args.dump_dir.resolve() if args.dump_dir else None,
            timeout_s=max(30, args.timeout),
        )
        for opt in range(4)
    ]
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    _write_markdown(source, rows, args.output)
    print(f"jit opt sweep md: {args.output}")
    print(f"jit opt sweep json: {args.json_output}")
    return 0 if all(row.get("status") == "ok" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

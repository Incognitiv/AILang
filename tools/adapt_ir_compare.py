#!/usr/bin/env python3
"""Compare ADAPT hot SQLite helper calls in emitted AOT IR and JIT IR."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADAPT_BENCH = Path(
    os.getenv("AILANG_ADAPT_BENCH", "../ADAPT/tests/bench_sqlite_storage.ail")
)
MARKER = "JIT_WARM_RESULT="

HOT_SYMBOLS = [
    "sqlite3_prepare_v2",
    "sqlite3_exec",
    "sqlite3_bind_int64",
    "sqlite3_bind_text",
    "sqlite3_bind_null",
    "sqlite3_clear_bindings",
    "sqlite3_step",
    "sqlite3_reset",
    "sqlite3_finalize",
    "sqlite3_column_int64",
    "sqlite3_column_text",
    "snprintf",
    "_snprintf",
    "malloc",
    "free",
    "ailang_request_alloc",
    "ailang_int_to_str",
    "ailang_str_concat",
]


def _run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _extract_jit_payload(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :].strip())
    return {"status": "fail", "note": "missing JIT_WARM_RESULT"}


def _count_symbols(ir_path: Path) -> dict[str, int]:
    text = ir_path.read_text(encoding="utf-8", errors="replace")
    counts: dict[str, int] = {}
    for sym in HOT_SYMBOLS:
        counts[sym] = len(re.findall(rf"@\"?{re.escape(sym)}\"?\(", text))
    counts["total_calls"] = len(re.findall(r"\bcall\b", text))
    counts["total_lines"] = len(text.splitlines())
    return counts


def _write_report(
    source: Path,
    aot_ir: Path,
    jit_ir: Path,
    aot_counts: dict[str, int],
    jit_counts: dict[str, int],
    jit_payload: dict[str, Any],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    lines = [
        "# ADAPT JIT vs AOT IR Comparison",
        "",
        f"- Generated: {generated}",
        f"- Source: `{source}`",
        f"- AOT IR: `{aot_ir}`",
        f"- JIT IR: `{jit_ir}`",
        f"- JIT status: `{jit_payload.get('status')}`",
        f"- JIT compile ms: `{jit_payload.get('compile_ms')}`",
        f"- JIT runs ms: `{jit_payload.get('runs_ms')}`",
        "",
        "| Symbol | AOT IR refs | JIT IR refs | Delta JIT-AOT |",
        "|---|---:|---:|---:|",
    ]
    keys = [*HOT_SYMBOLS, "total_calls", "total_lines"]
    for key in keys:
        aot = int(aot_counts.get(key, 0))
        jit = int(jit_counts.get(key, 0))
        lines.append(f"| `{key}` | {aot} | {jit} | {jit - aot} |")
    lines.extend(
        [
            "",
            "Interpretation:",
            "- `snprintf` / `_snprintf` should stay at zero for fixed-shape SQLite binds.",
            "- Similar SQLite call counts mean remaining gaps are likely runtime mode, helper boundary, or SQLite/libc behavior, not missing scalar optimization.",
            "- Higher allocation/string helper counts identify places where AILang is still building temporary values instead of writing directly to SQLite.",
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_ADAPT_BENCH)
    parser.add_argument("--jit-opt", type=int, default=1)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO_ROOT / "out" / "generated" / "adapt_ir_compare",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "adapt_ir_compare.md",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "adapt_ir_compare.json",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    aot_ir = work_dir / "adapt_aot_emit.ll"
    jit_ir = work_dir / f"adapt_jit_o{max(0, min(3, args.jit_opt))}.ll"

    emit = _run(
        [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(source),
            "--emit-llvm",
            "-o",
            str(aot_ir),
        ],
        timeout=180,
    )
    if emit.returncode != 0:
        sys.stderr.write(emit.stdout + emit.stderr)
        return emit.returncode

    jit = _run(
        [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(source),
            "--jit-json",
            "--jit-opt",
            str(max(0, min(3, args.jit_opt))),
            "--jit-dump-ir",
            str(jit_ir),
        ],
        timeout=300,
    )
    if jit.returncode != 0:
        sys.stderr.write(jit.stdout + jit.stderr)
        return jit.returncode
    payload = _extract_jit_payload(jit.stdout)
    if payload.get("status") != "ok":
        sys.stderr.write(jit.stdout + jit.stderr)
        return 1

    aot_counts = _count_symbols(aot_ir)
    jit_counts = _count_symbols(jit_ir)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(
            {
                "source": str(source),
                "aot_ir": str(aot_ir),
                "jit_ir": str(jit_ir),
                "jit_payload": payload,
                "aot_counts": aot_counts,
                "jit_counts": jit_counts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_report(source, aot_ir, jit_ir, aot_counts, jit_counts, payload, args.output)
    print(f"adapt ir compare md: {args.output}")
    print(f"adapt ir compare json: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Durability stress harness for a large mixed-feature AILang program."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"

DEFAULT_SOURCE = REPO_ROOT / "benchmarks" / "ailang" / "durability_mega.ail"
DEFAULT_REPORT_JSON = REPO_ROOT / "benchmarks" / "results" / "durability_stress.json"
DEFAULT_REPORT_MD = REPO_ROOT / "benchmarks" / "results" / "durability_stress.md"
DEFAULT_BASELINE = (
    REPO_ROOT / "benchmarks" / "results" / "durability_stress_baseline.json"
)

LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)


@dataclass
class BackendDurability:
    backend: str
    compile_ok: bool
    compile_exit: int
    compile_ms: float
    runtime_ok: bool
    runtime_exit: int
    runtime_ms: float
    closed_properly: bool
    crash_suspected: bool
    stdout_head: str
    stderr_head: str
    loops: int | None
    checksum: int | None
    leak_alloc_bytes: int | None = None
    leak_freed_bytes: int | None = None
    leak_live_bytes: int | None = None


def _run(
    cmd: list[str],
    timeout_s: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, float]:
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=env,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return proc.returncode, proc.stdout, proc.stderr, elapsed_ms


def _first_lines(text: str, count: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[:count])


def _parse_leak(blob: str) -> tuple[int, int, int] | None:
    m = LEAK_RE.search(blob)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _parse_loops_and_checksum(stdout: str) -> tuple[int | None, int | None]:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if "DURABILITY_OK" not in lines:
        return None, None
    idx = lines.index("DURABILITY_OK")
    tail = lines[idx + 1 :]
    numeric = []
    for ln in tail:
        try:
            numeric.append(int(ln))
        except ValueError:
            continue
    if len(numeric) >= 2:
        return numeric[0], numeric[1]
    return None, None


def _load_inventory() -> tuple[list[str], list[str]]:
    source_root = str(REPO_ROOT / "source")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from diagnostics.diagnostics_catalog import LANGUAGE_SURFACE
    from lexer.scan import TOKEN_PATTERNS

    token_types = sorted({token_type for token_type, _pattern in TOKEN_PATTERNS})
    builtins = sorted({str(name) for name in LANGUAGE_SURFACE})
    return token_types, builtins


def _build_mega_program(
    duration_ms: int, token_types: list[str], builtins: list[str]
) -> str:
    token_header = "\n".join(f"// token:{name}" for name in token_types)
    builtin_header = "\n".join(f"// builtin:{name}" for name in builtins)
    return f"""// Auto-generated durability mega program.
// Duration target: {duration_ms} ms.
// Inventory dump so one source contains full token/builtin names.
{token_header}
{builtin_header}

record Pair then
    int a
    int b
end

def stress_math(limit): int
    i = 0
    acc = 0
    while i < limit then
        acc = acc + ((i * 3) % 97)
        if (i % 9) == 0 then
            acc = acc + 1
        else
            acc = acc - 1
        end
        i = i + 1
    end
    return acc
end

def stress_string(limit): int
    i = 0
    acc = 0
    s = "alpha"
    while i < limit then
        s = s + "x"
        l = strlen(s)
        acc = acc + l
        if l > 32 then
            s = substr(s, 0, 5)
        end
        i = i + 1
    end
    return acc
end

def stress_dict(limit): int
    d = {{"a": 1, "b": 2, "c": 3, "d": 4}}
    i = 0
    acc = 0
    while i < limit then
        d["a"] = (d["a"] + d["b"]) % 1000003
        d["b"] = (d["b"] + d["c"]) % 1000003
        d["c"] = (d["c"] + d["d"]) % 1000003
        d["d"] = (d["d"] + 1) % 1000003
        acc = acc + d["a"] + d["b"] + d["c"] + d["d"]
        i = i + 1
    end
    return acc
end

def stress_record(limit): int
    p = new Pair(1, 2)
    i = 0
    acc = 0
    while i < limit then
        p.a = (p.a + p.b) % 1000003
        p.b = (p.b + 2) % 1000003
        acc = acc + p.a + p.b
        i = i + 1
    end
    return acc
end

def stress_file(limit): int
    path = "benchmarks/out/durability_io.txt"
    payload = "abcdefghijklmnopqrstuvwxyz0123456789"
    i = 0
    acc = 0
    while i < limit then
        if write_file(path, payload) == 1 then
            content = read_file(path)
            acc = acc + len(content)
        end
        i = i + 1
    end
    return acc
end

def main(): int
    duration_ms = {duration_ms}
    start = time_ms()
    loops = 0
    checksum = 0
    while time_ms() - start < duration_ms then
        checksum = checksum + stress_math(3000)
        checksum = checksum + stress_string(400)
        checksum = checksum + stress_dict(600)
        checksum = checksum + stress_record(900)
        checksum = checksum + stress_file(3)
        loops = loops + 1
    end
    print("DURABILITY_OK")
    print(loops)
    print(checksum)
    return 0
end
"""


def _compile_and_run(
    source: Path,
    backend: str,
    duration_ms: int,
    timeout_buffer_s: int = 20,
) -> BackendDurability:
    out_dir = REPO_ROOT / "out" / "durability"
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / f"durability_mega_{backend}.exe"

    compile_cmd = [sys.executable, str(REPO_ROOT / "ailang.py"), str(source)]
    if backend == "c":
        compile_cmd.append("--backend=c")
    compile_cmd.extend(["-o", str(exe)])

    cc, cc_out, cc_err, cc_ms = _run(compile_cmd, timeout_s=1200)
    compile_ok = cc == 0 and exe.exists()
    if not compile_ok:
        return BackendDurability(
            backend=backend,
            compile_ok=False,
            compile_exit=cc,
            compile_ms=round(cc_ms, 3),
            runtime_ok=False,
            runtime_exit=-1,
            runtime_ms=0.0,
            closed_properly=False,
            crash_suspected=False,
            stdout_head=_first_lines(cc_out),
            stderr_head=_first_lines(cc_err),
            loops=None,
            checksum=None,
        )

    run_env = os.environ.copy()
    if backend == "c":
        run_env["AILANG_LEAK_REPORT"] = "1"
    timeout_s = max(15, int(duration_ms / 1000) + timeout_buffer_s)
    rc, run_out, run_err, run_ms = _run([str(exe)], timeout_s=timeout_s, env=run_env)

    loops, checksum = _parse_loops_and_checksum(run_out)
    closed_properly = rc == 0 and ("DURABILITY_OK" in run_out)
    leak_alloc = leak_freed = leak_live = None
    if backend == "c":
        leak = _parse_leak((run_out or "") + "\n" + (run_err or ""))
        if leak is not None:
            leak_alloc, leak_freed, leak_live = leak

    return BackendDurability(
        backend=backend,
        compile_ok=True,
        compile_exit=cc,
        compile_ms=round(cc_ms, 3),
        runtime_ok=rc == 0,
        runtime_exit=rc,
        runtime_ms=round(run_ms, 3),
        closed_properly=closed_properly,
        crash_suspected=bool(rc < 0 or rc in {139, 3221225477, 3221226505}),
        stdout_head=_first_lines(run_out),
        stderr_head=_first_lines(run_err),
        loops=loops,
        checksum=checksum,
        leak_alloc_bytes=leak_alloc,
        leak_freed_bytes=leak_freed,
        leak_live_bytes=leak_live,
    )


def _to_dict(result: BackendDurability) -> dict[str, Any]:
    return result.__dict__.copy()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compare_against_baseline(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    for backend in ("llvm", "c"):
        b = baseline.get("backends", {}).get(backend, {})
        c = current.get("backends", {}).get(backend, {})
        if not b or not c:
            continue
        lines.append(f"- {backend}:")
        b_rt = b.get("runtime_ms")
        c_rt = c.get("runtime_ms")
        if isinstance(b_rt, (int, float)) and isinstance(c_rt, (int, float)):
            lines.append(f"  runtime delta ms: {c_rt - b_rt:+.3f}")
        b_cc = b.get("compile_ms")
        c_cc = c.get("compile_ms")
        if isinstance(b_cc, (int, float)) and isinstance(c_cc, (int, float)):
            lines.append(f"  compile delta ms: {c_cc - b_cc:+.3f}")
        b_leak = b.get("leak_live_bytes")
        c_leak = c.get("leak_live_bytes")
        if isinstance(b_leak, int) and isinstance(c_leak, int):
            lines.append(f"  live leak delta bytes: {c_leak - b_leak:+d}")
    return lines


def _write_md(path: Path, payload: dict[str, Any], baseline_notes: list[str]) -> None:
    lines: list[str] = []
    lines.append("# Durability Stress Report")
    lines.append("")
    lines.append(f"- Generated: {payload['timestamp_human']}")
    lines.append(f"- Source: `{payload['source']}`")
    lines.append(f"- Duration target: `{payload['duration_ms']} ms`")
    lines.append(f"- Token inventory count: `{payload['token_inventory_count']}`")
    lines.append(f"- Builtin inventory count: `{payload['builtin_inventory_count']}`")
    lines.append("")
    lines.append(
        "| backend | compile ok | runtime ok | closed properly | crash suspected | compile ms | runtime ms | loops | checksum | leak live B |"
    )
    lines.append("| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for backend in ("llvm", "c"):
        row = payload["backends"][backend]
        lines.append(
            f"| {backend} | {row['compile_ok']} | {row['runtime_ok']} | "
            f"{row['closed_properly']} | {row['crash_suspected']} | "
            f"{row['compile_ms']} | {row['runtime_ms']} | "
            f"{row['loops']} | {row['checksum']} | {row.get('leak_live_bytes')} |"
        )
    lines.append("")
    if baseline_notes:
        lines.append("## Baseline Diff")
        lines.append("")
        lines.extend(baseline_notes)
        lines.append("")
    lines.append("## Backend Output Heads")
    lines.append("")
    for backend in ("llvm", "c"):
        row = payload["backends"][backend]
        lines.append(f"### {backend}")
        lines.append("")
        lines.append("stdout:")
        lines.append("```text")
        lines.append(str(row.get("stdout_head", "")))
        lines.append("```")
        lines.append("stderr:")
        lines.append("```text")
        lines.append(str(row.get("stderr_head", "")))
        lines.append("```")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run 5s durability mega stress for AILang.")
    p.add_argument(
        "--duration-ms",
        type=int,
        default=5000,
        help="Target runtime duration for mega stress program.",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Generated durability source path.",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_REPORT_JSON,
        help="JSON report path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_REPORT_MD,
        help="Markdown report path.",
    )
    p.add_argument(
        "--save-baseline",
        type=Path,
        default=None,
        help="Save this run as baseline JSON.",
    )
    p.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Baseline JSON for before/after diff reporting.",
    )
    p.add_argument(
        "--leak-threshold",
        type=int,
        default=0,
        help="Maximum allowed C live leak bytes.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration_ms < 1000:
        print("duration-ms must be >= 1000")
        return 2

    token_types, builtins = _load_inventory()
    source = args.source.resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        _build_mega_program(args.duration_ms, token_types, builtins),
        encoding="utf-8",
    )

    llvm = _compile_and_run(source, "llvm", args.duration_ms)
    c = _compile_and_run(source, "c", args.duration_ms)

    payload: dict[str, Any] = {
        "timestamp_human": datetime.now().strftime(DATE_HUMAN_FMT),
        "timestamp_iso": datetime.now().strftime(DATE_ISO_FMT),
        "source": str(source),
        "duration_ms": args.duration_ms,
        "token_inventory_count": len(token_types),
        "builtin_inventory_count": len(builtins),
        "backends": {
            "llvm": _to_dict(llvm),
            "c": _to_dict(c),
        },
    }

    baseline_notes: list[str] = []
    baseline_path = args.baseline.resolve()
    if baseline_path.exists():
        baseline_notes = _compare_against_baseline(_load_json(baseline_path), payload)

    out_json = args.output_json.resolve()
    out_md = args.output_md.resolve()
    _write_json(out_json, payload)
    _write_md(out_md, payload, baseline_notes)

    if args.save_baseline is not None:
        _write_json(args.save_baseline.resolve(), payload)

    print(f"durability json: {out_json}")
    print(f"durability md: {out_md}")
    if args.save_baseline is not None:
        print(f"durability baseline saved: {args.save_baseline.resolve()}")

    failed = False
    for row in (llvm, c):
        if not row.compile_ok or not row.runtime_ok or not row.closed_properly:
            failed = True
    if isinstance(c.leak_live_bytes, int) and c.leak_live_bytes > args.leak_threshold:
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

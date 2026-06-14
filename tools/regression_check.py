#!/usr/bin/env python3
"""
Regression check — between-phase baseline harness for the AILang
compiler refactor.

For each program in a small corpus, compile twice (once via the LLVM
backend, once via the C backend), run the resulting binary, and
collect:

  * wall-clock compile time (seconds)
  * wall-clock runtime (seconds)
  * exit code + stdout
  * leak counts from AILANG_LEAK_REPORT (C backend only -- LLVM
    backend doesn't emit the cleanup instrumentation)
  * generated-IR / generated-C size in bytes

Two modes:

  --save BASELINE.json
      Snapshot current numbers into BASELINE.json. Intended to be run
      once at the start of a refactor session; subsequent runs diff
      against it.

  (default)
      If a sibling ``regression_baseline.json`` exists, diff the
      current numbers against it and print deltas. A non-zero exit
      code signals at least one regression: a new leak or a runtime
      output change. Timing deltas are still reported but informational
      by default (enable hard timing gates with ``--timing-gate``).

The discipline this tool enforces is the audit-pattern from the
AILang leak-audit playbook: compile, run with leak report, bisect
on regression. Run between every refactor phase.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool

CORPUS_DIR = REPO_ROOT / "tests" / "corpus"
# Native-path temp directory for compiled regression artifacts.
#
# On some Windows hosts ``C:\\tmp`` is writable for regular files but
# blocked for ``.exe`` creation/overwrite by endpoint policy, which
# causes deterministic link failures (and false regression reports).
# Keep Windows outputs inside the repo by default, with an env override
# for CI/lab environments.
TMP_DIR = Path(
    os.getenv("AILANG_REGRESSION_TMP")
    or (
        str(tempfile.gettempdir())
        if os.name != "nt"
        else str(REPO_ROOT / "out" / "regression_tmp")
    )
)
TMP_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_PROGRAMS = [
    "01_hello",
    "02_factorial",
    "03_fibonacci",
    "04_string_concat",
    "05_arena_routed",
    "06_sqlite_demo",
]


def _default_baseline_path() -> Path:
    tools_dir = REPO_ROOT / "tools"
    if sys.platform.startswith("win"):
        return tools_dir / "regression_baseline_windows.json"
    if "microsoft" in platform.uname().release.lower():
        return tools_dir / "regression_baseline_wsl.json"
    return tools_dir / "regression_baseline_linux.json"


DEFAULT_BASELINE = _default_baseline_path()

# Compile-time delta below this threshold is treated as noise on
# Windows. AILang itself runs in a few ms; the dominant cost is gcc
# (100-150 ms) plus Python startup (250 ms), both of which jitter at
# the tens-of-ms level run-to-run. Empirically, spikes can exceed 80ms
# under Defender/process-scheduler load, so gate at 100ms.
COMPILE_NOISE_THRESHOLD_MS = 100
# Runtime jitter on short programs (especially LLVM JIT/AOT startup +
# process launch on Windows) can spike well beyond 50 ms under background
# scheduler load, so keep a wider noise window.
RUNTIME_NOISE_THRESHOLD_MS = 80
PROGRAM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)


@dataclass
class BackendResult:
    """One compile+run pair for a single backend."""

    compile_ms: float
    compile_ok: bool
    output_size_bytes: int
    runtime_ms: float
    runtime_ok: bool
    stdout_first_line: str
    # C backend only -- AILANG_LEAK_REPORT outputs.
    leak_alloc_bytes: Optional[int] = None
    leak_freed_bytes: Optional[int] = None
    leak_live_bytes: Optional[int] = None


@dataclass
class ProgramResult:
    name: str
    llvm: Optional[BackendResult] = None
    c: Optional[BackendResult] = None


@dataclass
class Snapshot:
    """A complete run of the regression harness, comparable to past runs."""

    timestamp: str
    environment: Dict[str, str] = field(default_factory=dict)
    programs: List[ProgramResult] = field(default_factory=list)


def _run(
    cmd: List[str],
    *,
    env: Optional[Dict[str, str]] = None,
    timeout: float = 60,
) -> tuple[int, str, str, float]:
    """Run a subprocess, return ``(exit_code, stdout, stderr, ms)``."""
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (-1, "", "timeout", (time.perf_counter() - start) * 1000)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return proc.returncode, proc.stdout, proc.stderr, elapsed_ms


def _parse_leak_report(stderr_or_stdout: str) -> tuple[int, int, int] | None:
    """Return ``(alloc, freed, live)`` if the AILang leak banner is in
    the output, else None. The report can land on either stream
    depending on platform; check both."""
    m = LEAK_RE.search(stderr_or_stdout)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_program_name(program_name: str) -> None:
    if not PROGRAM_NAME_RE.fullmatch(program_name):
        raise ValueError(
            "invalid program name "
            f"{program_name!r}; allowed pattern: {PROGRAM_NAME_RE.pattern}"
        )


def _tool_banner(binary: str, version_args: list[str]) -> str:
    tool_path = shutil.which(binary)
    return _tool_banner_from_path(tool_path, version_args)


def _tool_banner_from_path(tool_path: str | None, version_args: list[str]) -> str:
    if not tool_path:
        return "missing"
    try:
        proc = subprocess.run(
            [tool_path, *version_args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return f"{tool_path} (unreadable)"
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    head = out.splitlines()[0].strip() if out else "unknown"
    return f"{tool_path} :: {head}"


def _collect_environment_metadata() -> Dict[str, str]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "gcc": _tool_banner("gcc", ["--version"]),
        "clang": _tool_banner_from_path(resolve_llvm_tool("clang"), ["--version"]),
        "llvm_profdata": _tool_banner_from_path(
            same_llvm_root_tool(resolve_llvm_tool("clang"), "llvm-profdata"),
            ["--version"],
        ),
        "llc": _tool_banner_from_path(
            same_llvm_root_tool(resolve_llvm_tool("clang"), "llc"),
            ["--version"],
        ),
        "rustc": _tool_banner("rustc", ["--version"]),
    }


def _env_fingerprint(env: Dict[str, str]) -> str:
    keys = (
        "platform",
        "system",
        "machine",
        "python",
        "gcc",
        "clang",
        "llvm_profdata",
        "llc",
        "rustc",
    )
    return " | ".join(f"{k}={env.get(k, 'unknown')}" for k in keys)


def _compile_and_run(
    program_name: str,
    backend: str,
) -> BackendResult:
    """Compile + run one program for one backend."""
    _validate_program_name(program_name)
    src = CORPUS_DIR / f"{program_name}.ail"
    out_stem = TMP_DIR / f"regr_{backend}_{program_name}"
    if not _is_within(src, CORPUS_DIR):
        raise ValueError(f"resolved source escaped corpus dir: {src}")
    if not _is_within(out_stem, TMP_DIR):
        raise ValueError(f"resolved output escaped tmp dir: {out_stem}")
    # ``ailang.py`` adds ``.exe`` automatically on Windows.
    out_exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem

    compile_cmd = [
        sys.executable,
        str(REPO_ROOT / "ailang.py"),
        str(src),
    ]
    if backend == "c":
        compile_cmd.append("--backend=c")
    compile_cmd += ["-o", str(out_stem)]

    cc_code, _cc_out, _cc_err, compile_ms = _run(compile_cmd, timeout=120)
    compile_ok = cc_code == 0

    output_size = 0
    if backend == "llvm":
        ll_path = src.with_suffix(".ll")
        if ll_path.exists():
            output_size = ll_path.stat().st_size
    elif backend == "c":
        c_path = src.with_suffix(".c")
        if c_path.exists():
            output_size = c_path.stat().st_size

    runtime_ms = 0.0
    runtime_ok = False
    stdout_first = ""
    leak_alloc = leak_freed = leak_live = None

    if compile_ok and out_exe.exists():
        env = dict(os.environ)
        if backend == "c":
            env["AILANG_LEAK_REPORT"] = "1"
        rc, run_out, run_err, runtime_ms = _run([str(out_exe)], env=env, timeout=30)
        runtime_ok = rc == 0
        # First non-empty stdout line for output-equivalence comparison.
        for line in run_out.splitlines():
            if line.strip():
                stdout_first = line.strip()
                break
        if backend == "c":
            leak = _parse_leak_report(run_err) or _parse_leak_report(run_out)
            if leak is not None:
                leak_alloc, leak_freed, leak_live = leak

    return BackendResult(
        compile_ms=round(compile_ms, 1),
        compile_ok=compile_ok,
        output_size_bytes=output_size,
        runtime_ms=round(runtime_ms, 1),
        runtime_ok=runtime_ok,
        stdout_first_line=stdout_first,
        leak_alloc_bytes=leak_alloc,
        leak_freed_bytes=leak_freed,
        leak_live_bytes=leak_live,
    )


def collect_snapshot(programs: List[str]) -> Snapshot:
    """Run the full harness across the given program list."""
    snap = Snapshot(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        environment=_collect_environment_metadata(),
    )
    for name in programs:
        _validate_program_name(name)
        result = ProgramResult(name=name)
        result.llvm = _compile_and_run(name, "llvm")
        result.c = _compile_and_run(name, "c")
        snap.programs.append(result)
    return snap


def _fmt_backend_row(name: str, b: Optional[BackendResult]) -> str:
    if b is None:
        return f"{name:8} -- not run --"
    parts = [
        f"compile {b.compile_ms:6.0f}ms",
        f"run {b.runtime_ms:5.0f}ms",
        "OK" if (b.compile_ok and b.runtime_ok) else "FAIL",
    ]
    if b.leak_live_bytes is not None:
        leak_tag = "clean" if b.leak_live_bytes == 0 else f"LEAK {b.leak_live_bytes}B"
        parts.append(f"alloc {b.leak_alloc_bytes}B")
        parts.append(leak_tag)
    return f"{name:8}  " + "  ".join(parts)


def render_report(snap: Snapshot) -> str:
    lines = [
        f"=== AILang regression check ({snap.timestamp}) ===",
        "",
    ]
    for prog in snap.programs:
        lines.append(prog.name)
        lines.append("  " + _fmt_backend_row("LLVM", prog.llvm))
        lines.append("  " + _fmt_backend_row("C   ", prog.c))
        lines.append("")
    return "\n".join(lines)


def render_diff(
    curr: Snapshot,
    base: Snapshot,
    *,
    timing_gate: bool = False,
) -> tuple[str, int]:
    """Return ``(report_text, regression_count)``. A regression is any
    of: new leak, output mismatch, or compile/runtime over the noise
    threshold. Compile/runtime improvements are reported but don't count."""
    base_by_name = {p.name: p for p in base.programs}
    out: List[str] = [
        "=== regression diff vs baseline ===",
        f"baseline: {base.timestamp}",
        f"current:  {curr.timestamp}",
        "",
    ]
    regressions = 0

    for prog in curr.programs:
        prev = base_by_name.get(prog.name)
        if prev is None:
            out.append(f"{prog.name}  (new program -- no baseline)")
            continue
        out.append(prog.name)
        for backend in ("llvm", "c"):
            cur_b = getattr(prog, backend)
            prev_b = getattr(prev, backend)
            if cur_b is None or prev_b is None:
                continue
            tag = backend.upper().ljust(4)
            d_compile = cur_b.compile_ms - prev_b.compile_ms
            d_runtime = cur_b.runtime_ms - prev_b.runtime_ms
            issues: List[str] = []
            if cur_b.stdout_first_line != prev_b.stdout_first_line:
                issues.append(
                    f"OUTPUT CHANGED ({prev_b.stdout_first_line!r} "
                    f"-> {cur_b.stdout_first_line!r})"
                )
                regressions += 1
            if (
                cur_b.leak_live_bytes is not None
                and prev_b.leak_live_bytes is not None
                and cur_b.leak_live_bytes > prev_b.leak_live_bytes
            ):
                issues.append(
                    f"LEAK GROWTH ({prev_b.leak_live_bytes}B -> "
                    f"{cur_b.leak_live_bytes}B)"
                )
                regressions += 1
            if d_compile > COMPILE_NOISE_THRESHOLD_MS:
                issues.append(
                    f"compile +{d_compile:.0f}ms"
                    + (" [gate]" if timing_gate else " [info]")
                )
                if timing_gate:
                    regressions += 1
            elif d_compile < -COMPILE_NOISE_THRESHOLD_MS:
                issues.append(f"compile {d_compile:+.0f}ms (faster)")
            if d_runtime > RUNTIME_NOISE_THRESHOLD_MS:
                issues.append(
                    f"runtime +{d_runtime:.0f}ms"
                    + (" [gate]" if timing_gate else " [info]")
                )
                if timing_gate:
                    regressions += 1
            elif d_runtime < -RUNTIME_NOISE_THRESHOLD_MS:
                issues.append(f"runtime {d_runtime:+.0f}ms (faster)")

            if issues:
                out.append(f"  {tag} {' / '.join(issues)}")
            else:
                out.append(f"  {tag} clean")
        out.append("")
    out.append(f"regressions: {regressions}")
    return "\n".join(out), regressions


def _snap_to_dict(snap: Snapshot) -> dict:
    return {
        "timestamp": snap.timestamp,
        "environment": dict(snap.environment),
        "programs": [
            {
                "name": p.name,
                "llvm": asdict(p.llvm) if p.llvm else None,
                "c": asdict(p.c) if p.c else None,
            }
            for p in snap.programs
        ],
    }


def _dict_to_snap(d: dict) -> Snapshot:
    snap = Snapshot(
        timestamp=d["timestamp"],
        environment=dict(d.get("environment", {})),
    )
    for entry in d["programs"]:
        prog = ProgramResult(name=entry["name"])
        if entry.get("llvm"):
            prog.llvm = BackendResult(**entry["llvm"])
        if entry.get("c"):
            prog.c = BackendResult(**entry["c"])
        snap.programs.append(prog)
    return snap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Save snapshot to BASELINE.json instead of diffing",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"Baseline file to diff against (default: {DEFAULT_BASELINE.name})",
    )
    parser.add_argument(
        "--programs",
        nargs="+",
        default=DEFAULT_PROGRAMS,
        help="Corpus program names to run (without .ail)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force baseline comparison even when environment fingerprint differs.",
    )
    parser.add_argument(
        "--timing-gate",
        action="store_true",
        help="Treat compile/runtime timing regressions as hard failures.",
    )
    args = parser.parse_args()

    try:
        snap = collect_snapshot(args.programs)
    except ValueError as exc:
        print(f"regression-check input error: {exc}")
        return 2
    print(render_report(snap))

    if args.save is not None:
        args.save.write_text(
            json.dumps(_snap_to_dict(snap), indent=2),
            encoding="utf-8",
        )
        print(f"baseline saved -> {args.save}")
        return 0

    if args.baseline.exists():
        base = _dict_to_snap(json.loads(args.baseline.read_text(encoding="utf-8")))
        curr_fp = _env_fingerprint(snap.environment)
        base_fp = _env_fingerprint(base.environment)
        if curr_fp != base_fp and not args.force:
            print("=== baseline environment mismatch ===")
            print("current:", curr_fp)
            print("baseline:", base_fp)
            print("Refusing timing comparison across incompatible environments.")
            print("Re-run with --force to compare anyway.")
            return 2
        diff_text, regressions = render_diff(
            snap,
            base,
            timing_gate=bool(args.timing_gate),
        )
        print(diff_text)
        return 1 if regressions > 0 else 0

    print(f"(no baseline at {args.baseline}; pass --save to create one)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

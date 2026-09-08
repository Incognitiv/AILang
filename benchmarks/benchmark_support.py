"""Shared models, process helpers, and compiler adapters for benchmarks."""


from __future__ import annotations

import argparse
import importlib
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
from types import ModuleType
from typing import Any, Iterable, Optional, cast

_PSUTIL_MODULE: Optional[ModuleType]
try:
    _PSUTIL_MODULE = importlib.import_module("psutil")
except ImportError:
    _PSUTIL_MODULE = None

PSUTIL_PROCESS: Any = None
PSUTIL_NO_SUCH_PROCESS: Optional[type[BaseException]] = None
PSUTIL_ACCESS_DENIED: Optional[type[BaseException]] = None
if _PSUTIL_MODULE is not None:
    PSUTIL_PROCESS = getattr(_PSUTIL_MODULE, "Process")
    PSUTIL_NO_SUCH_PROCESS = cast(
        type[BaseException],
        getattr(_PSUTIL_MODULE, "NoSuchProcess"),
    )
    PSUTIL_ACCESS_DENIED = cast(
        type[BaseException],
        getattr(_PSUTIL_MODULE, "AccessDenied"),
    )

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


def _capture_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _psutil_process(pid: int) -> Any:
    if PSUTIL_PROCESS is None:
        return None
    return PSUTIL_PROCESS(pid)


def _is_psutil_process_error(exc: BaseException) -> bool:
    classes = [
        cls for cls in (PSUTIL_NO_SUCH_PROCESS, PSUTIL_ACCESS_DENIED) if cls is not None
    ]
    if not classes:
        return False
    return isinstance(exc, tuple(classes))


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
            stdout = _capture_text(exc.stdout)
            stderr = _capture_text(exc.stderr)
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

    p_handle = _psutil_process(proc.pid)
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
            except BaseException as exc:
                if not _is_psutil_process_error(exc):
                    raise
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
    """Extract the benchmark result from a standalone integer output line.

    AILang JIT also emits diagnostics such as ``Program exited with code: 0``.
    Parsing the last integer token made benchmark correctness depend on stdout
    buffering order.  Benchmark programs have a stricter contract: their result
    is printed on its own line, so diagnostics containing numbers are ignored.
    """
    numeric_lines = [
        line.strip()
        for line in text.replace("\r", "\n").splitlines()
        if re.fullmatch(r"[-+]?\d+", line.strip())
    ]
    if not numeric_lines:
        return None
    return int(numeric_lines[-1])


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


def compile_ailang_c_aot(
    source: Path, case_id: str
) -> tuple[bool, float, Optional[Path], str]:
    exe = OUT_DIR / f"bench_{case_id}_ailang_c_aot{_ext()}"
    cmd = [
        AilangTool,
        str(AILEXEC),
        str(source),
        "--backend=c",
        "-o",
        str(exe),
        "-O3",
    ]
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


def gather_versions() -> dict[str, str]:
    versions = {
        "python": sys.version.splitlines()[0],
    }
    versions["ailang"] = "source entrypoint"
    c = command_exists("clang") or command_exists("gcc")
    versions["c23"] = _version(c or "clang", ["--version"]) if c else "not found"
    versions["rust"] = _version("rustc", ["--version"])
    versions["os"] = platform.platform()
    versions["machine"] = platform.machine()
    return versions

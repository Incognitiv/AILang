"""Subprocess worker helpers for fast_jit repeat/JIT JSON mode."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

JIT_WORKER_MARKER = "JIT_WORKER_RESULT="
DEFAULT_JIT_OPT_LEVEL = 3


def _clamp_jit_opt(value: int) -> int:
    return max(0, min(3, int(value)))


def _is_packaged_runtime() -> bool:
    """Detect frozen/packaged executables (PyInstaller/Nuitka)."""
    if getattr(sys, "frozen", False):
        return True
    if "__compiled__" in globals():
        return True
    if not Path(sys.executable or "").exists():
        return True
    return False


def _frozen_worker_entrypoint() -> str:
    """Resolve executable path for a frozen self-spawn worker process.

    Nuitka can set ``sys.executable`` to a non-existent helper path
    (e.g. ``.../python``) inside dist folders. Prefer ``sys.argv[0]``
    when it points to a real file, then fall back to ``sys.executable``.
    """
    candidates = [sys.argv[0], sys.executable]
    for raw in candidates:
        if not raw:
            continue
        try:
            candidate = Path(raw).resolve()
        except OSError:
            candidate = Path(raw)
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    fallback = sys.executable or sys.argv[0]
    return str(Path(fallback).resolve())


def _jit_worker_cmd(
    filename: str,
    *,
    run_count: int,
    warmup_count: int,
    optimize: bool,
    profile: bool,
    flame_path: str,
    sample_hz: int,
    capture_output: bool,
    jit_opt: int = DEFAULT_JIT_OPT_LEVEL,
    dump_ir_path: str = "",
) -> list[str]:
    jit_opt = _clamp_jit_opt(jit_opt)
    args: list[str] = [
        "__jit_worker__",
        "--source-file",
        str(Path(filename).resolve()),
        "--run-count",
        str(max(1, int(run_count))),
        "--warmup-count",
        str(max(0, int(warmup_count))),
        "--jit-opt",
        str(jit_opt),
    ]
    if optimize or jit_opt > 0:
        args.append("--optimize")
    if profile:
        args.append("--profile")
    if flame_path:
        args.extend(["--flame-path", flame_path])
    if sample_hz > 0:
        args.extend(["--sample-hz", str(int(sample_hz))])
    if capture_output:
        args.append("--capture-output")
    if dump_ir_path:
        args.extend(["--jit-dump-ir", str(Path(dump_ir_path).resolve())])

    if _is_packaged_runtime():
        return [_frozen_worker_entrypoint(), *args]

    cli_main = Path(__file__).resolve().parents[1] / "cli" / "main.py"
    return [sys.executable, str(cli_main), *args]


def _checksum_from_worker_stdout(
    stdout: str, *, run_count: int, warmup_count: int
) -> tuple[int | None, str | None]:
    """Extract measured-run checksum from worker stdout around the JSON marker.

    The in-process JIT stdout redirection is not reliable on every hosted CRT,
    especially on Windows. In subprocess mode the parent captures the worker's
    process stdout reliably. C stdio may flush after the worker marker, so scan
    all numeric-only lines while ignoring marker lines.
    """
    values: list[int] = []
    for line in stdout.splitlines():
        if line.startswith(JIT_WORKER_MARKER):
            continue
        stripped = line.strip()
        if re.fullmatch(r"[-+]?\d+", stripped):
            values.append(int(stripped))

    expected_total = max(1, int(run_count)) + max(0, int(warmup_count))
    if not values:
        return None, None

    tail = values[-expected_total:]
    measured = tail[max(0, int(warmup_count)) :]
    if not measured:
        return None, None

    first = measured[0]
    if any(value != first for value in measured):
        return first, f"Non-deterministic worker output ({measured[:3]}...)."
    return first, None


def run_jit_worker_subprocess(
    filename: str,
    *,
    run_count: int,
    warmup_count: int,
    optimize: bool,
    profile: bool,
    flame_path: str,
    sample_hz: int,
    capture_output: bool,
    jit_opt: int = DEFAULT_JIT_OPT_LEVEL,
    dump_ir_path: str = "",
) -> dict[str, Any]:
    cmd = _jit_worker_cmd(
        filename,
        run_count=run_count,
        warmup_count=warmup_count,
        optimize=optimize,
        jit_opt=jit_opt,
        profile=profile,
        flame_path=flame_path,
        sample_hz=sample_hz,
        capture_output=capture_output,
        dump_ir_path=dump_ir_path,
    )
    timeout_s = max(60, 30 + (run_count + warmup_count) * 120)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "fail",
            "compile_ms": None,
            "runs_ms": [],
            "checksum": None,
            "note": f"JIT worker timed out after {timeout_s}s.",
        }

    payload: dict[str, Any] | None = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(JIT_WORKER_MARKER):
            raw = line[len(JIT_WORKER_MARKER) :].strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                return {
                    "status": "fail",
                    "compile_ms": None,
                    "runs_ms": [],
                    "checksum": None,
                    "note": f"Invalid JIT worker JSON payload: {exc}",
                }
            break

    if payload is None:
        err_tail = (proc.stderr or "").strip().splitlines()[-3:]
        out_tail = (proc.stdout or "").strip().splitlines()[-3:]
        detail = "; ".join((*out_tail, *err_tail)).strip()
        if not detail:
            detail = "no worker payload emitted"
        return {
            "status": "fail",
            "compile_ms": None,
            "runs_ms": [],
            "checksum": None,
            "note": f"JIT worker failed (exit {proc.returncode}): {detail}",
        }

    if not isinstance(payload.get("runs_ms"), list):
        payload["runs_ms"] = []
    if payload.get("checksum") is None:
        checksum, note = _checksum_from_worker_stdout(
            proc.stdout or "",
            run_count=run_count,
            warmup_count=warmup_count,
        )
        if checksum is not None:
            payload["checksum"] = checksum
        if note:
            payload["status"] = "fail"
            payload["note"] = note
    if payload.get("status") != "ok" and not payload.get("note"):
        payload["note"] = f"JIT worker returned non-ok status (exit {proc.returncode})."
    return payload

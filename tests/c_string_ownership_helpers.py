"""Shared execution helpers for C string-ownership regression tests."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
LIVE_AT_EXIT_RE = re.compile(r"live at exit:\s*(\d+)\s*bytes", re.IGNORECASE)

def _compile_c(source: str, out_stem: Path) -> Path:
    src_path = out_stem.with_suffix(".ail")
    src_path.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src_path),
            "--backend=c",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out_stem.with_suffix(".exe") if os.name == "nt" else out_stem


def _compile_c_with_generated_source(source: str, out_stem: Path) -> tuple[Path, str]:
    generated_dir = REPO_ROOT / "out" / "generated" / "c_backend"
    before = set(generated_dir.glob("*.c")) if generated_dir.exists() else set()
    exe = _compile_c(source, out_stem)
    after = set(generated_dir.glob("*.c")) if generated_dir.exists() else set()
    candidates = list(after - before) or list(after)
    assert candidates, "C backend did not emit a generated .c file"
    generated = max(candidates, key=lambda path: path.stat().st_mtime)
    return exe, generated.read_text(encoding="utf-8")


def _run_with_leak_report(exe: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["AILANG_LEAK_REPORT"] = "1"
    proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}"


def _live_bytes(output: str) -> int:
    match = LIVE_AT_EXIT_RE.search(output)
    assert match is not None, output
    return int(match.group(1))

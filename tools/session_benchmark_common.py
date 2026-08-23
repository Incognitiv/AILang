#!/usr/bin/env python3
"""
Session benchmark + safety snapshot harness.

Creates labeled session snapshots that combine:
1) cross-language performance benchmarks
2) regression corpus compile/run + leak snapshot
3) strict verifier summary
4) god-object audit snapshot

Then compares two saved snapshots with a markdown diff report.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_ROOT = REPO_ROOT / "benchmarks"
SESSION_ROOT = BENCH_ROOT / "sessions"
CHECK_REPORT_CORPUS = REPO_ROOT / "tests" / "corpus"
CHECK_REPORT_PROGRAMS = [
    "01_hello",
    "02_factorial",
    "03_fibonacci",
    "04_string_concat",
    "05_arena_routed",
    "06_sqlite_demo",
]
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"

def _regression_baseline_path() -> Path:
    tools_dir = REPO_ROOT / "tools"
    if sys.platform.startswith("win"):
        return tools_dir / "regression_baseline_windows.json"
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release or os.getenv("WSL_DISTRO_NAME"):
        return tools_dir / "regression_baseline_wsl.json"
    return tools_dir / "regression_baseline_linux.json"
def _run(cmd: list[str], timeout: int = 900) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr
def _median(values: list[float] | None) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
def _read_json_optional(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    return _read_json(path)
def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
def _parse_verifier_summary(text: str) -> tuple[int, int] | None:
    m = re.search(r"SUMMARY:\s*(\d+)\s*/\s*(\d+)\s*files passed", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

__all__ = [name for name in globals() if not name.startswith("__")]

#!/usr/bin/env python3
"""
One-command stabilization routine.

Runs a session capture (benchmark + regression + strict verifier + god-object
audit), then compares against the previous routine snapshot if present.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_ROOT = REPO_ROOT / "benchmarks" / "sessions"
SESSION_TOOL = REPO_ROOT / "tools" / "session_benchmark.py"
OLD_MAIN_COMPARE_TOOL = REPO_ROOT / "tools" / "compare_with_ailang_main.py"
PHASE_PROFILE_TOOL = REPO_ROOT / "tools" / "compile_phase_profile.py"
LANGUAGE_PROFILE_TOOL = REPO_ROOT / "tools" / "language_surface_profile.py"
DURABILITY_TOOL = REPO_ROOT / "tools" / "durability_stress.py"
STRICT_SURFACE_TOOL = REPO_ROOT / "tools" / "strict_surface_suite.py"
ENV_CHECK_TOOL = REPO_ROOT / "tools" / "env_check.py"
PACKAGE_SMOKE_TOOL = REPO_ROOT / "tools" / "package_smoke.py"
VARIANT_RECOMMENDATION_TOOL = REPO_ROOT / "tools" / "variant_recommendation.py"
PACKAGE_MATRIX_TOOL = REPO_ROOT / "tools" / "package_matrix_report.py"
PACKAGE_EXTRACT_TOOL = REPO_ROOT / "tools" / "package_extract_smoke.py"
RELEASE_MANIFEST_TOOL = REPO_ROOT / "tools" / "release_manifest.py"
RELEASE_CHECKLIST_TOOL = REPO_ROOT / "tools" / "release_checklist.py"
ADAPT_TEARDOWN_TOOL = REPO_ROOT / "tools" / "adapt_teardown_audit.py"
SESSION_BENCH_DOC = REPO_ROOT / "benchmarks" / "results" / "session_benchmarking.md"
AUTO_SECTION_START = "<!-- AUTO-ROUTINE-START -->"
AUTO_SECTION_END = "<!-- AUTO-ROUTINE-END -->"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"

def _run(cmd: list[str], timeout: int = 3600) -> int:
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        timeout=timeout,
    )
    return int(proc.returncode)
def _latest_routine_labels(current_label: str) -> list[str]:
    def _is_compare_compatible(label: str) -> bool:
        manifest_path = SESSION_ROOT / label / "session.json"
        if not manifest_path.exists():
            return False
        try:
            manifest = _read_json(manifest_path)
        except (OSError, ValueError, TypeError):
            return False
        benchmark_json = manifest.get("paths", {}).get("benchmark_json")
        if not isinstance(benchmark_json, str) or not benchmark_json.strip():
            return False
        return Path(benchmark_json).exists()

    labels = [
        label
        for label, _ in _all_routine_records()
        if label != current_label
        and label < current_label
        and _is_compare_compatible(label)
    ]
    return labels
def _all_routine_labels() -> list[str]:
    return [label for label, _ in _all_routine_records()]
def _all_routine_records() -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    if not SESSION_ROOT.exists():
        return labels
    for d in SESSION_ROOT.iterdir():
        if not d.is_dir():
            continue
        if not d.name.startswith("routine_"):
            continue
        manifest_path = d / "session.json"
        if not manifest_path.exists():
            continue
        ts = ""
        try:
            manifest = _read_json(manifest_path)
            ts = str(
                manifest.get("timestamp_iso")
                or manifest.get("timestamp")
                or manifest.get("timestamp_human")
                or ""
            )
        except (OSError, ValueError, TypeError):
            ts = ""
        labels.append((d.name, ts))

    def _record_sort_key(record: tuple[str, str]) -> tuple[str, str]:
        label, ts = record
        return ts, label

    return sorted(labels, key=_record_sort_key)
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
def _format_human_date(raw: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", DATE_HUMAN_FMT):
        try:
            return datetime.strptime(raw, fmt).strftime(DATE_HUMAN_FMT)
        except ValueError:
            continue
    return raw
def _display_timestamp(manifest: dict[str, Any]) -> str:
    human = manifest.get("timestamp_human")
    if isinstance(human, str) and human.strip():
        return human
    iso = manifest.get("timestamp_iso") or manifest.get("timestamp")
    if isinstance(iso, str) and iso.strip():
        return _format_human_date(iso)
    return "unknown"

__all__ = [name for name in globals() if not name.startswith("__")]

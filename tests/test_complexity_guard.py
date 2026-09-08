from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_god_object_audit_has_no_candidates() -> None:
    with tempfile.TemporaryDirectory() as td:
        json_out = Path(td) / "god_object_audit.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "god_object_audit.py"),
                "--max-file-lines",
                "750",
                "--json-output",
                str(json_out),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["candidate_count"] == 0
    assert payload["oversized_file_count"] == 0
    assert payload["max_file_line_count"] <= 750


def test_first_party_python_files_do_not_exceed_750_lines() -> None:
    roots = ("source", "verifier", "tools", "tests", "benchmarks")
    oversized: list[tuple[str, int]] = []
    for root_name in roots:
        for path in (REPO_ROOT / root_name).rglob("*.py"):
            if any(part in {"out", "build", "dist", "__pycache__"} for part in path.parts):
                continue
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > 750:
                oversized.append((str(path.relative_to(REPO_ROOT)), line_count))
    assert oversized == []


def test_authored_public_tree_has_no_file_over_750_lines() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "public_tree_audit.py"),
            "--enforce-line-limit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

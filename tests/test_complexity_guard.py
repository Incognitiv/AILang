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

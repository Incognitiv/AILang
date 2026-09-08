from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_repository_hygiene_is_clean(tmp_path: Path) -> None:
    report = tmp_path / "repository_hygiene.json"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/repository_hygiene_audit.py",
            "--json-output",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["clone_roots"] == {"source": 0, "verifier": 0, "tools": 0}
    assert payload["issues"] == []
    assert payload["passed"] is True

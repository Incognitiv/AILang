from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
STRESS_CORPUS = REPO_ROOT / "tests" / "corpus" / "stress"


def _report_checks(path: Path) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(path), "--report-checks-json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_stress_corpus_int64_edges_keep_overflow_checks() -> None:
    for name in ("int64_max_edge.ail", "int64_min_edge.ail"):
        payload = _report_checks(STRESS_CORPUS / name)
        summary = payload.get("summary", {})
        assert int(summary.get("overflow:inserted", 0)) >= 1

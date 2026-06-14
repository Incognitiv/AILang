from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_cli_version_flag_without_source_file() -> None:
    proc = subprocess.run(
        [sys.executable, str(AILANG), "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    output = (proc.stdout or "").strip()
    assert output.startswith("AILang Compiler v"), output

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_library_modules_may_export_unused_constants() -> None:
    source = """
@library("constants")

const int EXPORTED_CONSTANT = 42
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "constants.ail"
        src_path.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src_path), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0
        assert "declared but never used" not in proc.stdout

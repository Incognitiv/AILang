from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_check_includes_class_cleanup_warning() -> None:
    source = """
class Session then
    handle token
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "class_cleanup.ail"
        src_path.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src_path), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 1
        assert "CLASS-CLEANUP" in proc.stdout
        assert "~Session()" in proc.stdout

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
SVG_DEMO = REPO_ROOT / "examples" / "ui" / "svg_demo.ail"


def test_svg_demo_passes_check() -> None:
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(SVG_DEMO), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        proc.returncode == 0
    ), f"--check failed for svg_demo.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_leak_report_zero_suppresses_completed_exit_leak(tmp_path: Path) -> None:
    src = tmp_path / "leak_suppressed.ail"
    out = tmp_path / "leak_suppressed"
    src.write_text(
        """\
def main(): int
    leaked = "" + "intentional-live-state"
    return strlen(leaked)
end
""",
        encoding="utf-8",
    )
    compile_proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--backend=c", "-o", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr
    exe = out.with_suffix(".exe") if os.name == "nt" else out
    env = dict(os.environ)
    env["AILANG_LEAK_REPORT"] = "0"
    run_proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert run_proc.returncode == len("intentional-live-state")
    assert "AILang memory report" not in run_proc.stderr
    assert "POSSIBLE LEAK" not in run_proc.stderr

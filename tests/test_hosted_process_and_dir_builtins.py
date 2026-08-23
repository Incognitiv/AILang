from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_c_backend_process_capture_and_list_dir_are_owned(tmp_path: Path) -> None:
    sentinel = tmp_path / "msh_list_dir_sentinel.txt"
    sentinel.write_text("ok", encoding="utf-8")

    capture_command = "cmd /C echo captest" if os.name == "nt" else "printf captest"
    src = tmp_path / "hosted_process_dir.ail"
    out_stem = tmp_path / "hosted_process_dir"
    src.write_text(
        f"""\
def main(): int
    out = process_capture("{capture_command}")
    print out
    entries = list_dir(".")
    print entries
    dealloc(out)
    dealloc(entries)
    return 0
end
""",
        encoding="utf-8",
    )

    compile_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--backend=c",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert compile_proc.returncode == 0, (
        f"compile failed\nstdout:\n{compile_proc.stdout}\n\nstderr:\n{compile_proc.stderr}"
    )

    exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
    env = os.environ.copy()
    env["AILANG_LEAK_REPORT"] = "1"
    run_proc = subprocess.run(
        [str(exe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert run_proc.returncode == 0, run_proc.stderr
    assert "captest" in run_proc.stdout
    assert sentinel.name in run_proc.stdout
    assert "POSSIBLE LEAK" not in run_proc.stderr

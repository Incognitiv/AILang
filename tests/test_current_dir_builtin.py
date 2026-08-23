from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _compile_and_run_current_dir(tmp_path: Path, backend: str) -> subprocess.CompletedProcess[str]:
    src = tmp_path / "current_dir_builtin.ail"
    out_stem = tmp_path / f"current_dir_{backend}"
    src.write_text(
        """\
def main(): int
    cwd = current_dir()
    print cwd
    dealloc(cwd)
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
            "--backend",
            backend,
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if compile_proc.returncode != 0:
        if backend == "llvm":
            msg = (compile_proc.stdout + "\n" + compile_proc.stderr).lower()
            if "llvm toolchain" in msg or "clang not found" in msg or "llc not found" in msg:
                pytest.skip("LLVM toolchain unavailable in this environment")
        raise AssertionError(
            f"{backend} compile failed\nstdout:\n{compile_proc.stdout}\n\nstderr:\n{compile_proc.stderr}"
        )

    exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
    env = os.environ.copy()
    env["AILANG_LEAK_REPORT"] = "1"
    return subprocess.run(
        [str(exe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_current_dir_returns_owned_process_cwd(tmp_path: Path, backend: str) -> None:
    run_proc = _compile_and_run_current_dir(tmp_path, backend)
    assert run_proc.returncode == 0, (
        f"{backend} runtime failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    )
    lines = [line.strip() for line in run_proc.stdout.splitlines() if line.strip()]
    assert lines[0].lower() == str(tmp_path).lower()
    assert "POSSIBLE LEAK" not in run_proc.stderr

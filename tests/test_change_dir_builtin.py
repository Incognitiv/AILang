from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _compile_change_dir(tmp_path: Path, backend: str) -> Path:
    src = tmp_path / "change_dir_builtin.ail"
    out_stem = tmp_path / f"change_dir_{backend}"
    src.write_text(
        """\
def main(): int
    rc = change_dir(argv(1))
    print rc
    cwd = current_dir()
    print cwd
    dealloc(cwd)
    return 0
end
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
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
    if proc.returncode != 0:
        if backend == "llvm":
            msg = (proc.stdout + "\n" + proc.stderr).lower()
            if "llvm toolchain" in msg or "clang not found" in msg or "llc not found" in msg:
                pytest.skip("LLVM toolchain unavailable in this environment")
        raise AssertionError(
            f"{backend} compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
    return out_stem.with_suffix(".exe") if os.name == "nt" else out_stem


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_change_dir_updates_process_cwd(tmp_path: Path, backend: str) -> None:
    exe = _compile_change_dir(tmp_path, backend)
    target = tmp_path / "target"
    target.mkdir()
    env = os.environ.copy()
    env["AILANG_LEAK_REPORT"] = "1"
    run = subprocess.run(
        [str(exe), str(target)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, f"stdout:\n{run.stdout}\n\nstderr:\n{run.stderr}"
    lines = [line.strip() for line in run.stdout.splitlines() if line.strip()]
    assert lines[0] == "0"
    assert lines[1].lower() == str(target).lower()
    assert "POSSIBLE LEAK" not in run.stderr

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def _compile_and_run(src: Path, out_stem: Path, backend: str) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if backend == "llvm" and proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    run_proc = subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stdout + run_proc.stderr
    return [line.strip() for line in run_proc.stdout.splitlines() if line.strip()]


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_record_value_field_access_and_assignment(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "record_value_parity.ail"
    src.write_text(
        """\
record Pair then
    int left
    int right
end

def bumped_sum(p: Pair): int
    p.left = p.left + 5
    return p.left + p.right
end

def main(): int
    Pair p = new Pair(7, 30)
    p.right = p.right + 5
    print(p.left + p.right)
    print(bumped_sum(p))
    print(p.left + p.right)
    return 0
end
""",
        encoding="utf-8",
    )

    assert _compile_and_run(src, tmp_path / f"record_value_{backend}", backend) == [
        "42",
        "47",
        "42",
    ]

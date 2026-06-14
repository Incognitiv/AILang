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


def _compile_and_run(
    src: Path, out_stem: Path, *, backend: str
) -> subprocess.CompletedProcess[str]:
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
    if proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if backend == "llvm" and (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
        if backend == "c" and "no c compiler found" in msg:
            pytest.skip("C compiler unavailable")
    assert (
        proc.returncode == 0
    ), f"{backend} compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

    run_proc = subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        run_proc.returncode == 0
    ), f"{backend} run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    return run_proc


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_typedef_aliases_lower_across_native_backends(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "typedef_aliases.ail"
    src.write_text(
        """\
typedef int UserId
typedef Score = int
typedef short Small
typedef [byte; 4] Hash4

record Packet then
    UserId id
    Small amount
    Hash4 digest
end

def alias_sum(x: UserId): UserId
    UserId y = x + 1
    return y
end

def main(): int
    Score score = 39
    if alias_sum(score + 2) != 42 then
        return 1
    end
    if sizeof("UserId") != sizeof("int") then
        return 2
    end
    if sizeof("Hash4") != 4 then
        return 3
    end
    if sizeof("Packet") != 16 then
        return 4
    end
    return 0
end
""",
        encoding="utf-8",
    )

    check_proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        check_proc.returncode == 0
    ), f"check failed\nstdout:\n{check_proc.stdout}\n\nstderr:\n{check_proc.stderr}"

    _compile_and_run(src, tmp_path / f"typedef_aliases_{backend}", backend=backend)

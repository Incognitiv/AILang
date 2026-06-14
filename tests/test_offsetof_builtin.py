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


def _compile_and_run(src: Path, out_stem: Path, *, backend: str) -> None:
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


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_offsetof_record_and_union_across_native_backends(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "offsetof_builtin.ail"
    src.write_text(
        """\
record OffsetPacket then
    byte tag
    int value
    [byte;3] data
    byte tail
end

union OffsetWord then
    int whole
    [byte;8] bytes
end

def main(): int
    if offsetof("OffsetPacket", "tag") != 0 then
        return 1
    end
    if offsetof("OffsetPacket", "value") != 8 then
        return 2
    end
    if offsetof("OffsetPacket", "data") != 16 then
        return 3
    end
    if offsetof("OffsetPacket", "tail") != 19 then
        return 4
    end
    if sizeof("OffsetPacket") != 24 then
        return 5
    end
    if alignof("OffsetPacket") != 8 then
        return 6
    end
    if offsetof("OffsetWord", "whole") != 0 then
        return 7
    end
    if offsetof("OffsetWord", "bytes") != 0 then
        return 8
    end
    if sizeof("OffsetWord") != 8 then
        return 9
    end
    if alignof("OffsetWord") != 8 then
        return 10
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

    _compile_and_run(src, tmp_path / f"offsetof_builtin_{backend}", backend=backend)

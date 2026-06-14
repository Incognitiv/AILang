from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_native_argc_argv_reaches_user_main(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "argv_smoke.ail"
    src.write_text(
        """\
int main():
    if argc() < 3 then
        return 10
    end
    if streq(argv(1), "alpha") != 1 then
        return 11
    end
    if streq(argv(2), "beta") != 1 then
        return 12
    end
    print argv(1)
    print argv(2)
    return 0
end
""",
        encoding="utf-8",
    )

    out = tmp_path / f"argv_smoke_{backend}"
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-o",
            str(out),
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
    assert proc.returncode == 0, proc.stdout + proc.stderr

    run = subprocess.run(
        [str(_native_exe(out)), "alpha", "beta"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip().splitlines() == ["alpha", "beta"]

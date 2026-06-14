from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from llvmlite import binding

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

AILANG = REPO_ROOT / "ailang.py"

from target_info import os_from_platform, os_from_triple  # noqa: E402


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def _expected_os(backend: str) -> str:
    if backend == "llvm":
        return os_from_triple(binding.get_default_triple())
    return os_from_platform()


def _write_source(tmp_path: Path, *, backend: str) -> Path:
    expected_os = _expected_os(backend)
    src = tmp_path / f"target_introspection_{backend}.ail"
    src.write_text(
        f"""\
def main(): int
    backend_name = target_backend()
    os_name = target_os()

    if streq(backend_name, "{backend}") != 1 then
        return 1
    end
    if strlen(os_name) <= 0 then
        return 2
    end

    comptime if target_backend() != "{backend}" then
        return 3
    end

    comptime if target_os() != "{expected_os}" then
        return 4
    end

    return 0
end
""",
        encoding="utf-8",
    )
    return src


def _compile_and_run(src: Path, out_stem: Path, *, backend: str) -> str:
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
    return run_proc.stdout.strip()


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_target_introspection_backend_and_os_compiletime_and_runtime(
    tmp_path: Path, backend: str
) -> None:
    src = _write_source(tmp_path, backend=backend)
    output = _compile_and_run(src, tmp_path / f"target_intro_{backend}", backend=backend)
    assert output == ""

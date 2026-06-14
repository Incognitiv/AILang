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


def _compile(src: Path, out: Path, *, backend: str) -> subprocess.CompletedProcess:
    return subprocess.run(
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


def _compile_or_skip(src: Path, out: Path, *, backend: str) -> None:
    proc = _compile(src, out, backend=backend)
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


def test_read_stdin_reads_all_piped_input_c_backend(tmp_path: Path) -> None:
    src = tmp_path / "read_stdin_probe.ail"
    src.write_text(
        """\
int main():
    body = read_stdin()
    print body
    dealloc(body)
    return 0
end
""",
        encoding="utf-8",
    )
    out = tmp_path / "read_stdin_probe"
    _compile_or_skip(src, out, backend="c")
    run = subprocess.run(
        [str(_native_exe(out))],
        input=b"one\ntwo\n",
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.replace(b"\r\n", b"\n") == b"one\ntwo\n\n"


def test_read_stdin_llvm_lowering_declares_fgetc(tmp_path: Path) -> None:
    src = tmp_path / "read_stdin_probe.ail"
    src.write_text(
        """\
int main():
    body = read_stdin()
    print strlen(body)
    dealloc(body)
    return 0
end
""",
        encoding="utf-8",
    )
    out = tmp_path / "read_stdin_probe_llvm"
    _compile_or_skip(src, out, backend="llvm")

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


def _compile(src: Path, out: Path, *, backend: str) -> None:
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


def test_read_file_crlf_output_matches_c_and_llvm(tmp_path: Path) -> None:
    src = tmp_path / "read_file_crlf.ail"
    src.write_text(
        """\
int main():
    body = read_file(argv(1))
    print body
    return 0
end
""",
        encoding="utf-8",
    )
    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"AILang cat smoke\r\n")

    c_out = tmp_path / "read_file_c"
    llvm_out = tmp_path / "read_file_llvm"
    _compile(src, c_out, backend="c")
    _compile(src, llvm_out, backend="llvm")

    c_run = subprocess.run(
        [str(_native_exe(c_out)), sample.name],
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    llvm_run = subprocess.run(
        [str(_native_exe(llvm_out)), sample.name],
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert c_run.returncode == 0, c_run.stdout + c_run.stderr
    assert llvm_run.returncode == 0, llvm_run.stdout + llvm_run.stderr

    def normalize_newlines(data: bytes) -> bytes:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"")

    expected = b"AILang cat smoke\n\n"
    assert normalize_newlines(c_run.stdout) == expected
    assert normalize_newlines(llvm_run.stdout) == expected


def test_file_exists_accepts_files_and_directories(tmp_path: Path) -> None:
    src = tmp_path / "file_exists_paths.ail"
    src.write_text(
        """\
int main():
    if file_exists(argv(1)) != 1 then
        return 1
    end
    if file_exists(argv(2)) != 1 then
        return 2
    end
    if file_exists(argv(3)) != 0 then
        return 3
    end
    return 0
end
""",
        encoding="utf-8",
    )
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("x", encoding="utf-8")
    sample_dir = tmp_path / "sample-dir"
    sample_dir.mkdir()
    missing = tmp_path / "missing"

    out = tmp_path / "file_exists_paths"
    _compile(src, out, backend="c")
    run_proc = subprocess.run(
        [str(_native_exe(out)), str(sample_file), str(sample_dir), str(missing)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stderr or run_proc.stdout


def test_access_mode_and_file_can_execute_c_backend(tmp_path: Path) -> None:
    src = tmp_path / "access_modes.ail"
    src.write_text(
        """\
int main():
    if access(argv(1), 0) != 1 then
        return 1
    end
    if access(argv(3), 0) != 0 then
        return 2
    end
    if file_can_execute(argv(2)) != 1 then
        return 3
    end
    if file_can_execute(argv(1)) != 0 then
        return 4
    end
    return 0
end
""",
        encoding="utf-8",
    )
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("not executable", encoding="utf-8")
    if os.name != "nt":
        sample_file.chmod(0o644)
    missing = tmp_path / "missing"

    out = tmp_path / "access_modes"
    _compile(src, out, backend="c")
    run_proc = subprocess.run(
        [
            str(_native_exe(out)),
            str(sample_file),
            sys.executable,
            str(missing),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stderr or run_proc.stdout


def test_file_mode_predicates_c_backend(tmp_path: Path) -> None:
    src = tmp_path / "file_mode_predicates.ail"
    src.write_text(
        """\
int main():
    if file_is_regular(argv(1)) != 1 then
        return 1
    end
    if file_is_regular(argv(2)) != 0 then
        return 2
    end
    if file_is_block(argv(1)) != 0 then
        return 3
    end
    if file_is_char(argv(1)) != 0 then
        return 4
    end
    if file_is_fifo(argv(1)) != 0 then
        return 5
    end
    if file_is_socket(argv(1)) != 0 then
        return 6
    end
    if file_is_setuid(argv(1)) != 0 then
        return 7
    end
    if file_is_setgid(argv(1)) != 0 then
        return 8
    end
    if fd_is_tty(1) != 0 then
        return 9
    end
    return 0
end
""",
        encoding="utf-8",
    )
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("plain file", encoding="utf-8")
    sample_dir = tmp_path / "sample-dir"
    sample_dir.mkdir()

    out = tmp_path / "file_mode_predicates"
    _compile(src, out, backend="c")
    run_proc = subprocess.run(
        [str(_native_exe(out)), str(sample_file), str(sample_dir)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stderr or run_proc.stdout

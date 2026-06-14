from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def test_c_backend_lowers_fixed_array_and_slice_fields() -> None:
    source = """\
record Packet then
    [byte;16] data
    slice[byte] view
end

def main(): int
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "p8_layout.ail"
        exe_stem = tmp / "p8_layout"
        src.write_text(source, encoding="utf-8")

        proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src),
                "--backend=c",
                "-o",
                str(exe_stem),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert (
            proc.returncode == 0
        ), f"C compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

        generated_c_paths = [
            Path(line.split("Generated ", 1)[1].strip())
            for line in proc.stdout.splitlines()
            if "Generated " in line and line.strip().endswith(".c")
        ]
        assert generated_c_paths, f"Expected generated C path in stdout:\n{proc.stdout}"
        c_file = generated_c_paths[-1]
        assert c_file.exists(), f"Expected generated C file: {c_file}"
        c_text = c_file.read_text(encoding="utf-8")

        assert "uint8_t data[16];" in c_text
        assert "ailang_dyn_array view;" in c_text


def test_llvm_fixed_array_local_literal_initialization_runs(tmp_path: Path) -> None:
    src = tmp_path / "llvm_fixed_array_init.ail"
    out_stem = tmp_path / "llvm_fixed_array_init"
    src.write_text(
        """\
type Arr4 = [int; 4]

def main(): int
    Arr4 arr = [3, 1, 4, 1]
    if arr[0] + arr[1] + arr[2] + arr[3] != 9 then
        return 1
    end
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--backend=llvm", "-o", str(out_stem)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
    assert proc.returncode == 0, proc.stderr or proc.stdout

    run_proc = subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stderr or run_proc.stdout

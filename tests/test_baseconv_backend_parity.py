from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _compile_and_run(source: str, backend: str) -> tuple[int, str, str, int, str, str]:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "baseconv_parity.ail"
        out_stem = tmp / f"baseconv_{backend}"
        src.write_text(source, encoding="utf-8")

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
            return (
                compile_proc.returncode,
                compile_proc.stdout,
                compile_proc.stderr,
                -1,
                "",
                "",
            )

        exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        return (
            compile_proc.returncode,
            compile_proc.stdout,
            compile_proc.stderr,
            run_proc.returncode,
            run_proc.stdout,
            run_proc.stderr,
        )


def test_hex_bin_oct_backend_parity_and_expected_semantics() -> None:
    source = """\
def main(): int
    print(hex(0))
    print(hex(15))
    print(hex(16))
    print(hex(255))
    print(hex(-1))
    print(bin(0))
    print(bin(10))
    print(bin(-1))
    print(oct(0))
    print(oct(8))
    print(oct(255))
    print(oct(-1))
    return 0
end
"""
    expected = [
        "0x0",
        "0xF",
        "0x10",
        "0xFF",
        "0xFFFFFFFFFFFFFFFF",
        "0b0",
        "0b1010",
        "0b1111111111111111111111111111111111111111111111111111111111111111",
        "0o0",
        "0o10",
        "0o377",
        "0o1777777777777777777777",
    ]

    c_cc_rc, c_cc_out, c_cc_err, c_run_rc, c_run_out, c_run_err = _compile_and_run(
        source, "c"
    )
    assert c_cc_rc == 0, f"C compile failed\nstdout:\n{c_cc_out}\n\nstderr:\n{c_cc_err}"
    assert (
        c_run_rc == 0
    ), f"C runtime failed\nstdout:\n{c_run_out}\n\nstderr:\n{c_run_err}"
    c_lines = _non_empty_lines(c_run_out)
    assert c_lines == expected

    (
        llvm_cc_rc,
        llvm_cc_out,
        llvm_cc_err,
        llvm_run_rc,
        llvm_run_out,
        llvm_run_err,
    ) = _compile_and_run(source, "llvm")
    if llvm_cc_rc != 0:
        msg = (llvm_cc_out + "\n" + llvm_cc_err).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM toolchain unavailable in this environment")
        raise AssertionError(
            "LLVM compile failed\n" f"stdout:\n{llvm_cc_out}\n\nstderr:\n{llvm_cc_err}"
        )

    assert (
        llvm_run_rc == 0
    ), f"LLVM runtime failed\nstdout:\n{llvm_run_out}\n\nstderr:\n{llvm_run_err}"
    llvm_lines = _non_empty_lines(llvm_run_out)
    assert llvm_lines == expected
    assert llvm_lines == c_lines


def test_oct_negative_one_string_value_backend_parity() -> None:
    source = """\
def main(): int
    s = oct(-1)
    print(len(s))
    print(s)
    return 0
end
"""
    expected = ["24", "0o1777777777777777777777"]

    c_cc_rc, c_cc_out, c_cc_err, c_run_rc, c_run_out, c_run_err = _compile_and_run(
        source, "c"
    )
    assert c_cc_rc == 0, f"C compile failed\nstdout:\n{c_cc_out}\n\nstderr:\n{c_cc_err}"
    assert (
        c_run_rc == 0
    ), f"C runtime failed\nstdout:\n{c_run_out}\n\nstderr:\n{c_run_err}"
    assert _non_empty_lines(c_run_out) == expected

    (
        llvm_cc_rc,
        llvm_cc_out,
        llvm_cc_err,
        llvm_run_rc,
        llvm_run_out,
        llvm_run_err,
    ) = _compile_and_run(source, "llvm")
    if llvm_cc_rc != 0:
        msg = (llvm_cc_out + "\n" + llvm_cc_err).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM toolchain unavailable in this environment")
        raise AssertionError(
            "LLVM compile failed\n" f"stdout:\n{llvm_cc_out}\n\nstderr:\n{llvm_cc_err}"
        )
    assert (
        llvm_run_rc == 0
    ), f"LLVM runtime failed\nstdout:\n{llvm_run_out}\n\nstderr:\n{llvm_run_err}"
    assert _non_empty_lines(llvm_run_out) == expected

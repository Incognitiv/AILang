"""Run literal contracts through AOT, C AOT and JIT with an independent oracle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("backend", ["llvm", "c", "jit"])
def test_native_literal_values_match_source_contract(tmp_path: Path, backend: str) -> None:
    source = tmp_path / "literal_contract.ail"
    source.write_text(
        'void main():\n'
        + r'    print("BEGIN>\\n<END")' + "\n"
        + r'    print("BEGIN>\x5cn<END")' + "\n"
        + r'    print("BEGIN>\\x41<END")' + "\n"
        + r"    print('\x41')" + "\n"
        + r"    print('\101')" + "\n"
        + 'end\n',
        encoding="utf-8",
    )
    command = [sys.executable, str(ROOT / "ailang.py"), str(source)]
    output = tmp_path / ("program.exe" if sys.platform == "win32" else "program")
    if backend != "jit":
        command.extend(["--backend=" + backend, "-o", str(output)])
    compiled = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                              timeout=120, check=False)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    if backend == "jit":
        stdout = compiled.stdout
    else:
        run = subprocess.run([str(output)], capture_output=True, text=True,
                             timeout=20, check=False)
        assert run.returncode == 0, run.stderr
        stdout = run.stdout
    expected = "BEGIN>\\n<END\nBEGIN>\\n<END\nBEGIN>\\x41<END\n65\n65\n"
    # JIT has driver banners. The compiled program payload must still be exact.
    if backend == "jit":
        assert expected in stdout, repr(stdout)
    else:
        assert stdout == expected, repr(stdout)

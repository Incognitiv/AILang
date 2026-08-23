from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _generated_c_path_from_stdout(stdout: str) -> Path:
    match = re.search(r"Generated\s+(.+\.c)\s*$", stdout, re.MULTILINE)
    assert match is not None, f"Missing generated C path in output:\n{stdout}"
    return Path(match.group(1).strip())


def test_c_backend_lowers_unsigned_aliases_to_expected_c_types() -> None:
    source = """\
def main(): int
    uint x = 1
    usmall y = 2
    ulong z = 3
    byte b = 4
    print(x)
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "type_lowering.ail"
        exe_stem = tmp / "type_lowering"
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

        c_file = _generated_c_path_from_stdout(proc.stdout)
        assert c_file.exists(), f"Expected generated C file: {c_file}"
        c_text = c_file.read_text(encoding="utf-8")

        assert "uint64_t x;" in c_text
        assert "uint16_t y;" in c_text
        assert "unsigned __int128 z;" in c_text
        assert "uint8_t b;" in c_text

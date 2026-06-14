from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402

AILANG = REPO_ROOT / "ailang.py"


PTR_ARRAY_SOURCE = """
def main(): int
    argv = ptr_array("msh", "-c", "echo ok")
    if argv == 0 then
        return 1
    end
    return 0
end
"""


def _to_c(src: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    transpiler = CTranspiler()
    return transpiler.transpile(ast, "<inline>")


def test_ptr_array_is_known_to_diagnostics(tmp_path: Path) -> None:
    src_path = tmp_path / "ptr_array_probe.ail"
    src_path.write_text(PTR_ARRAY_SOURCE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src_path), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_ptr_array_c_backend_lowers_to_compound_literal() -> None:
    c_code = _to_c(PTR_ARRAY_SOURCE)
    assert "ptr_array(" not in c_code
    assert "(const char *[])" in c_code
    assert "NULL" in c_code


def test_ptr_array_c_backend_compiles_and_runs() -> None:
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "ptr_array_probe.ail"
        out_stem = Path(td) / "ptr_array_probe"
        src_path.write_text(PTR_ARRAY_SOURCE, encoding="utf-8")
        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src_path),
                "--backend=c",
                "-o",
                str(out_stem),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert compile_proc.returncode == 0, compile_proc.stderr or compile_proc.stdout
        exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr or run_proc.stdout

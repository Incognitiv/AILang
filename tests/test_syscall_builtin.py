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

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402

AILANG = REPO_ROOT / "ailang.py"


def _to_c_with_needs(src: str) -> tuple[str, object]:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    transpiler = CTranspiler()
    c_code = transpiler.transpile(ast, "<inline>")
    return c_code, transpiler.runtime_needs


def test_syscall_c_backend_emits_unified_runtime_helper() -> None:
    src = """
@effect(syscall)
def main(): int
    r = syscall(39)
    if target_os() == "linux" then
        return r > 0
    end
    return r == -38
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "syscall" in needs.helpers
    assert "ailang_syscall_native" in c_code
    assert "ailang_syscall0" not in c_code
    assert "linux_syscall" not in c_code


def test_syscall_llvm_lowering_is_unified_and_target_safe() -> None:
    src = """
@effect(syscall)
def main(): int
    r = syscall(39)
    return r != 0
end
"""
    ir_text = compile_to_ir_fast(src, source_file="syscall_probe.ail")
    assert "linux_syscall" not in ir_text
    assert "syscall0" not in ir_text
    if sys.platform.startswith("linux"):
        assert '@"syscall"' in ir_text
        assert "syscall_result" in ir_text
    else:
        assert "syscall_result" not in ir_text
        assert "-38" in ir_text


def test_syscall_c_backend_native_smoke_compiles_and_runs() -> None:
    src = """
@effect(syscall)
def main(): int
    r = syscall(39)
    if target_os() == "linux" then
        if r <= 0 then
            return 1
        end
        return 0
    end
    if r != -38 then
        return 2
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "syscall_smoke.ail"
        out_stem = Path(td) / "syscall_smoke"
        src_path.write_text(src, encoding="utf-8")
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
        assert compile_proc.returncode == 0, compile_proc.stderr
        exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr

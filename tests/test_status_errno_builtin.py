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
from runtime.modes import CompilationContext, CompilationMode  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402

AILANG = REPO_ROOT / "ailang.py"


def _to_c_with_needs(src: str) -> tuple[str, object]:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    transpiler = CTranspiler()
    c_code = transpiler.transpile(ast, "<inline>")
    return c_code, transpiler.runtime_needs


def test_errno_c_backend_emits_status_runtime_helper() -> None:
    src = """
def main(): int
    errno_set(5)
    errno_clear()
    return errno_get()
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "status" in needs.helpers
    assert "ailang_errno_get" in c_code
    assert "ailang_errno_clear" in c_code
    assert "ailang_errno_set" in c_code


def test_errno_llvm_lowering_uses_target_errno_accessor() -> None:
    src = """
def main(): int
    errno_set(5)
    return errno_get()
end
"""
    ir_text = compile_to_ir_fast(src, source_file="errno_probe.ail")
    if sys.platform == "win32":
        assert '@"_errno"' in ir_text
    elif sys.platform.startswith("linux"):
        assert '@"__errno_location"' in ir_text
    elif sys.platform.startswith(("darwin", "freebsd", "openbsd", "netbsd")):
        assert '@"__error"' in ir_text


def test_errno_llvm_freestanding_does_not_declare_libc_errno() -> None:
    src = """
def main(): int
    errno_set(5)
    return errno_get()
end
"""
    CompilationContext.set_mode(CompilationMode.FREESTANDING)
    try:
        ir_text = compile_to_ir_fast(src, source_file="errno_freestanding.ail")
    finally:
        CompilationContext.set_mode(CompilationMode.HOSTED)

    assert "__errno_location" not in ir_text
    assert "__error" not in ir_text
    assert "_errno" not in ir_text


def test_errno_c_backend_native_smoke_compiles_and_runs() -> None:
    src = """
def main(): int
    errno_set(7)
    if errno_get() != 7 then
        return 2
    end
    errno_clear()
    if errno_get() != 0 then
        return 1
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "errno_smoke.ail"
        out_stem = Path(td) / "errno_smoke"
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

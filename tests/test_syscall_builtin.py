from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.codegen import CodeGen  # noqa: E402
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


def _to_llvm_with_triple(src: str, triple: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    codegen = CodeGen()
    codegen.module.triple = triple
    return codegen.generate(ast, source_file="syscall_probe.ail")


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
    assert "AILANG_RAW_SYSCALL_X86_64" in c_code
    assert '__asm__("r10")' in c_code
    assert "__asm__ volatile (" in c_code
    assert '"rcx", "r11", "memory"' in c_code
    assert "extern long syscall(long number, ...);" in c_code
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
    linux_x64_ir = _to_llvm_with_triple(src, "x86_64-unknown-linux-gnu")
    assert 'asm sideeffect "syscall"' in linux_x64_ir
    assert (
        "={rax},0,{rdi},{rsi},{rdx},{r10},{r8},{r9},~{rcx},~{r11},~{memory}"
    ) in linux_x64_ir
    assert '@"syscall"' not in linux_x64_ir

    linux_arm64_ir = _to_llvm_with_triple(src, "aarch64-unknown-linux-gnu")
    assert '@"syscall"' in linux_arm64_ir
    assert 'asm sideeffect "syscall"' not in linux_arm64_ir

    windows_ir = _to_llvm_with_triple(src, "x86_64-pc-windows-msvc")
    assert "syscall_result" not in windows_ir
    assert "-38" in windows_ir


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


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or platform.machine().lower() not in {"x86_64", "amd64"},
    reason="raw syscall result contract is currently implemented for Linux x86-64",
)
@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_syscall_backends_return_raw_linux_errno(backend: str) -> None:
    src = """
@effect(syscall)
def main(): int
    if syscall(3, -1) != -9 then
        return 1
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "raw_syscall_errno.ail"
        out_stem = Path(td) / "raw_syscall_errno"
        src_path.write_text(src, encoding="utf-8")
        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src_path),
                f"--backend={backend}",
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
        run_proc = subprocess.run(
            [str(out_stem)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr

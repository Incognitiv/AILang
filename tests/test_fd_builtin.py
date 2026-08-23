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


def test_fd_c_backend_emits_hosted_fd_runtime_helper() -> None:
    src = """
def main(): int
    fd = fd_open("x", 1, 0)
    if fd >= 0 then
        fd_close(fd)
    end
    return fd
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "fd" in needs.helpers
    assert "ailang_fd_open" in c_code
    assert "ailang_fd_read" in c_code
    assert "ailang_fd_write" in c_code
    assert "ailang_fd_close" in c_code
    assert "ailang_fd_dup" in c_code
    assert "ailang_fd_dup2" in c_code
    assert "ailang_fd_tell" in c_code
    assert "ailang_fd_seek" in c_code
    assert "ailang_fd_flush" in c_code


def test_fd_llvm_lowering_uses_target_fd_symbols() -> None:
    src = """
def main(): int
    fd = fd_open("x", 1, 0)
    if fd >= 0 then
        fd2 = fd_dup(fd)
        if fd2 >= 0 then
            fd_tell(fd2)
            fd_seek(fd2, 0)
            fd_close(fd2)
        end
        fd_close(fd)
    end
    return fd
end
"""
    ir_text = compile_to_ir_fast(src, source_file="fd_probe.ail")
    if sys.platform == "win32":
        assert '@"_open"' in ir_text
        assert '@"_close"' in ir_text
        assert '@"_dup"' in ir_text
        assert '@"_lseeki64"' in ir_text
    else:
        assert '@"open"' in ir_text
        assert '@"close"' in ir_text
        assert '@"dup"' in ir_text
        assert '@"lseek"' in ir_text


def test_fd_llvm_freestanding_does_not_declare_hosted_fd_symbols() -> None:
    src = """
def main(): int
    return fd_tell(0)
end
"""
    CompilationContext.set_mode(CompilationMode.FREESTANDING)
    try:
        ir_text = compile_to_ir_fast(src, source_file="fd_freestanding.ail")
    finally:
        CompilationContext.set_mode(CompilationMode.HOSTED)

    assert '@"open"' not in ir_text
    assert '@"_open"' not in ir_text
    assert '@"lseek"' not in ir_text
    assert '@"_lseeki64"' not in ir_text
    assert "-38" in ir_text


def test_fd_dup2_c_backend_smoke_redirects_stdout_and_restores() -> None:
    src = """
def main(): int
    path = "fd_dup2_tmp.txt"
    fd = fd_open(path, 2 + 4 + 8, 420)
    if fd < 0 then
        return 1
    end
    saved = fd_dup(1)
    if saved < 0 then
        fd_close(fd)
        return 2
    end
    if fd_dup2(fd, 1) < 0 then
        fd_close(saved)
        fd_close(fd)
        return 3
    end
    fd_close(fd)
    print "dup2-ok"
    fd_flush()
    if fd_dup2(saved, 1) < 0 then
        fd_close(saved)
        return 4
    end
    fd_close(saved)
    text = read_file(path)
    ok = streq(text, "dup2-ok\\n")
    dealloc(text)
    delete_file(path)
    if ok then
        return 0
    end
    return 5
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "fd_dup2_smoke.ail"
        out_stem = Path(td) / "fd_dup2_smoke"
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
            cwd=td,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr


def test_fd_c_backend_native_smoke_compiles_and_roundtrips() -> None:
    src = """
def main(): int
    path = "fd_smoke_tmp.txt"
    write_flags = 2 + 4 + 8
    fd = fd_open(path, write_flags, 420)
    if fd < 0 then
        return 1
    end
    msg = "abc"
    written = fd_write(fd, msg, 3)
    close_rc = fd_close(fd)
    if written != 3 then
        return 2
    end
    if close_rc != 0 then
        return 3
    end

    read_fd = fd_open(path, 1, 0)
    if read_fd < 0 then
        return 4
    end
    buf = alloc(4)
    got = fd_read(read_fd, buf, 3)
    pos_after_read = fd_tell(read_fd)
    pos_after_seek = fd_seek(read_fd, 1)
    fd_close(read_fd)
    if got != 3 then
        dealloc(buf)
        return 5
    end
    if pos_after_read != 3 then
        dealloc(buf)
        return 9
    end
    if pos_after_seek != 1 then
        dealloc(buf)
        return 10
    end
    if peek8(buf, 0) != 97 then
        dealloc(buf)
        return 6
    end
    if peek8(buf, 1) != 98 then
        dealloc(buf)
        return 7
    end
    if peek8(buf, 2) != 99 then
        dealloc(buf)
        return 8
    end
    dealloc(buf)
    delete_file(path)
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "fd_smoke.ail"
        out_stem = Path(td) / "fd_smoke"
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
            cwd=td,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr

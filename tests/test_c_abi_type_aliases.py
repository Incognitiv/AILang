from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def _compile_and_run(src: Path, out_stem: Path, *, backend: str) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if backend == "llvm" and proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if "llvm toolchain" in msg or "clang not found" in msg or "llc not found" in msg:
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


def test_cabi_decorator_emits_exact_public_header_without_c_types(tmp_path: Path) -> None:
    src = tmp_path / "cabi_decorator.ail"
    header = tmp_path / "cabi_decorator.h"
    generated_c = tmp_path / "cabi_decorator.c"
    src.write_text(
        """\
@export("abi_strlcpy_shape")
@abi("size_t", "char * dst", "const char * src", "size_t dstsize")
def internal_strlcpy_shape(dst: pointer, src: pointer, dstsize: int): int
    return dstsize
end

@export("abi_readonly_probe")
@cabi("int64_t", "const void * data")
def internal_readonly_probe(data: pointer): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(header)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = header.read_text(encoding="utf-8")
    assert "#include <stddef.h>" in text
    assert "size_t abi_strlcpy_shape(char * dst, const char * src, size_t dstsize);" in text
    assert "int64_t abi_readonly_probe(const void * data);" in text

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-c", "-o", str(generated_c)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    c_text = generated_c.read_text(encoding="utf-8")
    assert "size_t abi_strlcpy_shape(char * dst, const char * src, size_t dstsize)" in c_text
    assert "int64_t abi_readonly_probe(const void * data)" in c_text
    assert "typedef __SIZE_TYPE__ size_t;" in c_text
    assert "typedef unsigned long size_t;" not in c_text


def test_internal_boundary_syntax_lowers_to_cabi_metadata(tmp_path: Path) -> None:
    src = tmp_path / "internal_boundary.ail"
    header = tmp_path / "internal_boundary.h"
    generated_c = tmp_path / "internal_boundary.c"
    src.write_text(
        """\
internal size_t abi_internal_copy(
    dst: internal charptr,
    src: internal cstring,
    dstsize: internal size_t
):
    return dstsize
end

internal int abi_internal_default(param):
    return param
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(header)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = header.read_text(encoding="utf-8")
    assert "size_t abi_internal_copy(char * dst, const char * src, size_t dstsize);" in text
    assert "int abi_internal_default(int64_t param);" in text

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-c", "-o", str(generated_c)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    c_text = generated_c.read_text(encoding="utf-8")
    assert "size_t abi_internal_copy(char * dst, const char * src, size_t dstsize)" in c_text
    assert "int abi_internal_default(int64_t param)" in c_text


def test_internal_boundary_supports_stdio_pointer_aliases(tmp_path: Path) -> None:
    src = tmp_path / "internal_stdio_boundary.ail"
    header = tmp_path / "internal_stdio_boundary.h"
    generated_c = tmp_path / "internal_stdio_boundary.c"
    src.write_text(
        """\
#cinclude <stdio.h>

internal int abi_internal_stdio_probe(
    stream: internal fileptr,
    len: internal size_tp
):
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(header)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = header.read_text(encoding="utf-8")
    assert "#include <stdio.h>" in text
    assert "int abi_internal_stdio_probe(FILE * stream, size_t * len);" in text

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-c", "-o", str(generated_c)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    c_text = generated_c.read_text(encoding="utf-8")
    assert "int abi_internal_stdio_probe(FILE * stream, size_t * len)" in c_text


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_pointer_abi_code_stays_normal_ailang_on_backends(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "pointer_abi_run.ail"
    src.write_text(
        """\
int first_byte(src: pointer):
    addr = reinterpret(i64, src)
    return peek8(addr, 0)
end

int write_x(dst: pointer):
    addr = reinterpret(i64, dst)
    poke8(addr, 0, 88)
    poke8(addr, 1, 0)
    return 0
end

int main():
    raw = alloc(2)
    dst = reinterpret(pointer, raw)
    write_x(dst)
    if peek8(raw, 0) != 88 then
        dealloc(raw)
        return 1
    end
    if first_byte(reinterpret(pointer, "abc")) != 97 then
        dealloc(raw)
        return 2
    end
    dealloc(raw)
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / f"pointer_abi_{backend}", backend=backend)

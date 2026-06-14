from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def test_export_decorator_can_choose_c_symbol_name(tmp_path: Path) -> None:
    cc = _available_c_compiler()
    if cc is None:
        pytest.skip("no C compiler available")

    src = tmp_path / "native_alias.ail"
    generated_c = tmp_path / "native_alias.c"
    obj = tmp_path / "native_alias.o"
    consumer = tmp_path / "consumer.c"
    exe = _native_exe(tmp_path / "consumer")
    src.write_text(
        """\
@export("native_answer")
def ailang_answer(): int
    return 42
end
""",
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(tmp_path / "native_alias.h")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    header = (tmp_path / "native_alias.h").read_text(encoding="utf-8")
    assert "int64_t native_answer(void);" in header
    assert "ailang_answer" not in header

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(REPO_ROOT / 'source')!r}); "
                "from transpiler.core import transpile_file; "
                f"transpile_file({str(src)!r}, {str(generated_c)!r})"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    c_text = generated_c.read_text(encoding="utf-8")
    assert "int64_t native_answer(void)" in c_text
    assert "int64_t ailang_answer(void)" not in c_text

    proc = subprocess.run(
        [cc, "-std=gnu23", "-Wall", "-Wextra", "-Werror", "-c", str(generated_c), "-o", str(obj)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    consumer.write_text(
        """\
#include <stdint.h>

int64_t native_answer(void);

int main(void) {
    return native_answer() == 42 ? 0 : 1;
}
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [cc, "-std=gnu23", str(consumer), str(obj), "-o", str(exe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    run = subprocess.run(
        [str(exe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stderr or run.stdout


def test_llvm_export_alias_uses_requested_symbol(tmp_path: Path) -> None:
    src = tmp_path / "native_alias_ir.ail"
    out_ll = tmp_path / "native_alias_ir.ll"
    src.write_text(
        """\
@export("native_answer")
def ailang_answer(): int
    return 42
end

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-llvm", "-o", str(out_ll)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = out_ll.read_text(encoding="utf-8")
    assert '@"native_answer"' in text
    assert '@"ailang_answer"' not in text

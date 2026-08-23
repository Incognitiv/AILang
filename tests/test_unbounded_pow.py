from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

AILANG = REPO_ROOT / "ailang.py"
LEAK_RE = re.compile(r"live at exit:\s*(\d+)\s*bytes", re.IGNORECASE)

# Odd and above INT64_MAX.  Using base -1 proves that the exponent remains a
# BigInt without allocating an astronomically large result.
HUGE_ODD_EXPONENT = 9_223_372_036_854_775_809

PROGRAM = f"""
int main():
    unbounded base = -1
    unbounded exponent = {HUGE_ODD_EXPONENT}
    unbounded result = base ** exponent
    if result != -1 then
        return 41
    end
    return 0
end
"""


def _native_exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


def _compile(source: str, tmp_path: Path, backend: str, name: str) -> Path:
    src = tmp_path / f"{name}.ail"
    out = tmp_path / name
    src.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-O1",
            "-o",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    return _native_exe(out)


def test_unbounded_pow_exponent_is_not_narrowed_to_i64_in_c_source() -> None:
    text = (REPO_ROOT / "source" / "transpiler" / "expr_gen_binary_impl.py").read_text(
        encoding="utf-8"
    )
    assert "ailang_bigint_pow_unbounded_take({left}, {exponent})" in text
    pow_block = text.split('if op in ("**", "^"):', 1)[1].split(
        'if op in ("<<", "shl", ">>", "shr"):', 1
    )[0]
    assert "ailang_bigint_count_take" not in pow_block


def test_unbounded_pow_pure_ail_module_has_no_machine_exponent_conversion() -> None:
    text = (REPO_ROOT / "stdlib" / "core" / "bigint_pow.ail").read_text(
        encoding="utf-8"
    )
    assert "pointer ailang_bigint_pow_unbounded(pointer base, pointer exponent)" in text
    assert "ailang_bigint_limb(e, 0)" in text
    assert "ailang_bigint_shr(e, 1)" in text
    assert "ailang_bigint_to_i64" not in text
    assert "ailang_bigint_count_take" not in text


def test_unbounded_pow_above_i64_max_llvm_aot(tmp_path: Path) -> None:
    if shutil.which("clang") is None:
        pytest.skip("clang is required for LLVM AOT integration test")
    exe = _compile(PROGRAM, tmp_path, "llvm", "pow_llvm")
    proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_unbounded_pow_above_i64_max_jit() -> None:
    pytest.importorskip("llvmlite")
    from codegen.fast_jit import _build_jit_callable

    main_callable, _tracker, status = _build_jit_callable(
        PROGRAM,
        optimize=True,
        jit_opt=3,
        source_file="unbounded_pow_above_i64.ail",
    )
    assert status == "ok"
    assert main_callable is not None
    assert main_callable() == 0


def test_unbounded_pow_above_i64_max_c_backend(tmp_path: Path) -> None:
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        pytest.skip("a C compiler is required for C-backend integration test")
    exe = _compile(PROGRAM, tmp_path, "c", "pow_c")
    proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_str_unbounded_local_is_freed_by_c_backend(tmp_path: Path) -> None:
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        pytest.skip("a C compiler is required for leak integration test")
    source = """
int main():
    unbounded value = 123456789012345678901234567890123456789
    string text = str(value)
    print(text)
    return 0
end
"""
    exe = _compile(source, tmp_path, "c", "str_unbounded_leak")
    env = dict(os.environ)
    env["AILANG_LEAK_REPORT"] = "1"
    proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "123456789012345678901234567890123456789"
    match = LEAK_RE.search(proc.stdout + proc.stderr)
    assert match is not None, proc.stdout + proc.stderr
    assert int(match.group(1)) == 0, proc.stdout + proc.stderr

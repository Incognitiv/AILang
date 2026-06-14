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


def _compile_and_run(
    src: Path, out_stem: Path, *, backend: str
) -> subprocess.CompletedProcess[str]:
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
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
    assert proc.returncode == 0, (
        f"{backend} compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    )

    run_proc = subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, (
        f"{backend} run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    )
    return run_proc


def _run_check(src: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_prefix_and_postfix_increment_decrement_are_statement_sugar(
    tmp_path: Path,
) -> None:
    src = tmp_path / "inc_dec.ail"
    src.write_text(
        """\
int main():
    i = 0
    ++i
    i++
    --i
    i--
    if i != 0 then
        return 1
    end
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / "inc_dec_c", backend="c")
    _compile_and_run(src, tmp_path / "inc_dec_llvm", backend="llvm")


def test_pointer_type_alias_matches_ptr_in_function_surface(tmp_path: Path) -> None:
    src = tmp_path / "pointer_alias.ail"
    src.write_text(
        """\
pointer pointer_identity(context: pointer):
    return context
end

int main():
    raw = alloc(8)
    poke64(raw, 0, 123)
    out = pointer_identity(reinterpret(pointer, raw))
    if peek64(reinterpret(i64, out), 0) != 123 then
        dealloc(raw)
        return 1
    end
    dealloc(raw)
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / "pointer_alias_c", backend="c")
    _compile_and_run(src, tmp_path / "pointer_alias_llvm", backend="llvm")


@pytest.mark.parametrize(
    "expression",
    [
        "x = ++i",
        "x = i++",
        "return take(++i)",
        "return take(i++)",
    ],
)
def test_increment_decrement_expression_forms_are_rejected_by_check(
    tmp_path: Path, expression: str
) -> None:
    src = tmp_path / "bad_inc_dec_expr.ail"
    src.write_text(
        f"""\
int take(v: int):
    return v
end

int main():
    i = 0
    {expression}
    return 0
end
""",
        encoding="utf-8",
    )

    proc = _run_check(src)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "statement-only" in combined

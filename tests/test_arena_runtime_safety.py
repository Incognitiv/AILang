from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _compile_and_run_c(source: str) -> tuple[int, str, str, int, str, str]:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "arena_runtime_safety.ail"
        out_stem = tmp / "arena_runtime_safety"
        src.write_text(source, encoding="utf-8")

        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src),
                "--backend=c",
                "-o",
                str(out_stem),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if compile_proc.returncode != 0:
            return (
                compile_proc.returncode,
                compile_proc.stdout,
                compile_proc.stderr,
                -1,
                "",
                "",
            )

        exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        return (
            compile_proc.returncode,
            compile_proc.stdout,
            compile_proc.stderr,
            run_proc.returncode,
            run_proc.stdout,
            run_proc.stderr,
        )


def test_arena_create_negative_size_traps() -> None:
    source = """\
def main(): int
    a = arena_create(-1)
    print(a)
    return 0
end
"""
    cc_rc, cc_out, cc_err, run_rc, run_out, run_err = _compile_and_run_c(source)
    assert cc_rc == 0, f"C compile failed\nstdout:\n{cc_out}\n\nstderr:\n{cc_err}"
    assert run_rc != 0, f"Expected runtime trap\nstdout:\n{run_out}\n\nstderr:\n{run_err}"
    merged = (run_out + "\n" + run_err).lower()
    assert "arena_create" in merged and "negative size" in merged


def test_arena_alloc_negative_size_traps() -> None:
    source = """\
def main(): int
    a = arena_create(16)
    p = arena_alloc(a, -8)
    print(arena_used(a))
    arena_destroy(a)
    return 0
end
"""
    cc_rc, cc_out, cc_err, run_rc, run_out, run_err = _compile_and_run_c(source)
    assert cc_rc == 0, f"C compile failed\nstdout:\n{cc_out}\n\nstderr:\n{cc_err}"
    assert run_rc != 0, f"Expected runtime trap\nstdout:\n{run_out}\n\nstderr:\n{run_err}"
    merged = (run_out + "\n" + run_err).lower()
    assert "arena_alloc" in merged and "negative size" in merged
    assert "-8" not in run_out

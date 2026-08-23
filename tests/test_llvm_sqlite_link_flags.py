from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

AILANG = REPO_ROOT / "ailang.py"
CORPUS_DIR = REPO_ROOT / "tests" / "corpus"
EXPECTED_PATH = REPO_ROOT / "tests" / "expected_outputs.json"

from cli.compilation import _detect_llvm_link_flags


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _compile_llvm(src: Path, out_stem: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--backend=llvm",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _emit_llvm(src: Path, out_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--emit-llvm",
            "-o",
            str(out_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _run_executable(stem: Path) -> subprocess.CompletedProcess[str]:
    exe = stem.with_suffix(".exe") if os.name == "nt" else stem
    return subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _skip_if_toolchain_unavailable(stdout: str, stderr: str) -> None:
    msg = (stdout + "\n" + stderr).lower()
    if (
        "llvm toolchain" in msg
        or "clang not found" in msg
        or "llc not found" in msg
        or "sqlite" in msg
    ):
        pytest.skip("LLVM/SQLite toolchain unavailable in this environment")


def test_sqlite_link_flag_detected_from_real_corpus_ir() -> None:
    src = CORPUS_DIR / "06_sqlite_demo.ail"
    with tempfile.TemporaryDirectory() as td:
        ll_path = Path(td) / "06_sqlite_demo.ll"
        emit_proc = _emit_llvm(src, ll_path)
        if emit_proc.returncode != 0:
            _skip_if_toolchain_unavailable(emit_proc.stdout, emit_proc.stderr)
            raise AssertionError(
                "LLVM IR emit failed for 06_sqlite_demo.ail\n"
                f"stdout:\n{emit_proc.stdout}\n\nstderr:\n{emit_proc.stderr}"
            )
        ll_text = ll_path.read_text(encoding="utf-8")
    flags = _detect_llvm_link_flags(ll_text, platform="linux")
    assert "-lsqlite3" in flags


def test_sqlite_link_flag_detected_for_indirect_module_symbols() -> None:
    # Main itself does not call SQLite, but imported/lifted module code
    # contributes SQLite extern declarations/calls in the final IR module.
    ll_text = """
define i64 @"main"() {
entry:
  ret i64 0
}

declare i32 @"sqlite3_open"(ptr %".1", ptr %".2")
declare i32 @"sqlite3_exec"(ptr %".1", ptr %".2", ptr %".3", ptr %".4", ptr %".5")

define i64 @"init_imported_db_runtime"(ptr %"db") {
entry:
  %"rc" = call i32 @"sqlite3_exec"(ptr %"db", ptr null, ptr null, ptr null, ptr null)
  %"rc64" = sext i32 %"rc" to i64
  ret i64 %"rc64"
}
"""
    flags = _detect_llvm_link_flags(ll_text, platform="linux")
    assert "-lsqlite3" in flags


def test_llvm_backend_sqlite_demo_compiles_and_runs() -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    src = CORPUS_DIR / "06_sqlite_demo.ail"
    with tempfile.TemporaryDirectory() as td:
        out_stem = Path(td) / "sqlite_demo_llvm"
        compile_proc = _compile_llvm(src, out_stem)
        if compile_proc.returncode != 0:
            _skip_if_toolchain_unavailable(compile_proc.stdout, compile_proc.stderr)
            raise AssertionError(
                "LLVM compile failed for 06_sqlite_demo.ail\n"
                f"stdout:\n{compile_proc.stdout}\n\nstderr:\n{compile_proc.stderr}"
            )
        run_proc = _run_executable(out_stem)
        assert (
            run_proc.returncode == 0
        ), f"LLVM runtime failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
        assert _non_empty_lines(run_proc.stdout) == expected["06_sqlite_demo.ail"]


def test_llvm_backend_sqlite_indirect_import_compiles_and_runs() -> None:
    main_src = """import mod_sql

def main():
    r = open_and_close()
    print(r)
end
"""
    mod_src = """def open_and_close():
    db = sql_open("tmp_indirect.db")
    if db == 0 then
        return -1
    end
    sql_close(db)
    return 1
end
"""

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        main_path = td_path / "main.ail"
        mod_path = td_path / "mod_sql.ail"
        main_path.write_text(main_src, encoding="utf-8")
        mod_path.write_text(mod_src, encoding="utf-8")
        out_stem = td_path / "main_llvm"

        compile_proc = _compile_llvm(main_path, out_stem)
        if compile_proc.returncode != 0:
            _skip_if_toolchain_unavailable(compile_proc.stdout, compile_proc.stderr)
            raise AssertionError(
                "LLVM compile failed for indirect SQLite import case\n"
                f"stdout:\n{compile_proc.stdout}\n\nstderr:\n{compile_proc.stderr}"
            )

        run_proc = _run_executable(out_stem)
        assert (
            run_proc.returncode == 0
        ), f"LLVM runtime failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
        assert _non_empty_lines(run_proc.stdout) == ["1"]

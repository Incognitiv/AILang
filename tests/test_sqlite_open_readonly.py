from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
AILANG = REPO_ROOT / "ailang.py"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from transpiler.core import transpile_file  # noqa: E402


READONLY_SOURCE = """\
def main(): int
    db = sql_open_readonly("__DB_PATH__")
    if db == 0 then
        print("open failed")
        return 1
    end
    if sql_last_open_status() != 0 then
        print("valid-status-failed")
        return 4
    end

    stmt = sql_prepare(db, "SELECT value FROM config WHERE key='mode'")
    rc = sql_step(stmt)
    if rc != 100 then
        print(rc)
        return 2
    end
    print(sql_column_text(stmt, 0))
    sql_finalize(stmt)
    sql_close(db)

    missing = sql_open_readonly("__MISSING_PATH__")
    if missing != 0 then
        sql_close(missing)
        print("created")
        return 3
    end
    if sql_last_open_status() == 0 then
        print("missing-status-failed")
        return 5
    end
    print("missing-ok")
    return 0
end
"""


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO config(key, value) VALUES ('mode', 'readonly')")


def _compile_and_run(source: str, backend: str) -> tuple[int, str, str, bool]:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db_path = tmp / "readonly.db"
        missing_path = tmp / "missing.db"
        _create_db(db_path)
        src = tmp / "readonly.ail"
        out_stem = tmp / f"readonly_{backend}"
        src.write_text(
            source.replace("__DB_PATH__", db_path.as_posix()).replace(
                "__MISSING_PATH__", missing_path.as_posix()
            ),
            encoding="utf-8",
        )

        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src),
                "--backend",
                backend,
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
            return compile_proc.returncode, compile_proc.stdout, compile_proc.stderr, False

        exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        created_missing = missing_path.exists()
        return run_proc.returncode, run_proc.stdout, run_proc.stderr, created_missing


def test_c_backend_sql_open_readonly_uses_readonly_flags() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sqlite_readonly.ail"
        src.write_text(READONLY_SOURCE, encoding="utf-8")
        c_text = transpile_file(str(src))
    assert "static sqlite3 *sql_open_readonly" in c_text
    assert "SQLITE_OPEN_READONLY | SQLITE_OPEN_URI" in c_text
    assert "static int64_t sql_last_open_status" in c_text
    assert "ailang_sql_last_open_status = rc" in c_text


def test_llvm_sql_open_readonly_uses_readonly_flags() -> None:
    ir_text = compile_to_ir_fast(READONLY_SOURCE, source_file="sqlite_readonly.ail")
    assert '@"sqlite3_open_v2"' in ir_text
    assert "ailang_sql_last_open_status" in ir_text
    assert "i32 65" in ir_text
    assert "i32 70" not in ir_text


def test_sql_open_readonly_c_backend_reads_without_creating() -> None:
    rc, out, err, created_missing = _compile_and_run(READONLY_SOURCE, "c")
    if rc != 0:
        msg = (out + "\n" + err).lower()
        if "sqlite3" in msg and ("undefined reference" in msg or "cannot find" in msg):
            pytest.skip("SQLite toolchain unavailable in this environment")
    assert rc == 0, f"C readonly run failed\nstdout:\n{out}\n\nstderr:\n{err}"
    assert _lines(out) == ["readonly", "missing-ok"]
    assert created_missing is False


def test_sql_open_readonly_llvm_backend_reads_without_creating() -> None:
    rc, out, err, created_missing = _compile_and_run(READONLY_SOURCE, "llvm")
    if rc != 0:
        msg = (out + "\n" + err).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or (
                "sqlite3" in msg
                and ("undefined reference" in msg or "cannot find" in msg)
            )
        ):
            pytest.skip("LLVM/SQLite toolchain unavailable in this environment")
    assert rc == 0, f"LLVM readonly run failed\nstdout:\n{out}\n\nstderr:\n{err}"
    assert _lines(out) == ["readonly", "missing-ok"]
    assert created_missing is False

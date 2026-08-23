from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _compile_and_run(source: str, backend: str) -> tuple[int, str, str, int, str, str]:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db_path = (tmp / f"sqlite_bind_{backend}.db").as_posix()
        src = tmp / "sqlite_bind.ail"
        out_stem = tmp / f"sqlite_bind_{backend}"
        src.write_text(source.replace("__DB_PATH__", db_path), encoding="utf-8")

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


def _emit_llvm(source: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db_path = (tmp / "sqlite_bind_ir.db").as_posix()
        src = tmp / "sqlite_bind_ir.ail"
        out_ll = tmp / "sqlite_bind_ir.ll"
        src.write_text(source.replace("__DB_PATH__", db_path), encoding="utf-8")

        proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src),
                "--emit-llvm",
                "-o",
                str(out_ll),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return out_ll.read_text(encoding="utf-8")


SQLITE_BIND_SOURCE = """\
def main(): int
    db = sql_open("__DB_PATH__")
    if db == 0 then
        print("open failed")
        return 1
    end

    sql_exec(db, "CREATE TABLE t (id INTEGER, name TEXT, maybe TEXT)")
    stmt = sql_prepare(db, "INSERT INTO t(id, name, maybe) VALUES (?, ?, ?)")
    if stmt == 0 then
        print("prepare failed")
        return 2
    end

    sql_exec(db, "BEGIN")
    i = 0
    while i < 3 then
        sql_bind_int(stmt, 1, i)
        sql_bind_text_i64(stmt, 2, "name_", i)
        if i == 1 then
            sql_bind_null(stmt, 3)
        else
            sql_bind_text_i64_parts(stmt, 3, "payload_", i, "_ok")
        end
        rc = sql_step(stmt)
        if rc != 101 then
            print(rc)
            return 3
        end
        sql_reset(stmt)
        sql_clear_bindings(stmt)
        i = i + 1
    end
    sql_exec(db, "COMMIT")
    sql_finalize(stmt)

    q = sql_prepare(db, "SELECT COUNT(*), SUM(id) FROM t")
    sql_step(q)
    print(sql_column_int(q, 0))
    print(sql_column_int(q, 1))
    sql_finalize(q)

    q2 = sql_prepare(db, "SELECT name FROM t WHERE id=2")
    sql_step(q2)
    print(sql_column_text(q2, 0))
    sql_finalize(q2)

    q3 = sql_prepare(db, "SELECT maybe FROM t WHERE id=2")
    sql_step(q3)
    print(sql_column_text(q3, 0))
    sql_finalize(q3)

    sql_close(db)
    return 0
end
"""


def test_sqlite_bind_builtins_c_backend() -> None:
    cc_rc, cc_out, cc_err, run_rc, run_out, run_err = _compile_and_run(
        SQLITE_BIND_SOURCE, "c"
    )
    if cc_rc != 0:
        msg = (cc_out + "\n" + cc_err).lower()
        if "sqlite3" in msg and ("undefined reference" in msg or "cannot find" in msg):
            pytest.skip("SQLite toolchain unavailable in this environment")
    assert cc_rc == 0, f"C compile failed\nstdout:\n{cc_out}\n\nstderr:\n{cc_err}"
    assert run_rc == 0, f"C runtime failed\nstdout:\n{run_out}\n\nstderr:\n{run_err}"
    assert _non_empty_lines(run_out) == ["3", "3", "name_2", "payload_2_ok"]


def test_sqlite_bind_builtins_llvm_backend() -> None:
    cc_rc, cc_out, cc_err, run_rc, run_out, run_err = _compile_and_run(
        SQLITE_BIND_SOURCE, "llvm"
    )
    if cc_rc != 0:
        msg = (cc_out + "\n" + cc_err).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or (
                "sqlite3" in msg
                and ("undefined reference" in msg or "cannot find" in msg)
            )
        ):
            pytest.skip("LLVM/SQLite toolchain unavailable in this environment")
    assert cc_rc == 0, f"LLVM compile failed\nstdout:\n{cc_out}\n\nstderr:\n{cc_err}"
    assert run_rc == 0, f"LLVM runtime failed\nstdout:\n{run_out}\n\nstderr:\n{run_err}"
    assert _non_empty_lines(run_out) == ["3", "3", "name_2", "payload_2_ok"]


def test_fast_sqlite_text_bind_llvm_avoids_snprintf() -> None:
    ir_text = _emit_llvm(SQLITE_BIND_SOURCE)
    assert "sql_text_i64_snprintf" not in ir_text
    assert "snprintf" not in ir_text
    assert "sqlite3_bind_text" in ir_text

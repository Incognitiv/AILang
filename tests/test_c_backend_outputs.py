from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
CORPUS_DIR = REPO_ROOT / "tests" / "corpus"
EXPECTED_PATH = REPO_ROOT / "tests" / "expected_outputs.json"


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _compile_and_run_case(src: Path, stem: Path) -> tuple[int, str, str, int, str, str]:
    compile_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--backend=c",
            "-o",
            str(stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
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
    exe = stem.with_suffix(".exe") if os.name == "nt" else stem
    run_proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
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


def test_c_backend_output_golden_subset() -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    cases = [
        "01_hello.ail",
        "02_factorial.ail",
        "03_fibonacci.ail",
        "04_string_concat.ail",
        "05_arena_routed.ail",
    ]
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        for name in cases:
            src = CORPUS_DIR / name
            stem = out_dir / name.replace(".ail", "")
            (
                cc_rc,
                cc_out,
                cc_err,
                run_rc,
                run_out,
                run_err,
            ) = _compile_and_run_case(src, stem)
            assert (
                cc_rc == 0
            ), f"C compile failed for {name}\nstdout:\n{cc_out}\n\nstderr:\n{cc_err}"
            assert (
                run_rc == 0
            ), f"C runtime failed for {name}\nstdout:\n{run_out}\n\nstderr:\n{run_err}"
            got_lines = _non_empty_lines(run_out)
            want_lines = expected[name]
            assert got_lines == want_lines, (
                f"Unexpected output for {name}\n"
                f"got={got_lines!r}\nwant={want_lines!r}"
            )


def test_c_backend_output_sqlite_golden() -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    name = "06_sqlite_demo.ail"
    with tempfile.TemporaryDirectory() as td:
        src = CORPUS_DIR / name
        stem = Path(td) / "06_sqlite_demo"
        cc_rc, cc_out, cc_err, run_rc, run_out, run_err = _compile_and_run_case(
            src, stem
        )
        if cc_rc != 0:
            msg = (cc_out + "\n" + cc_err).lower()
            if "sqlite" in msg:
                pytest.skip("SQLite toolchain unavailable in this environment")
            raise AssertionError(
                f"C compile failed for {name}\nstdout:\n{cc_out}\n\nstderr:\n{cc_err}"
            )
        assert (
            run_rc == 0
        ), f"C runtime failed for {name}\nstdout:\n{run_out}\n\nstderr:\n{run_err}"
        got_lines = _non_empty_lines(run_out)
        want_lines = expected[name]
        assert got_lines == want_lines, (
            f"Unexpected output for {name}\n" f"got={got_lines!r}\nwant={want_lines!r}"
        )

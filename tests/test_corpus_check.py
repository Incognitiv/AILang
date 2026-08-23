from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
CORPUS_DIR = REPO_ROOT / "tests" / "corpus"


def test_corpus_programs_pass_check() -> None:
    names = [
        "01_hello.ail",
        "02_factorial.ail",
        "03_fibonacci.ail",
        "04_string_concat.ail",
        "05_arena_routed.ail",
        "06_sqlite_demo.ail",
    ]
    programs = [CORPUS_DIR / n for n in names]
    for src in programs:
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert (
            proc.returncode == 0
        ), f"--check failed for {src.name}\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

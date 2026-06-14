from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _compile_c(source: str, out_stem: Path) -> Path:
    src_path = out_stem.with_suffix(".ail")
    src_path.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src_path),
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
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out_stem.with_suffix(".exe") if os.name == "nt" else out_stem


def test_c_backend_classifies_sigint_exit_as_interrupted_cleanup() -> None:
    source = """\
#template ansi_c
#include <signal.h>
int64_t ail_raise_sigint(void) { return (int64_t)raise(SIGINT); }
#end

extern fn ail_raise_sigint(): int

def main(): int
    text = "hello" + "world"
    ail_raise_sigint()
    dealloc(text)
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "abnormal_exit_probe")
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

    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0
    assert "** CLEANUP INTERRUPTED **" in output
    assert "classification: not a completed-exit leak" in output
    assert "** POSSIBLE LEAK **" not in output

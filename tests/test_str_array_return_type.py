from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_str_array_function_return_compiles_and_runs_cleanly(tmp_path: Path) -> None:
    src = tmp_path / "str_array_return.ail"
    out_stem = tmp_path / "str_array_return"
    src.write_text(
        """\
def make_words(): str_array
    words = str_array_new(2)
    words = str_array_push(words, "a")
    words = str_array_push(words, "b")
    return words
end

int main():
    words = make_words()
    joined = str_array_join(words, "|")
    print joined
    dealloc(joined)
    dealloc_str_array(words)
    return 0
end
""",
        encoding="ascii",
    )

    compile_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--backend=c",
            "-O2",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stderr

    exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
    env = os.environ.copy()
    env["AILANG_LEAK_REPORT"] = "1"
    run_proc = subprocess.run(
        [str(exe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert run_proc.returncode == 0, run_proc.stderr
    assert run_proc.stdout.splitlines()[0] == "a|b"
    assert "POSSIBLE LEAK" not in run_proc.stderr

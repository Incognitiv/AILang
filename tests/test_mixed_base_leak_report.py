from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
LIVE_AT_EXIT_RE = re.compile(r"live at exit:\s*(\d+)\s*bytes", re.IGNORECASE)


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
    assert proc.returncode == 0, (
        "C backend compile failed:\n"
        f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    )
    return out_stem.with_suffix(".exe") if os.name == "nt" else out_stem


def _run_and_get_live_bytes(exe_path: Path) -> int:
    env = dict(os.environ)
    env["AILANG_LEAK_REPORT"] = "1"
    proc = subprocess.run(
        [str(exe_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, (
        "Mixed-base leak probe runtime failed:\n"
        f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    )
    blob = f"{proc.stdout}\n{proc.stderr}"
    match = LIVE_AT_EXIT_RE.search(blob)
    assert match is not None, (
        "Leak report banner was not found in process output.\n"
        f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    )
    return int(match.group(1))


def test_mixed_base_formatter_leak_report_is_clean_and_stable() -> None:
    source = """\
def main(): int
    print(hex(-1))
    print(oct(-1))
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        out_stem = Path(td) / "mixed_base_probe"
        exe = _compile_c(source, out_stem)
        # Repeated runs to catch unstable/false-positive leak banners.
        lives = [_run_and_get_live_bytes(exe) for _ in range(8)]
        assert all(v == 0 for v in lives), f"non-zero live bytes observed: {lives}"

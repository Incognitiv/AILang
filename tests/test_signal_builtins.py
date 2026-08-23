from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _compile_run(src: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "signal_probe.ail"
    out_stem = tmp_path / "signal_probe"
    source.write_text(src, encoding="utf-8")
    compile_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(source),
            "--backend=c",
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
    return subprocess.run(
        [str(exe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_signal_install_raise_pending_and_drain(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    install_rc = signal_install(15)
    if install_rc != 0 then
        return 1
    end
    raise_rc = signal_raise(15)
    if raise_rc != 0 then
        return 2
    end
    pending = signal_pending()
    drained = signal_drain()
    after = signal_pending()
    if pending != 15 then
        return 3
    end
    if drained != 15 then
        return 4
    end
    if after != 0 then
        return 5
    end
    return 0
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr


def test_signal_clear_removes_pending_signal(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    install_rc = signal_install(2)
    if install_rc != 0 then
        return 1
    end
    raise_rc = signal_raise(2)
    if raise_rc != 0 then
        return 2
    end
    clear_rc = signal_clear(2)
    if clear_rc != 0 then
        return 3
    end
    if signal_pending() != 0 then
        return 4
    end
    return 0
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr


def test_signal_ignore_does_not_record_pending_signal(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    ignore_rc = signal_ignore(15)
    if ignore_rc != 0 then
        return 1
    end
    raise_rc = signal_raise(15)
    if raise_rc != 0 then
        return 2
    end
    pending = signal_pending()
    default_rc = signal_default(15)
    if default_rc != 0 then
        return 3
    end
    if pending != 0 then
        return 4
    end
    return 0
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

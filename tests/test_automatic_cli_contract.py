"""Real CLI contract: write code, run it; no separate linking command."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "ailang.py"


def invoke(source: Path, *options: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), str(source), *options], cwd=source.parent,
        capture_output=True, text=True, timeout=120, check=False,
    )


def test_default_command_compiles_and_executes_imports(tmp_path: Path) -> None:
    (tmp_path / "helper.ail").write_text(
        "int answer():\nreturn 42\nend\n", encoding="utf-8"
    )
    source = tmp_path / "app.ail"
    source.write_text(
        "from helper import answer\nvoid main():\nprint(answer())\nend\n",
        encoding="utf-8",
    )
    result = invoke(source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "42" in result.stdout.splitlines()
    assert not list(tmp_path.glob("*.o"))
    assert not list(tmp_path.glob("*.ll"))


def test_invalid_source_does_not_execute_user_code(tmp_path: Path) -> None:
    source = tmp_path / "broken.ail"
    source.write_text(
        'void main():\nprint("SHOULD-NOT-RUN")\nreturn (\nend\n',
        encoding="utf-8",
    )
    result = invoke(source)
    assert result.returncode != 0
    assert "SHOULD-NOT-RUN" not in result.stdout.splitlines()
    assert "error" in (result.stdout + result.stderr).lower()


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(os, "memfd_create")
    or not (shutil.which("clang") or shutil.which("gcc")),
    reason="memory-native adapter needs Linux and an available native driver",
)
def test_ordinary_output_option_selects_memory_build(tmp_path: Path) -> None:
    source = tmp_path / "app.ail"
    source.write_text('void main():\nprint("automatic-native-ok")\nend\n', encoding="utf-8")
    output = tmp_path / "app"
    result = invoke(source, "-o", str(output), "--profile-phases")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native.link" in result.stdout
    assert "native.object" in result.stdout
    assert "automatic-native-ok" not in result.stdout.splitlines()
    run = subprocess.run([str(output)], capture_output=True, text=True, timeout=10, check=False)
    assert run.returncode == 0
    assert run.stdout.strip() == "automatic-native-ok"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["app", "app.ail"]

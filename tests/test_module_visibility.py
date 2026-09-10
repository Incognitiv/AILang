from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from codegen.fast_jit import fast_jit_file

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


def _write_api(root: Path) -> None:
    (root / "api.ail").write_text(
        "private def hidden():\n"
        "    return 7\n"
        "end\n\n"
        "public def exposed():\n"
        "    return hidden() + 35\n"
        "end\n\n"
        "public def other():\n"
        "    return 99\n"
        "end\n\n"
        "record Pair:\n"
        "    int left\n"
        "    int right\n"
        "end\n\n"
        "class Box:\n"
        "    int base = 40\n"
        "    int value = 0\n\n"
        "    void init(int x):\n"
        "        this.value = x\n"
        "    end\n\n"
        "    int total():\n"
        "        return this.base + this.value\n"
        "    end\n"
        "end\n",
        encoding="utf-8",
    )


def _compile(src: Path, backend: str) -> subprocess.CompletedProcess[str]:
    out = src.parent / f"{src.stem}-{backend}"
    return subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-o",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_public_function_record_class_and_private_helper(
    tmp_path: Path, backend: str
) -> None:
    _write_api(tmp_path)
    src = tmp_path / "main.ail"
    src.write_text(
        "from api import exposed, Pair, Box\n\n"
        "int main():\n"
        "    Pair p = new Pair(40, 2)\n"
        "    Box b = new Box(2)\n"
        "    return (exposed() - 42) + (p.left + p.right - 42) + "
        "(b.total() - 42)\n"
        "end\n",
        encoding="utf-8",
    )
    proc = _compile(src, backend)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run = subprocess.run(
        [str(_exe(tmp_path / f"main-{backend}"))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_public_function_record_class_and_private_helper_jit(tmp_path: Path) -> None:
    _write_api(tmp_path)
    src = tmp_path / "main.ail"
    src.write_text(
        "from api import exposed, Pair, Box\n\n"
        "int main():\n"
        "    Pair p = new Pair(40, 2)\n"
        "    Box b = new Box(2)\n"
        "    return (exposed() - 42) + (p.left + p.right - 42) + "
        "(b.total() - 42)\n"
        "end\n",
        encoding="utf-8",
    )
    assert fast_jit_file(str(src), optimize=False, jit_opt=0) == 0


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_private_function_cannot_be_imported(tmp_path: Path, backend: str) -> None:
    _write_api(tmp_path)
    src = tmp_path / "private_main.ail"
    src.write_text(
        "from api import hidden\n\nint main():\n    return hidden()\nend\n",
        encoding="utf-8",
    )
    proc = _compile(src, backend)
    assert proc.returncode != 0
    assert "hidden" in (proc.stdout + proc.stderr)


def test_private_function_cannot_be_imported_jit(tmp_path: Path) -> None:
    _write_api(tmp_path)
    src = tmp_path / "private_main.ail"
    src.write_text(
        "from api import hidden\n\nint main():\n    return hidden()\nend\n",
        encoding="utf-8",
    )
    with pytest.raises((ImportError, RuntimeError, ValueError)):
        fast_jit_file(str(src), optimize=False, jit_opt=0)


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_private_helper_not_visible_through_public_import(
    tmp_path: Path, backend: str
) -> None:
    _write_api(tmp_path)
    src = tmp_path / "leak_main.ail"
    src.write_text(
        "from api import exposed\n\nint main():\n    return hidden()\nend\n",
        encoding="utf-8",
    )
    proc = _compile(src, backend)
    assert proc.returncode != 0


def test_unrequested_public_function_not_visible_in_jit(tmp_path: Path) -> None:
    _write_api(tmp_path)
    src = tmp_path / "selective_main.ail"
    src.write_text(
        "from api import exposed\n\nint main():\n    return other()\nend\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="not imported|Undefined|unknown"):
        fast_jit_file(str(src), optimize=False, jit_opt=0)

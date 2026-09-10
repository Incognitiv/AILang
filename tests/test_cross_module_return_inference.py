from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from codegen.fast_jit import fast_jit_file

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _write_transitive_program(
    root: Path, *, selective_dependency: bool = False
) -> Path:
    (root / "leaf.ail").write_text(
        "def answer():\n    return 42\nend\n",
        encoding="utf-8",
    )
    dependency = "from leaf import answer" if selective_dependency else "import leaf"
    (root / "defs.ail").write_text(
        f"{dependency}\n\ndef forwarded():\n    return answer()\nend\n",
        encoding="utf-8",
    )
    main = root / "main.ail"
    main.write_text(
        "import defs\n\ndef wrapper():\n    return forwarded()\nend\n\n"
        "int main():\n    return wrapper()\nend\n",
        encoding="utf-8",
    )
    return main


@pytest.mark.parametrize("backend", ["c", "llvm"])
@pytest.mark.parametrize(
    "selective_dependency", [False, True], ids=["import", "from-import"]
)
def test_transitive_import_return_inference_aot(
    tmp_path: Path, backend: str, selective_dependency: bool
) -> None:
    main = _write_transitive_program(
        tmp_path, selective_dependency=selective_dependency
    )
    output = tmp_path / f"main-{backend}"
    compile_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(main),
            f"--backend={backend}",
            "-o",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr
    run_proc = subprocess.run(
        [str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run_proc.returncode == 42, run_proc.stdout + run_proc.stderr


@pytest.mark.parametrize(
    "selective_dependency", [False, True], ids=["import", "from-import"]
)
def test_transitive_import_return_inference_jit(
    tmp_path: Path, selective_dependency: bool
) -> None:
    main = _write_transitive_program(
        tmp_path, selective_dependency=selective_dependency
    )
    assert fast_jit_file(str(main), optimize=False, jit_opt=0) == 42

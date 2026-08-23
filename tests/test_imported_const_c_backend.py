from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _run(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_imported_library_const_int_is_emitted_by_c_backend() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "defs.ail").write_text(
            '@library("defs")\n\nconst int A = 32\n',
            encoding="utf-8",
        )
        main = root / "main.ail"
        main.write_text(
            "import defs\n\nint main():\n    return A\nend\n",
            encoding="utf-8",
        )
        out = root / "main.exe"
        proc = _run(
            [sys.executable, str(AILANG), str(main), "--backend=c", "-o", str(out)]
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        run_proc = _run([str(out)])
        assert run_proc.returncode == 32


def test_invalid_imported_module_reports_import_parse_error() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "defs.ail").write_text(
            '@library("defs")\n\nconst int broken():\n    return 0\nend\n',
            encoding="utf-8",
        )
        main = root / "main.ail"
        main.write_text(
            "import defs\n\nint main():\n    return 0\nend\n",
            encoding="utf-8",
        )
        proc = _run(
            [sys.executable, str(AILANG), str(main), "--backend=c", "-o", str(root / "main.exe")]
        )
        output = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert "failed to parse imported module" in output
        assert "defs.ail" in output

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


def _compile(src: Path, out: Path, backend: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AILANG), str(src), f"--backend={backend}", "-o", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _compile_and_run(src: Path, out: Path, backend: str) -> subprocess.CompletedProcess[str]:
    cp = _compile(src, out, backend)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return subprocess.run(
        [str(_exe(out))], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=False
    )


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_default_argument_obeys_declared_fixed_integer_type(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "default_arg_bad.ail"
    src.write_text(
        """\
int f(u8 x = -1):
    print(x)
    return 0
end

int main():
    return f()
end
""",
        encoding="utf-8",
    )
    run = _compile_and_run(src, tmp_path / f"default_arg_bad_{backend}", backend)
    assert run.returncode != 0
    assert "does not fit u8" in (run.stdout + run.stderr)


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_global_fixed_integer_mutation_is_checked(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "global_mut_bad.ail"
    src.write_text(
        """\
u8 g = 1

int main():
    i256 x = 300
    g = x
    print(g)
    return 0
end
""",
        encoding="utf-8",
    )
    run = _compile_and_run(src, tmp_path / f"global_mut_bad_{backend}", backend)
    assert run.returncode != 0
    assert "does not fit u8" in (run.stdout + run.stderr)


def test_global_fixed_integer_initializer_rejects_out_of_range_literal(tmp_path: Path) -> None:
    src = tmp_path / "global_init_bad.ail"
    src.write_text("u8 g = -1\nint main():\n    return 0\nend\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0
    assert "does not fit u8" in (proc.stdout + proc.stderr)


def test_nonconstant_fixed_integer_global_is_fail_closed(tmp_path: Path) -> None:
    src = tmp_path / "global_expr_bad.ail"
    src.write_text(
        "int a = 5\nint b = a + 1\nint main():\n    return 0\nend\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0
    assert "runtime global initialization is not implemented" in (proc.stdout + proc.stderr)


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_compound_assignment_preserves_u8_value_domain(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "compound_bad.ail"
    src.write_text(
        """\
int main():
    u8 x = 255
    x += 1
    print(x)
    return 0
end
""",
        encoding="utf-8",
    )
    run = _compile_and_run(src, tmp_path / f"compound_bad_{backend}", backend)
    assert run.returncode != 0
    combined = run.stdout + run.stderr
    assert "overflow" in combined.lower() or "does not fit u8" in combined

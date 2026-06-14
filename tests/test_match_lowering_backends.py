from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_string_match_lowers_to_value_comparison(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "string_match.ail"
    src.write_text(
        """\
string classify(op: string):
    match op then
        case "layout.status":
            return "layout"
        case "sh":
            return "shell"
        else:
            return "unknown"
    end
end

int main():
    if streq(classify("sh"), "shell") != 1 then
        return 1
    end
    if streq(classify("layout.status"), "layout") != 1 then
        return 2
    end
    if streq(classify("nope"), "unknown") != 1 then
        return 3
    end
    return 0
end
""",
        encoding="utf-8",
    )

    out = tmp_path / f"string_match_{backend}"
    proc = subprocess.run(
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
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if backend == "llvm" and (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
        if backend == "c" and "no c compiler found" in msg:
            pytest.skip("C compiler unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    run = subprocess.run(
        [str(_native_exe(out))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_match_default_alias_and_arm_local_assignment(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "match_default_alias.ail"
    src.write_text(
        """\
int main():
    default = 7
    if default != 7 then
        return 9
    end

    op = "missing"
    match op then
        case "known":
            msg = "bad" + ""
        default:
            msg = "ok" + ""
    end

    if streq(msg, "ok") != 1 then
        dealloc(msg)
        return 1
    end
    dealloc(msg)
    return 0
end
""",
        encoding="utf-8",
    )

    out = tmp_path / f"match_default_alias_{backend}"
    proc = subprocess.run(
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
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if backend == "llvm" and (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
        if backend == "c" and "no c compiler found" in msg:
            pytest.skip("C compiler unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    run = subprocess.run(
        [str(_native_exe(out))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_integer_match_still_runs(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "integer_match.ail"
    src.write_text(
        """\
int classify(n: int):
    match n then
        case 1:
            return 10
        case 2:
            return 20
        else:
            return 30
    end
end

int main():
    if classify(1) != 10 then
        return 1
    end
    if classify(2) != 20 then
        return 2
    end
    if classify(9) != 30 then
        return 3
    end
    return 0
end
""",
        encoding="utf-8",
    )

    out = tmp_path / f"integer_match_{backend}"
    proc = subprocess.run(
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
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if backend == "llvm" and (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
        if backend == "c" and "no c compiler found" in msg:
            pytest.skip("C compiler unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    run = subprocess.run(
        [str(_native_exe(out))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr

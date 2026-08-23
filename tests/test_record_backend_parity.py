from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def _compile_and_run(src: Path, out_stem: Path, backend: str) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if backend == "llvm" and proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    run_proc = subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stdout + run_proc.stderr
    return [line.strip() for line in run_proc.stdout.splitlines() if line.strip()]


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_record_value_field_access_and_assignment(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "record_value_parity.ail"
    src.write_text(
        """\
record Pair:
    int left
    int right
end

def bumped_sum(p: Pair): int
    p.left = p.left + 5
    return p.left + p.right
end

def main(): int
    Pair p = new Pair(7, 30)
    p.right = p.right + 5
    print(p.left + p.right)
    print(bumped_sum(p))
    print(p.left + p.right)
    return 0
end
""",
        encoding="utf-8",
    )

    assert _compile_and_run(src, tmp_path / f"record_value_{backend}", backend) == [
        "42",
        "47",
        "42",
    ]


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_canonical_colon_declarations_defaults_and_prefix_methods(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "canonical_declarations.ail"
    src.write_text(
        """\
record Pair:
    int left = 99
    int right = 7
end

class Box:
    int base = 40
    int value = 0

    void init(int x):
        this.value = x
    end

    int total():
        return this.base + this.value
    end
end

int identity(int x):
    return x
end

pointer pass_ptr(pointer p):
    return p
end

int main():
    Pair p = new Pair()
    Box b = new Box(2)
    print(p.left + p.right)
    print(b.total())
    print(identity(5))
    return 0
end
""",
        encoding="utf-8",
    )

    assert _compile_and_run(src, tmp_path / f"canonical_{backend}", backend) == [
        "106",
        "42",
        "5",
    ]


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_canonical_trailing_defaults_without_explicit_init(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "canonical_defaults.ail"
    src.write_text(
        """\
record Pair:
    int left
    int right = 7
end

class Plain:
    int left = 40
    int right = 2
end

int main():
    Pair p = new Pair(99)
    Plain q = new Plain()
    print(p.left + p.right)
    print(q.left + q.right)
    return 0
end
""",
        encoding="utf-8",
    )

    assert _compile_and_run(src, tmp_path / f"canonical_defaults_{backend}", backend) == [
        "106",
        "42",
    ]


def _compile_and_run_failure(src: Path, out_stem: Path, backend: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-O0",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if backend == "llvm" and proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if "llvm toolchain" in msg or "clang not found" in msg or "llc not found" in msg:
            pytest.skip("LLVM native toolchain unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("backend", ["c", "llvm"])
@pytest.mark.parametrize(
    "body",
    [
        """\
class Box:
    u8 echo(u8 x):
        return x
    end
end

int main():
    Box b = new Box()
    i8 x = -1
    print(b.echo(x))
    return 0
end
""",
        """\
class Box:
    u8 bad():
        i8 x = -1
        return x
    end
end

int main():
    Box b = new Box()
    print(b.bad())
    return 0
end
""",
    ],
)
def test_type_prefix_method_boundaries_reject_value_changing_conversion(
    tmp_path: Path, backend: str, body: str
) -> None:
    src = tmp_path / "method_boundary.ail"
    src.write_text(body, encoding="utf-8")
    run = _compile_and_run_failure(src, tmp_path / f"method_boundary_{backend}", backend)
    assert run.returncode != 0
    assert "255" not in run.stdout

@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_unannotated_def_return_inference_is_native_and_backend_equal(
    tmp_path: Path, backend: str
) -> None:
    src = tmp_path / "inferred_returns.ail"
    src.write_text(
        """\
def later():
    return text_value()
end

def text_value():
    return "hello"
end

def choose(bool b):
    if b then
        i8 x = -1
        return x
    else
        u8 y = 200
        return y
    end
end

def ping():
    print("ping")
end

class Box:
    u8 value = 7

    def get():
        return this.value
    end
end

int main():
    ping()
    print(later())
    print(choose(false))
    Box b = new Box()
    print(b.get())
    return 0
end
""",
        encoding="utf-8",
    )
    assert _compile_and_run(src, tmp_path / f"inferred_returns_{backend}", backend) == [
        "ping",
        "hello",
        "200",
        "7",
    ]


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_unannotated_void_main_keeps_native_zero_exit(tmp_path: Path, backend: str) -> None:
    src = tmp_path / "void_main.ail"
    src.write_text(
        """\
def main():
    print("ok")
end
""",
        encoding="utf-8",
    )
    assert _compile_and_run(src, tmp_path / f"void_main_{backend}", backend) == ["ok"]

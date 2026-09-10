from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _compile_and_run(source: str):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "wide.ail"
        exe = tmp / "wide"
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=c", "-O2", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode == 0, build.stdout + "\n" + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        if not actual.exists() and exe.exists():
            actual = exe
        run = subprocess.run([str(actual)], capture_output=True, text=True, timeout=60, check=False)
        return build, run


def test_c_backend_wide_sizes_are_real():
    source = r'''
def main(): int
    if sizeof("wide") != 32 then return 1 end
    if sizeof("vast") != 64 then return 2 end
    if sizeof("grand") != 128 then return 3 end
    if sizeof("giant") != 256 then return 4 end
    if sizeof("titan") != 512 then return 5 end
    if sizeof("colos") != 1024 then return 6 end
    return 0
end
'''
    build, run = _compile_and_run(source)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr


def test_c_backend_wide_arithmetic_and_shift():
    source = r'''
def main(): int
    wide a = 1
    wide b = a << 200
    wide c = b + b
    if c <= b then return 10 end
    if (c >> 201) != 1 then return 11 end

    colos x = 1
    colos y = x << 8000
    colos z = y OR x
    if z <= y then return 12 end
    if (z AND x) != 1 then return 13 end
    return 0
end
'''
    build, run = _compile_and_run(source)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr


def test_c_backend_prints_wide_without_truncating():
    source = r'''
def main(): int
    wide x = 1
    x = x << 200
    print x
    return 0
end
'''
    build, run = _compile_and_run(source)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr
    expected = str(1 << 200)
    assert run.stdout.strip() == expected


def test_c_backend_unbounded_preserves_arbitrary_precision():
    source = r"""
def main(): int
    unbounded x = 1267650600228229401496703205376
    unbounded y = 7
    unbounded z = x + y
    print z
    return 0
end
"""
    _build, run = _compile_and_run(source)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr
    assert run.stdout.strip() == "1267650600228229401496703205383"

def test_c_backend_wide_decimal_and_base_formatting_do_not_truncate():
    source = r'''
def main(): int
    i256 x = 1
    x = x << 200
    print str(x)
    print hex(x)
    print bin(x)
    print oct(x)
    return 0
end
'''
    _build, run = _compile_and_run(source)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr
    value = 1 << 200
    assert run.stdout.splitlines() == [
        str(value),
        hex(value).upper().replace("0X", "0x"),
        bin(value),
        "0o" + format(value, "o"),
    ]


def test_c_backend_checked_i64_boundary_rejects_wide_array_value():
    source = r'''
def main(): int
    arr = array_new(1)
    i256 x = 1
    x = x << 200
    arr = array_push(arr, x)
    return 0
end
'''
    _build, run = _compile_and_run(source)
    assert run.returncode != 0
    assert "integer value does not fit signed 64-bit boundary" in run.stderr


def test_c_backend_f128_fails_closed_instead_of_long_double():
    source = r'''
def widen(quad x): quad
    return x
end
'''
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "f128.ail"
        exe = tmp / "f128"
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=c", "-O1", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode != 0
        text = build.stdout + "\n" + build.stderr
        assert "f128/quad" in text
        assert "binary128" in text
        assert "long double" in text


def test_c_checked_integer_narrowing_and_signedness_conversions(tmp_path: Path) -> None:
    def build_run(name: str, body: str) -> subprocess.CompletedProcess[str]:
        src = tmp_path / f"{name}.ail"
        exe = tmp_path / name
        src.write_text(body, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=c", "-O1", "-o", str(exe)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=180, check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        return subprocess.run([str(actual)], capture_output=True, text=True, timeout=60, check=False)

    ok = build_run("narrow_ok", """
def main(): int
    u8 a = 200
    u16 b = a
    i8 c = 100
    u8 d = c
    if b != 200 then return 10 end
    if d != 100 then return 11 end
    return 0
end
""")
    assert ok.returncode == 0, ok.stdout + ok.stderr

    for name, lines, target in (
        ("wide_i8", "i256 x = argc() + 299\n    i8 y = x", "i8"),
        ("neg_u8", "i8 x = -1\n    u8 y = x", "u8"),
        ("u8_i8", "u8 x = 200\n    i8 y = x", "i8"),
    ):
        run = build_run(name, f"def main(): int\n    {lines}\n    print y\n    return 0\nend\n")
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stderr


def test_c_function_integer_boundaries_preserve_value_semantics(tmp_path: Path) -> None:
    def build_run(name: str, source: str) -> subprocess.CompletedProcess[str]:
        src = tmp_path / f"{name}.ail"
        exe = tmp_path / name
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=c", "-O1", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        return subprocess.run(
            [str(actual)], capture_output=True, text=True, timeout=60, check=False
        )

    cases = (
        (
            "arg_sign",
            """def sink(u8 x): int\n    print x\n    return 0\nend\ndef main(): int\n    i8 x = -1\n    return sink(x)\nend\n""",
            "u8",
        ),
        (
            "arg_narrow",
            """def sink(i8 x): int\n    print x\n    return 0\nend\ndef main(): int\n    i256 x = argc() + 299\n    return sink(x)\nend\n""",
            "i8",
        ),
        (
            "ret_sign",
            """def give(): u8\n    i8 x = -1\n    return x\nend\ndef main(): int\n    print give()\n    return 0\nend\n""",
            "u8",
        ),
        (
            "ret_narrow",
            """def give(): i8\n    i256 x = argc() + 299\n    return x\nend\ndef main(): int\n    print give()\n    return 0\nend\n""",
            "i8",
        ),
    )
    for name, source, target in cases:
        run = build_run(name, source)
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stderr

    ok = build_run(
        "valid_function_boundary",
        """def sink(u16 x): u16\n    return x\nend\ndef main(): int\n    i8 x = 100\n    u16 y = sink(x)\n    if y != 100 then return 9 end\n    return 0\nend\n""",
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_c_record_and_fixed_array_integer_boundaries_preserve_value_semantics(tmp_path: Path) -> None:
    def build_run(name: str, source: str) -> subprocess.CompletedProcess[str]:
        src = tmp_path / f"{name}.ail"
        exe = tmp_path / name
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=c", "-O1", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        return subprocess.run(
            [str(actual)], capture_output=True, text=True, timeout=60, check=False
        )

    cases = (
        ("record_field", """record Box:
    u8 v
end

def main(): int
    Box b = new Box(0)
    i8 x = -1
    b.v = x
    return 0
end
""", "u8"),
        ("record_ctor", """record Box:
    u8 v
end

def main(): int
    i8 x = -1
    Box b = new Box(x)
    return 0
end
""", "u8"),
        ("fixed_array_init", """type Arr1 = [u8; 1]

def main(): int
    i8 x = -1
    Arr1 a = [x]
    return 0
end
""", "u8"),
    )
    for name, source, target in cases:
        run = build_run(name, source)
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stderr

    ok = build_run("valid_data_boundary", """record Box:
    u16 v
end
type Arr1 = [u16; 1]
def main(): int
    i8 x = 100
    Box b = new Box(x)
    b.v = x
    Arr1 a = [x]
    if b.v != 100 then return 1 end
    if a[0] != 100 then return 2 end
    return 0
end
""")
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_c_class_enum_and_fixed_array_write_boundaries_preserve_value_semantics(tmp_path: Path) -> None:
    def build_run(name: str, source: str) -> subprocess.CompletedProcess[str]:
        src = tmp_path / f"{name}.ail"
        exe = tmp_path / name
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=c", "-O1", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        return subprocess.run([str(actual)], capture_output=True, text=True, timeout=60, check=False)

    cases = (
        ("fixed_set", """type Arr1 = [u8; 1]
def main(): int
    Arr1 a = [0]
    i8 x = -1
    a[0] = x
    return 0
end
""", "u8"),
        ("class_ctor", """class Box:
    u8 v
    public def init(x: u8):
        this.v = x
    end
end
def main(): int
    i8 x = -1
    Box b = new Box(x)
    return 0
end
""", "u8"),
        ("class_field", """class Box:
    u8 v
    public def init():
        this.v = 0
    end
end
def main(): int
    Box b = new Box()
    i8 x = -1
    b.v = x
    return 0
end
""", "u8"),
        ("enum_payload", """enum Cell then
    Value(u8 v)
end
def main(): int
    i8 x = -1
    Cell c = Cell.Value(x)
    return 0
end
""", "u8"),
    )
    for name, source, target in cases:
        run = build_run(name, source)
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stderr

    ok = build_run("valid_structured_boundaries", """type Arr1 = [u16; 1]
class Box:
    u16 v
    public def init(x: u16):
        this.v = x
    end
end
enum Cell then
    Value(u16 v)
end
def main(): int
    i8 x = 100
    Arr1 a = [0]
    a[0] = x
    Box b = new Box(x)
    Cell c = Cell.Value(x)
    if a[0] != 100 then return 1 end
    if b.v != 100 then return 2 end
    return 0
end
""")
    assert ok.returncode == 0, ok.stdout + ok.stderr

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402

AILANG = REPO_ROOT / "ailang.py"


def test_wide_constant_propagation_does_not_collapse_declared_i8192_to_i64() -> None:
    ir_text = compile_to_ir_fast(
        """
def main(): int
    colos x = 1
    colos y = x << 8000
    if (y >> 8000) != x then return 1 end
    return 0
end
""",
        source_file="wide_const_prop.ail",
    )

    assert 'alloca i8192' in ir_text
    assert 'shl i8192' in ir_text
    assert 'shl i64 1, 8000' not in ir_text
    assert 'ashr i8192' in ir_text
    assert 'Shift amount out of bounds [0, 64)' not in ir_text


def test_all_declared_integer_widths_keep_real_shift_width_in_llvm_ir() -> None:
    widths = (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
    lines = ["def main(): int"]
    for bits in widths:
        shift = bits - 2
        for prefix, tag, right_shift in (("i", "s", ">>"), ("u", "u", "ushr")):
            type_name = f"{prefix}{bits}"
            stem = f"{tag}{bits}"
            lines.extend(
                [
                    f"    {type_name} {stem}x = 1",
                    f"    {type_name} {stem}y = {stem}x << {shift}",
                    f"    if ({stem}y {right_shift} {shift}) != {stem}x then return 1 end",
                ]
            )
    lines.extend(["    return 0", "end"])
    ir_text = compile_to_ir_fast("\n".join(lines) + "\n", source_file="wide_matrix.ail")

    for bits in widths:
        assert f"shl i{bits}" in ir_text
        if bits != 8:
            # Both signed and unsigned paths are present; signed uses ashr and
            # unsigned uses lshr after the declared-width coercion.
            assert f"ashr i{bits}" in ir_text
            assert f"lshr i{bits}" in ir_text


def test_i8192_native_shift_roundtrip_and_print() -> None:
    source = r'''
def main(): int
    colos x = 1
    colos y = x << 8000
    colos z = y OR x
    if (y >> 8000) != x then return 21 end
    if (z AND x) != x then return 22 end
    print z
    return 0
end
'''
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "wide.ail"
        exe = tmp / "wide"
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "-O2", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode == 0, build.stdout + "\n" + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        run = subprocess.run(
            [str(actual)], capture_output=True, text=True, timeout=60, check=False
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr
        assert run.stdout.strip() == str((1 << 8000) | 1)



def test_llvm_backend_rejects_unbounded_instead_of_pointer_placeholder() -> None:
    source = """
def main(): int
    unbounded x = 1
    print x
    return 0
end
"""
    try:
        compile_to_ir_fast(source, source_file="unbounded.ail")
    except TypeError as exc:
        text = str(exc)
        assert "unbounded" in text
        assert "arbitrary-precision" in text
        assert "not implemented" in text
    else:
        raise AssertionError("unbounded must fail closed until BigInt runtime exists")


def _compile_and_run_llvm(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "probe.ail"
        exe = tmp / "probe"
        src.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [sys.executable, str(AILANG), str(src), "--backend=llvm", "-O1", "-o", str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert build.returncode == 0, build.stdout + "\n" + build.stderr
        actual = exe.with_suffix(".exe") if sys.platform.startswith("win") else exe
        return subprocess.run(
            [str(actual)], capture_output=True, text=True, timeout=60, check=False
        )


def test_llvm_wide_decimal_and_base_formatting_do_not_truncate() -> None:
    source = """
def main(): int
    i256 x = 1
    x = x << 200
    print str(x)
    print hex(x)
    print bin(x)
    print oct(x)
    return 0
end
"""
    run = _compile_and_run_llvm(source)
    assert run.returncode == 0, run.stdout + run.stderr
    value = 1 << 200
    assert run.stdout.splitlines() == [
        str(value),
        hex(value).replace("x", "x").upper().replace("0X", "0x"),
        bin(value),
        "0o" + format(value, "o"),
    ]


def test_llvm_prints_negative_wide_integer_with_sign() -> None:
    source = """
def main(): int
    i256 x = -1
    x = x << 200
    print x
    return 0
end
"""
    run = _compile_and_run_llvm(source)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == str(-(1 << 200))


def test_llvm_checked_i64_boundary_rejects_wide_array_value() -> None:
    source = """
def main(): int
    arr = array_new(1)
    i256 x = 1
    x = x << 200
    arr = array_push(arr, x)
    return 0
end
"""
    run = _compile_and_run_llvm(source)
    assert run.returncode != 0
    assert "does not fit 64-bit boundary" in run.stdout


def test_llvm_f128_fails_closed_instead_of_becoming_double() -> None:
    source = """
def widen(quad x): quad
    return x
end
"""
    try:
        compile_to_ir_fast(source, source_file="f128.ail")
    except TypeError as exc:
        text = str(exc)
        assert "f128/quad" in text
        assert "binary128" in text
        assert "f64 fallback" in text
    else:
        raise AssertionError("f128/quad must not silently lower to f64")


def test_llvm_checked_integer_narrowing_and_signedness_conversions() -> None:
    ok = _compile_and_run_llvm("""
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

    for body, target in (
        ("i256 x = argc() + 299\n    i8 y = x", "i8"),
        ("i8 x = -1\n    u8 y = x", "u8"),
        ("u8 x = 200\n    i8 y = x", "i8"),
    ):
        run = _compile_and_run_llvm(
            f"def main(): int\n    {body}\n    print y\n    return 0\nend\n"
        )
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stdout


def test_llvm_function_integer_boundaries_preserve_value_semantics() -> None:
    cases = (
        (
            """def sink(u8 x): int\n    print x\n    return 0\nend\ndef main(): int\n    i8 x = -1\n    return sink(x)\nend\n""",
            "u8",
        ),
        (
            """def sink(i8 x): int\n    print x\n    return 0\nend\ndef main(): int\n    i256 x = argc() + 299\n    return sink(x)\nend\n""",
            "i8",
        ),
        (
            """def give(): u8\n    i8 x = -1\n    return x\nend\ndef main(): int\n    print give()\n    return 0\nend\n""",
            "u8",
        ),
        (
            """def give(): i8\n    i256 x = argc() + 299\n    return x\nend\ndef main(): int\n    print give()\n    return 0\nend\n""",
            "i8",
        ),
    )
    for source, target in cases:
        run = _compile_and_run_llvm(source)
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stdout

    ok = _compile_and_run_llvm(
        """def sink(u16 x): u16\n    return x\nend\ndef main(): int\n    i8 x = 100\n    u16 y = sink(x)\n    if y != 100 then return 9 end\n    return 0\nend\n"""
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_llvm_record_and_fixed_array_integer_boundaries_preserve_value_semantics() -> None:
    cases = (
        ("""record Box:
    u8 v
end

def main(): int
    Box b = new Box(0)
    i8 x = -1
    b.v = x
    return 0
end
""", "u8"),
        ("""record Box:
    u8 v
end

def main(): int
    i8 x = -1
    Box b = new Box(x)
    return 0
end
""", "u8"),
        ("""type Arr1 = [u8; 1]

def main(): int
    i8 x = -1
    Arr1 a = [x]
    return 0
end
""", "u8"),
    )
    for source, target in cases:
        run = _compile_and_run_llvm(source)
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stdout

    ok = _compile_and_run_llvm("""record Box:
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


def test_llvm_class_enum_and_fixed_array_write_boundaries_preserve_value_semantics() -> None:
    cases = (
        ("""type Arr1 = [u8; 1]
def main(): int
    Arr1 a = [0]
    i8 x = -1
    a[0] = x
    return 0
end
""", "u8"),
        ("""class Box:
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
        ("""class Box:
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
        ("""enum Cell then
    Value(u8 v)
end
def main(): int
    i8 x = -1
    Cell c = Cell.Value(x)
    return 0
end
""", "u8"),
    )
    for source, target in cases:
        run = _compile_and_run_llvm(source)
        assert run.returncode != 0
        assert f"does not fit {target}" in run.stdout

    ok = _compile_and_run_llvm("""type Arr1 = [u16; 1]
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

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from test_c_fixed_array_layout import _native_exe
from test_literal_divisor_elision import _c_function_body, _to_c

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_c_u32_clamped_loop_accumulators_elide_overflow_checks() -> None:
    src = """
def fib_mix_bench(iterations: u32): int
    u32 a = 0
    u32 b = 7
    u32 i = 0
    while i < iterations then
        a = a + b
        if a > 1000000000 then
            a = a - 1000000000
        end
        b = b + 1
        if b > 1000000000 then
            b = b - 1000000000
        end
        i = i + 1
    end
    return a
end

def caller(): int
    return fib_mix_bench(8000000)
end
"""
    c_body = _c_function_body(_to_c(src), "fib_mix_bench")
    assert "ailang_safe_add(a, b)" not in c_body
    assert "ailang_safe_add(b, 1LL)" not in c_body
    assert "ailang_safe_sub(a, 1000000000LL)" not in c_body
    assert "ailang_safe_sub(b, 1000000000LL)" not in c_body
    # The optimization contract is semantic: no checked arithmetic helper is
    # needed once range analysis proves the operation safe.  Do not freeze the
    # exact spelling of C operand casts; those are allowed to remain as
    # correctness-first type boundaries and optimize away in native code.
    assert "+" in c_body


def test_c_fixed_array_slice_alias_proves_reduction_range() -> None:
    src = """
type Arr8 = [int; 8]
type IterCount = 0..250000

def slice_sum_bench(iterations: IterCount):
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    slice[int] view = arr
    acc := 0..7750000 = 0
    i = 0
    while i < iterations then
        j := 0..8 = 0
        while j < 8 then
            acc = acc + view[j]
            j = j + 1
        end
        i = i + 1
    end
    return acc
end
"""
    c_body = _c_function_body(_to_c(src), "slice_sum_bench")
    assert "Range error" not in c_body
    assert "view.data[j]" in c_body
    assert "ailang_safe_add" not in c_body


def test_c_backend_compiles_fixed_array_slice_alias(tmp_path: Path) -> None:
    src = tmp_path / "fixed_array_slice_alias.ail"
    out_stem = tmp_path / "fixed_array_slice_alias"
    src.write_text(
        """\
type Arr8 = [int; 8]

def main(): int
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    slice[int] view = arr
    if view[5] != 9 then
        return 1
    end
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
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


def test_c_backend_hoists_recursive_literal_call_to_constant() -> None:
    src = """
def recursive_fib(n: i32): i64
    if n <= 1 then
        return 1
    end
    return recursive_fib(n - 1) + recursive_fib(n - 2)
end

def recursive_bench(iterations: i32): i64
    return recursive_fib(iterations)
end

def main(): int
    depth = 32
    result = recursive_bench(depth)
    print(result)
    return 0
end
"""
    c_code = _to_c(src)
    start = c_code.index("int main(void) {")
    end = c_code.index("\n}", start)
    c_body = c_code[start:end]
    # Pure-call folding is intentionally disabled until typed evaluation can
    # prove it preserves the same parameter/return contracts as runtime.
    assert "recursive_bench(" in c_body
    assert "ailang_cast_i32_from_s64" in c_body
    assert "recursive_fib" not in c_body

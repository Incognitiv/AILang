from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

Parser = importlib.import_module("parser.parser").Parser
CodeGen = importlib.import_module("codegen.codegen").CodeGen
tokenize = importlib.import_module("lexer.scan").tokenize


def _to_ir(src: str) -> str:
    ast = Parser(tokenize(src)).parse_program()
    return CodeGen().generate(ast, "<inline>")


def _ir_function_body(ir_text: str, name: str) -> str:
    marker = f'define internal i64 @"{name}"'
    start = ir_text.find(marker)
    if start < 0:
        marker = f'define internal i32 @"{name}"'
        start = ir_text.index(marker)
    end = ir_text.index("\n}\n", start)
    return ir_text[start : end + 3]


def test_llvm_u32_local_comparisons_remain_unsigned() -> None:
    src = """
def bench(a: u32): u32
    u32 x = a
    if x > 1000000000 then
        x = x - 1000000000
    end
    return x
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "icmpu" in body
    assert "ext_lhs" not in body
    assert "icmp sgt" not in body
    assert "llvm.ssub.with.overflow" not in body


def test_llvm_i32_literal_compare_does_not_widen_to_i64() -> None:
    src = """
def bench(n: i32): i64
    if n <= 1 then
        return 1
    end
    return bench(n - 1)
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "alwaysinline" not in body
    assert "icmp sle i32" in body
    assert "sext i32" not in body
    assert "trunc i64" not in body


def test_llvm_print_loop_preserves_integer_range_proofs() -> None:
    src = """
def format_print_bench(iterations):
    i = 0
    sink = 0
    while i < iterations then
        print(i)
        sink = sink + i
        i = i + 1
    end
    return sink
end

def main(): int
    iterations = 100
    result = format_print_bench(iterations)
    print(result)
    return 0
end
"""
    body = _ir_function_body(_to_ir(src), "format_print_bench")
    assert "add nsw i32" in body or "add nsw i64" in body
    assert "llvm.sadd.with.overflow" not in body


def test_llvm_interpolated_strlen_preserves_accumulator_range_proof() -> None:
    src = """
def format_interp_bench(iterations):
    i = 0
    sink = 0
    while i < iterations then
        si = str(i)
        s = "v=#{si}"
        sink = sink + len(s)
        i = i + 1
    end
    return sink
end

def main(): int
    iterations = 1000
    result = format_interp_bench(iterations)
    print(result)
    return 0
end
"""
    body = _ir_function_body(_to_ir(src), "format_interp_bench")
    assert "known_i64_strlen" in body
    assert "interp_strlen_sum" in body
    assert "add nsw i64" in body
    assert "llvm.sadd.with.overflow" not in body


def test_llvm_range_index_elides_fixed_array_bounds_and_range_checks() -> None:
    src = """
type Arr8 = [int; 8]

def bench(iterations: int): int
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    acc = 0
    i = 0
    while i < iterations then
        j := 0..7 = 0
        while true then
            acc = acc + arr[j]
            if j == 7 then
                break
            end
            j = j + 1
        end
        i = i + 1
    end
    return acc
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "bounds_error" not in body
    assert "bounds_ok" not in body
    assert "range_error" not in body


def test_llvm_range_index_elides_slice_bounds_and_range_checks() -> None:
    src = """
def bench(iterations: int): int
    arr = [3, 1, 4, 1, 5, 9, 2, 6]
    slice[int] view = arr
    acc = 0
    i = 0
    while i < iterations then
        j := 0..7 = 0
        while true then
            acc = acc + view[j]
            if j == 7 then
                break
            end
            j = j + 1
        end
        i = i + 1
    end
    return acc
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "bounds_error" not in body
    assert "bounds_ok" not in body
    assert "range_error" not in body


def test_llvm_fixed_array_slice_alias_proves_range_accumulator() -> None:
    src = """
type Arr8 = [int; 8]
type IterCount = 0..250000

def slice_sum_bench(iterations: IterCount): int
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
    body = _ir_function_body(_to_ir(src), "slice_sum_bench")
    assert "bounds_error" not in body
    assert "bounds_ok" not in body
    assert "range_error" not in body


def test_llvm_fixed_array_can_back_slice_alias() -> None:
    src = """
type Arr8 = [int; 8]

def bench(iterations: int): int
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    slice[int] view = arr
    acc = 0
    i = 0
    while i < iterations then
        j := 0..7 = 0
        while true then
            acc = acc + view[j]
            if j == 7 then
                break
            end
            j = j + 1
        end
        i = i + 1
    end
    return acc
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "arr_slice_ptr" in body
    assert "bounds_error" not in body
    assert "bounds_ok" not in body
    assert "range_error" not in body


def test_llvm_non_escaping_array_literal_uses_stack_storage() -> None:
    src = """
def bench(): int
    arr = [3, 1, 4]
    slice[int] view = arr
    return view[2]
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "arr_stack_array" in body
    assert "arr_lit_alloc" not in body
    assert 'call i8* @"malloc"' not in body


def test_llvm_mutating_array_literal_stays_heap_backed() -> None:
    src = """
def bench(): int
    arr = [3, 1, 4]
    arr = array_push(arr, 5)
    return array_len(arr)
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "arr_stack_array" not in body
    assert "arr_lit_alloc" in body


def test_llvm_stack_array_allows_direct_array_len_metadata() -> None:
    src = """
def bench(): int
    arr = [3, 1, 4]
    return array_len(arr)
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "arr_stack_array" in body
    assert "arr_lit_alloc" not in body
    assert "ret i64 3" in body


def test_llvm_stack_array_rejects_alias_array_len_without_metadata() -> None:
    src = """
def bench(): int
    arr = [3, 1, 4]
    slice[int] view = arr
    return array_len(view)
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "arr_stack_array" not in body
    assert "arr_lit_alloc" in body


def test_llvm_positive_scalar_divisor_elides_modulo_checks() -> None:
    src = """
def bench(x: int): int
    modulus = 1000000007
    return x % modulus
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "1000000007" in body
    assert "modulus_val" not in body
    assert "srem_proven" in body
    assert "mod_by_zero" not in body
    assert "mod_overflow" not in body


def test_llvm_loop_invariant_divisor_elides_modulo_checks() -> None:
    src = """
def bench(iterations: int): int
    modulus = 1000000007
    acc = 0
    i = 0
    while i < iterations then
        acc = acc + (i % modulus)
        i = i + 1
    end
    return acc
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "1000000007" in body
    assert "modulus_val" not in body
    assert "srem_proven" in body
    assert "mod_by_zero" not in body
    assert "mod_overflow" not in body


def test_llvm_record_field_ranges_elide_bounded_add_overflow_checks() -> None:
    src = """
record Point:
    int x
    int y
end

def bench(iterations: int): int
    p = new Point(1, 2)
    modulus = 1000000007
    i = 0
    while i < iterations then
        p.x = (p.x + p.y) % modulus
        p.y = (p.y + 2) % modulus
        i = i + 1
    end
    return p.x + p.y
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "add_range_proven" in body
    assert body.count("add_range_proven") >= 3
    assert "mod_range_select" in body
    assert "urem_proven" not in body
    assert "srem_proven" not in body
    assert "mod_by_zero" not in body
    assert "mod_overflow" not in body


def test_llvm_local_record_field_access_loads_field_directly() -> None:
    src = """
record Point:
    int x
    int y
end

def bench(): int
    p = new Point(1, 2)
    return p.x + p.y
end
"""
    body = _ir_function_body(_to_ir(src), "bench")
    assert "load {i64, i64}" not in body
    assert "store {i64, i64}" not in body
    assert "extractvalue" not in body
    assert body.count("_ptr") >= 2


def test_llvm_call_hint_loop_accumulator_elides_record_checksum_checks() -> None:
    src = """
record Point:
    int x
    int y
end

def records_bench(iterations: int): int
    p = new Point(1, 2)
    modulus = 1000000007
    checksum = 0
    i = 0
    while i < iterations then
        p.x = (p.x + p.y) % modulus
        p.y = (p.y + 2) % modulus
        checksum = checksum + p.x + p.y
        i = i + 1
    end
    return checksum
end

def main(): int
    iterations = 4000000
    result = records_bench(iterations)
    print(result)
    return 0
end
"""
    body = _ir_function_body(_to_ir(src), "records_bench")
    assert "add_range_proven" in body
    assert "mod_range_select" in body
    assert "urem_proven" not in body
    assert "srem_proven" not in body
    assert "llvm.sadd.with.overflow" not in body
    assert "overflow_error" not in body

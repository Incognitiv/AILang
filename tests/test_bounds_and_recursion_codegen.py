from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser  # noqa: E402

from codegen.codegen import CodeGen  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402


def _parse(src: str):
    return Parser(tokenize(src)).parse_program()


def _to_c(src: str) -> str:
    return CTranspiler().transpile(_parse(src), "<inline>")


def _to_ir(src: str) -> str:
    return CodeGen().generate(_parse(src), "<inline>")


def _c_function_body(c_code: str, name: str) -> str:
    marker = f"int64_t {name}("
    search_from = 0
    while True:
        start = c_code.index(marker, search_from)
        brace = c_code.find("{", start)
        semicolon = c_code.find(";", start)
        if brace != -1 and (semicolon == -1 or brace < semicolon):
            break
        search_from = start + len(marker)
    depth = 0
    for i in range(brace, len(c_code)):
        if c_code[i] == "{":
            depth += 1
        elif c_code[i] == "}":
            depth -= 1
            if depth == 0:
                return c_code[brace : i + 1]
    raise AssertionError(f"could not extract C function body for {name}")


def _ir_function_body(ir_text: str, name: str) -> str:
    start = ir_text.index(f'define internal i64 @"{name}"')
    end = ir_text.index("\n}\n", start)
    return ir_text[start : end + 3]


def test_c_literal_array_index_elides_dead_safe_array_helper() -> None:
    src = """
def pick(): int
    arr = [10, 20, 30, 40]
    return arr[2]
end
"""
    c_code = _to_c(src)
    body = _c_function_body(c_code, "pick")
    assert "ailang_safe_array_get" not in c_code
    assert "return arr.data[2LL];" in body


def test_c_out_of_bounds_literal_array_index_keeps_guard() -> None:
    src = """
def pick(): int
    arr = [10, 20, 30, 40]
    return arr[4]
end
"""
    body = _c_function_body(_to_c(src), "pick")
    assert "ailang_safe_array_get" in body


def test_llvm_literal_array_index_elides_bounds_blocks() -> None:
    src = """
def pick(): int
    arr = [10, 20, 30, 40]
    return arr[2]
end
"""
    body = _ir_function_body(_to_ir(src), "pick")
    assert "bounds_error" not in body
    assert "bounds_ok" not in body


def test_llvm_fixed_array_literal_index_elides_bounds_blocks() -> None:
    src = """
type Arr4 = [int; 4]

def pick(): int
    Arr4 arr = [10, 20, 30, 40]
    return arr[2]
end
"""
    body = _ir_function_body(_to_ir(src), "pick")
    assert "bounds_error" not in body
    assert "bounds_ok" not in body


def test_llvm_out_of_bounds_literal_array_index_keeps_guard() -> None:
    src = """
def pick(): int
    arr = [10, 20, 30, 40]
    return arr[4]
end
"""
    body = _ir_function_body(_to_ir(src), "pick")
    assert "bounds_error" in body
    assert "bounds_ok" in body


def test_c_range_loop_exit_refinement_elides_redundant_range_checks() -> None:
    src = """
def sum_range(iterations: int): int
    arr = [3, 1, 4, 1, 5, 9, 2, 6]
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
    body = _c_function_body(_to_c(src), "sum_range")
    assert "Range error" not in body
    assert '__ailang_safety_trap("range error")' not in body


def test_c_recursive_scalar_return_uses_declared_temp_type() -> None:
    src = """
def recursive_fib(n: int): int
    if n <= 1 then
        return 1
    end
    return recursive_fib(n - 1) + recursive_fib(n - 2)
end
"""
    body = _c_function_body(_to_c(src), "recursive_fib")
    assert "typeof(" not in body
    assert "int64_t __ret_val" in body


def test_c_bounded_decreasing_recursion_elides_hot_guard() -> None:
    src = """
def recursive_fib(n: i32): i64
    if n <= 1 then
        return 1
    end
    return recursive_fib(n - 1) + recursive_fib(n - 2)
end

def main(): int
    return recursive_fib(32)
end
"""
    body = _c_function_body(_to_c(src), "recursive_fib")
    assert "__ailang_check_recursion" not in body
    assert "recursive_fib(" in body
    # Correctness-first call boundaries preserve the declared i32 contract via
    # checked conversion; the recursion optimization only elides the hot guard.
    assert "ailang_cast_i32_from_s64" in body


def test_c_unknown_recursion_keeps_hot_guard() -> None:
    src = """
def recursive_fib(n: int): int
    if n <= 1 then
        return 1
    end
    return recursive_fib(n - 1) + recursive_fib(n - 2)
end

def main(n: int): int
    return recursive_fib(n)
end
"""
    body = _c_function_body(_to_c(src), "recursive_fib")
    assert '__ailang_check_recursion("recursive_fib")' in body


def test_c_nondecreasing_recursion_keeps_hot_guard() -> None:
    src = """
def bad(n: int): int
    if n <= 1 then
        return 1
    end
    return bad(n + 1)
end

def main(): int
    return bad(4)
end
"""
    body = _c_function_body(_to_c(src), "bad")
    assert '__ailang_check_recursion("bad")' in body


def test_c_private_loop_bound_param_narrows_but_default_counter_stays_i64() -> None:
    src = """
def hot(iterations: int): int
    acc = 0
    i = 0
    while i < iterations then
        acc = acc + i
        i = i + 1
    end
    return acc
end

def main(): int
    return hot(100)
end
"""
    c_code = _to_c(src)
    assert "int64_t hot(int32_t iterations);" in c_code
    body = _c_function_body(c_code, "hot")
    assert "int64_t i;" in body


def test_c_arithmetic_param_keeps_i64_storage() -> None:
    src = """
def scale(n: int): int
    return n * n
end

def main(): int
    return scale(100)
end
"""
    c_code = _to_c(src)
    assert "int64_t scale(int64_t n);" in c_code
    assert "int64_t scale(int32_t n);" not in c_code

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


def test_c_positive_literal_divisor_elides_division_and_modulo_checks() -> None:
    src = """
def div_lit(n: int): int
    return n / 10
end

def mod_lit(n: int): int
    return n % 10
end
"""
    c_code = _to_c(src)
    div_body = _c_function_body(c_code, "div_lit")
    mod_body = _c_function_body(c_code, "mod_lit")
    assert "ailang_safe_div(" not in div_body
    assert "return (n / 10LL);" in div_body
    assert "ailang_safe_mod(" not in mod_body
    assert "return (n % 10LL);" in mod_body


def test_c_zero_and_negative_literal_divisors_keep_runtime_checks() -> None:
    src = """
def div_zero(n: int): int
    return n / 0
end

def div_neg_one(n: int): int
    return n / -1
end

def mod_zero(n: int): int
    return n % 0
end

def mod_neg_one(n: int): int
    return n % -1
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_div(n, 0LL)" in _c_function_body(c_code, "div_zero")
    assert "ailang_safe_div(n, (-1LL))" in _c_function_body(c_code, "div_neg_one")
    assert "ailang_safe_mod(n, 0LL)" in _c_function_body(c_code, "mod_zero")
    assert "ailang_safe_mod(n, (-1LL))" in _c_function_body(c_code, "mod_neg_one")


def test_llvm_positive_literal_divisor_elides_division_and_modulo_checks() -> None:
    src = """
def div_lit(n: int): int
    return n / 10
end

def mod_lit(n: int): int
    return n % 10
end
"""
    ir_text = _to_ir(src)
    div_body = _ir_function_body(ir_text, "div_lit")
    mod_body = _ir_function_body(ir_text, "mod_lit")
    assert "sdiv_proven" in div_body
    assert "div_by_zero" not in div_body
    assert "div_overflow" not in div_body
    assert "srem_proven" in mod_body
    assert "mod_by_zero" not in mod_body
    assert "mod_overflow" not in mod_body


def test_llvm_zero_and_negative_literal_divisors_keep_runtime_checks() -> None:
    src = """
def div_zero(n: int): int
    return n / 0
end

def div_neg_one(n: int): int
    return n / -1
end

def mod_zero(n: int): int
    return n % 0
end

def mod_neg_one(n: int): int
    return n % -1
end
"""
    ir_text = _to_ir(src)
    div_zero = _ir_function_body(ir_text, "div_zero")
    div_neg_one = _ir_function_body(ir_text, "div_neg_one")
    mod_zero = _ir_function_body(ir_text, "mod_zero")
    mod_neg_one = _ir_function_body(ir_text, "mod_neg_one")
    assert "div_by_zero" in div_zero
    assert "div_overflow" in div_neg_one
    assert "mod_by_zero" in mod_zero
    assert "mod_overflow" in mod_neg_one


def test_c_neutral_integer_arithmetic_elides_overflow_helpers() -> None:
    src = """
def add_zero(n: int): int
    return n + 0
end

def zero_add(n: int): int
    return 0 + n
end

def sub_zero(n: int): int
    return n - 0
end

def mul_one(n: int): int
    return n * 1
end

def one_mul(n: int): int
    return 1 * n
end

def mul_zero(n: int): int
    return n * 0
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add" not in c_code
    assert "ailang_safe_sub" not in c_code
    assert "ailang_safe_mul" not in c_code
    assert "return (n + 0LL);" in _c_function_body(c_code, "add_zero")
    assert "return (0LL + n);" in _c_function_body(c_code, "zero_add")
    assert "return (n - 0LL);" in _c_function_body(c_code, "sub_zero")
    assert "return (n * 1LL);" in _c_function_body(c_code, "mul_one")
    assert "return (1LL * n);" in _c_function_body(c_code, "one_mul")
    assert "return (n * 0LL);" in _c_function_body(c_code, "mul_zero")


def test_c_non_neutral_integer_arithmetic_keeps_overflow_helpers() -> None:
    src = """
def zero_sub(n: int): int
    return 0 - n
end

def mul_two(n: int): int
    return n * 2
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_sub(0LL, n)" in _c_function_body(c_code, "zero_sub")
    assert "ailang_safe_mul(n, 2LL)" in _c_function_body(c_code, "mul_two")


def test_llvm_neutral_integer_arithmetic_elides_overflow_intrinsics() -> None:
    src = """
def add_zero(n: int): int
    return n + 0
end

def zero_add(n: int): int
    return 0 + n
end

def sub_zero(n: int): int
    return n - 0
end

def mul_one(n: int): int
    return n * 1
end

def one_mul(n: int): int
    return 1 * n
end

def mul_zero(n: int): int
    return n * 0
end
"""
    ir_text = _to_ir(src)
    assert "llvm.sadd.with.overflow" not in ir_text
    assert "llvm.ssub.with.overflow" not in ir_text
    assert "llvm.smul.with.overflow" not in ir_text
    assert "add_identity" in _ir_function_body(ir_text, "add_zero")
    assert "add_identity" in _ir_function_body(ir_text, "zero_add")
    assert "sub_identity" in _ir_function_body(ir_text, "sub_zero")
    assert "mul_identity" in _ir_function_body(ir_text, "mul_one")
    assert "mul_identity" in _ir_function_body(ir_text, "one_mul")
    assert "mul_identity" in _ir_function_body(ir_text, "mul_zero")


def test_llvm_non_neutral_integer_arithmetic_keeps_overflow_intrinsics() -> None:
    src = """
def zero_sub(n: int): int
    return 0 - n
end

def mul_two(n: int): int
    return n * 2
end
"""
    ir_text = _to_ir(src)
    zero_sub = _ir_function_body(ir_text, "zero_sub")
    mul_two = _ir_function_body(ir_text, "mul_two")
    assert "llvm.ssub.with.overflow" in zero_sub
    assert "llvm.smul.with.overflow" in mul_two


def test_c_symbolic_step_one_while_counter_elides_overflow_helper() -> None:
    src = """
def counter(n: int): int
    i = 0
    while i < n then
        i = i + 1
    end
    return i
end
"""
    body = _c_function_body(_to_c(src), "counter")
    assert "ailang_safe_add(i, 1LL)" not in body
    assert "(i + 1LL)" in body


def test_llvm_symbolic_step_one_while_counter_elides_overflow_intrinsic() -> None:
    src = """
def counter(n: int): int
    i = 0
    while i < n then
        i = i + 1
    end
    return i
end
"""
    body = _ir_function_body(_to_ir(src), "counter")
    assert "llvm.sadd.with.overflow" not in body
    assert "add_proven" in body


def test_symbolic_decrement_while_counter_elides_subtraction_checks() -> None:
    src = """
def down(n: int): int
    i = n
    while i > 0 then
        i = i - 1
    end
    return i
end
"""
    c_body = _c_function_body(_to_c(src), "down")
    ir_body = _ir_function_body(_to_ir(src), "down")
    assert "ailang_safe_sub(i, 1LL)" not in c_body
    assert "(i - 1LL)" in c_body
    assert "llvm.ssub.with.overflow" not in ir_body
    assert "sub_proven" in ir_body


def test_symbolic_counter_checks_remain_for_overflow_unsafe_shapes() -> None:
    src = """
def step_two(n: int): int
    i = 0
    while i < n then
        i = i + 2
    end
    return i
end

def inclusive(n: int): int
    i = 0
    while i <= n then
        i = i + 1
    end
    return i
end

def nested_write(n: int): int
    i = 0
    while i < n then
        if n > 0 then
            i = n
        end
        i = i + 1
    end
    return i
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(i, 2LL)" in _c_function_body(c_code, "step_two")
    assert "ailang_safe_add(i, 1LL)" in _c_function_body(c_code, "inclusive")
    assert "ailang_safe_add(i, 1LL)" in _c_function_body(c_code, "nested_write")


def test_guarded_positive_division_elides_runtime_checks() -> None:
    src = """
def guarded_div(n: int, d: int): int
    if d > 0 then
        return n / d
    end
    return 0
end
"""
    c_body = _c_function_body(_to_c(src), "guarded_div")
    ir_body = _ir_function_body(_to_ir(src), "guarded_div")
    assert "ailang_safe_div(n, d)" not in c_body
    assert "return (n / d);" in c_body
    assert "div_by_zero" not in ir_body
    assert "div_overflow" not in ir_body
    assert "sdiv_proven" in ir_body


def test_c_pure_format_builtin_keeps_counter_proof_alive() -> None:
    src = """
def counter_with_hex(n: int): int
    i = 0
    while i < n then
        h = hex(i)
        i = i + 1
    end
    return i
end
"""
    body = _c_function_body(_to_c(src), "counter_with_hex")
    assert "ailang_safe_add(i, 1LL)" not in body
    assert "(i + 1LL)" in body


def test_bounded_scalar_reduction_elides_accumulator_checks() -> None:
    src = """
def sum_bounded(limit: int): int
    i = 0
    acc = 0
    while i < limit then
        acc = acc + i
        i = i + 1
    end
    return acc
end

def caller(): int
    return sum_bounded(1000)
end
"""
    c_body = _c_function_body(_to_c(src), "sum_bounded")
    ir_body = _ir_function_body(_to_ir(src), "sum_bounded")
    assert "ailang_safe_add(acc, i)" not in c_body
    assert "(acc + i)" in c_body
    assert "ailang_safe_add(i, 1LL)" not in c_body
    assert "llvm.sadd.with.overflow" not in ir_body
    assert "add_proven" in ir_body or "add_range_proven" in ir_body


def test_symbolic_scalar_reduction_keeps_accumulator_checks() -> None:
    src = """
def sum_unbounded(limit: int): int
    i = 0
    acc = 0
    while i < limit then
        acc = acc + i
        i = i + 1
    end
    return acc
end
"""
    c_body = _c_function_body(_to_c(src), "sum_unbounded")
    ir_body = _ir_function_body(_to_ir(src), "sum_unbounded")
    assert "ailang_safe_add(acc, i)" in c_body
    assert "ailang_safe_add(i, 1LL)" not in c_body
    assert "llvm.sadd.with.overflow" in ir_body


def test_scalar_reduction_rejects_non_self_assignment() -> None:
    src = """
def bad_reduction(limit: int): int
    i = 0
    acc = 0
    while i < limit then
        acc = acc + i
        acc = limit
        i = i + 1
    end
    return acc
end

def caller(): int
    return bad_reduction(1000)
end
"""
    c_body = _c_function_body(_to_c(src), "bad_reduction")
    assert "ailang_safe_add(acc, i)" in c_body


def test_bounded_scalar_reduction_with_clamp_elides_checks() -> None:
    src = """
def loop_hash(limit: int): int
    acc = 0
    i = 0
    while i < limit then
        acc = acc + i
        if acc > 1000000000 then
            acc = acc - 1000000000
        end
        i = i + 1
    end
    return acc
end

def caller(): int
    return loop_hash(12000)
end
"""
    c_body = _c_function_body(_to_c(src), "loop_hash")
    assert "ailang_safe_add(acc, i)" not in c_body
    assert "(acc + i)" in c_body
    assert "ailang_safe_sub(acc, 1000000000LL)" not in c_body
    assert "(acc - 1000000000LL)" in c_body


def test_clamped_loop_accumulators_elide_overflow_checks() -> None:
    src = """
def fib_mix_bench(iterations: int): int
    a = 0
    b = 7
    i = 0
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
    ir_body = _ir_function_body(_to_ir(src), "fib_mix_bench")
    assert "ailang_safe_add(a, b)" not in c_body
    assert "ailang_safe_add(b, 1LL)" not in c_body
    assert "ailang_safe_sub(a, 1000000000LL)" not in c_body
    assert "ailang_safe_sub(b, 1000000000LL)" not in c_body
    assert "llvm.sadd.with.overflow" not in ir_body
    assert "llvm.ssub.with.overflow" not in ir_body
    assert "add_range_proven" in ir_body
    assert "sub_range_proven" in ir_body


def test_c_in_range_literal_arithmetic_elides_overflow_helpers() -> None:
    src = """
def literal_add(): int
    return 6 + 2
end

def literal_mul(): int
    return 7 * 3
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add" not in c_code
    assert "ailang_safe_mul" not in c_code
    assert "return (6LL + 2LL);" in _c_function_body(c_code, "literal_add")
    assert "return (7LL * 3LL);" in _c_function_body(c_code, "literal_mul")


def test_c_out_of_range_literal_arithmetic_keeps_overflow_helpers() -> None:
    src = """
def literal_add_bad(): int
    return 9223372036854775807 + 1
end
"""
    body = _c_function_body(_to_c(src), "literal_add_bad")
    assert "ailang_safe_add(" in body
    assert "1LL" in body


def test_llvm_in_range_literal_arithmetic_elides_overflow_intrinsics() -> None:
    src = """
def literal_add(): int
    return 6 + 2
end

def literal_mul(): int
    return 7 * 3
end
"""
    ir_text = _to_ir(src)
    assert "llvm.sadd.with.overflow" not in ir_text
    assert "llvm.smul.with.overflow" not in ir_text
    assert "add_identity" in _ir_function_body(ir_text, "literal_add")
    assert "mul_identity" in _ir_function_body(ir_text, "literal_mul")


def test_llvm_out_of_range_literal_arithmetic_keeps_overflow_intrinsics() -> None:
    src = """
def literal_add_bad(): int
    return 9223372036854775807 + 1
end
"""
    body = _ir_function_body(_to_ir(src), "literal_add_bad")
    assert "llvm.sadd.with.overflow" in body


def test_c_valid_literal_shift_amount_elides_shift_checks() -> None:
    src = """
def shl_lit(n: int): int
    return n << 3
end

def shr_lit(n: int): int
    return n >> 2
end
"""
    c_code = _to_c(src)
    shl_body = _c_function_body(c_code, "shl_lit")
    shr_body = _c_function_body(c_code, "shr_lit")
    assert "ailang_safe_shl(" not in shl_body
    assert "return (n << 3LL);" in shl_body
    assert "ailang_safe_shr(" not in shr_body
    assert "return (n >> 2LL);" in shr_body
    assert "ailang_safe_shl" not in c_code
    assert "ailang_safe_shr" not in c_code


def test_c_invalid_literal_shift_amount_keeps_runtime_checks() -> None:
    src = """
def shl_bad(n: int): int
    return n << 64
end

def shr_bad(n: int): int
    return n >> 64
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_shl(n, 64LL)" in _c_function_body(c_code, "shl_bad")
    assert "ailang_safe_shr(n, 64LL)" in _c_function_body(c_code, "shr_bad")


def test_llvm_valid_literal_shift_amount_elides_shift_blocks() -> None:
    src = """
def shl_lit(n: int): int
    return n << 3
end

def shr_lit(n: int): int
    return n >> 2
end
"""
    ir_text = _to_ir(src)
    shl_body = _ir_function_body(ir_text, "shl_lit")
    shr_body = _ir_function_body(ir_text, "shr_lit")
    assert "shl_proven" in shl_body
    assert "shift_error" not in shl_body
    assert "shift_ok" not in shl_body
    assert "shr_proven" in shr_body
    assert "shift_error" not in shr_body
    assert "shift_ok" not in shr_body


def test_llvm_invalid_literal_shift_amount_keeps_runtime_checks() -> None:
    src = """
def shl_bad(n: int): int
    return n << 64
end

def shr_bad(n: int): int
    return n >> 64
end
"""
    ir_text = _to_ir(src)
    assert "shift_error" in _ir_function_body(ir_text, "shl_bad")
    assert "shift_error" in _ir_function_body(ir_text, "shr_bad")


def test_c_literal_char_at_elides_strlen_and_bounds_check() -> None:
    src = """
def literal_char(): int
    return char_at("abc", 1)
end

def literal_char_bad(): int
    return char_at("abc", 3)
end
"""
    c_code = _to_c(src)
    literal_body = _c_function_body(c_code, "literal_char")
    bad_body = _c_function_body(c_code, "literal_char_bad")
    assert "return 98LL;" in literal_body
    assert "char_at(" not in literal_body
    assert 'char_at("abc", 3LL, -1LL)' in bad_body


def test_llvm_literal_char_at_elides_strlen_and_bounds_blocks() -> None:
    src = """
def literal_char(): int
    return char_at("abc", 1)
end

def literal_char_bad(): int
    return char_at("abc", 3)
end
"""
    ir_text = _to_ir(src)
    literal_body = _ir_function_body(ir_text, "literal_char")
    bad_body = _ir_function_body(ir_text, "literal_char_bad")
    assert "ret i64 98" in literal_body
    assert "char_at_len" not in literal_body
    assert "char_at_oob" not in literal_body
    assert "char_at_len" in bad_body
    assert "char_at_oob" in bad_body


def test_c_explicit_literal_char_at_length_elides_bounds_check() -> None:
    src = """
def explicit_char(s: string): int
    return char_at(s, 1, 5)
end
"""
    c_code = _to_c(src)
    body = _c_function_body(c_code, "explicit_char")
    assert "return ((int64_t)(unsigned char)(s)[1LL]);" in body
    assert "char_at(" not in body
    assert "static int64_t char_at" not in c_code


def test_c_invalid_explicit_literal_char_at_length_keeps_bounds_check() -> None:
    src = """
def explicit_char_bad(s: string): int
    return char_at(s, 5, 5)
end
"""
    c_code = _to_c(src)
    body = _c_function_body(c_code, "explicit_char_bad")
    assert "char_at(s, 5LL, 5LL)" in body
    assert "static int64_t char_at" in c_code


def test_llvm_explicit_literal_char_at_length_elides_bounds_blocks() -> None:
    src = """
def explicit_char(s: string): int
    return char_at(s, 1, 5)
end
"""
    body = _ir_function_body(_to_ir(src), "explicit_char")
    assert "char_at_len_proven" in body
    assert "char_at_oob" not in body
    assert "char_at_ok" not in body


def test_llvm_invalid_explicit_literal_char_at_length_keeps_bounds_blocks() -> None:
    src = """
def explicit_char_bad(s: string): int
    return char_at(s, 5, 5)
end
"""
    body = _ir_function_body(_to_ir(src), "explicit_char_bad")
    assert "char_at_oob" in body
    assert "char_at_ok" in body

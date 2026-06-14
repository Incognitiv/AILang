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

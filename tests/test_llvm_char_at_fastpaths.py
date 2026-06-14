from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.codegen import CodeGen  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402


def _to_ir(src: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    return CodeGen().generate(ast, "<inline>")


def test_safe_char_at_uses_i64_gep_after_bounds_check() -> None:
    src = """
def main(s: string, i: int): int
    n = strlen(s)
    return char_at(s, i, n)
end
"""
    ir_text = _to_ir(src)
    assert "char_idx32" not in ir_text
    assert 'getelementptr i8, i8* %"s", i64 %"i"' in ir_text
    assert "char_at_oob" in ir_text


def test_proven_loop_char_at_lowers_to_unchecked_load() -> None:
    src = """
def scan(s: string): int
    n = strlen(s)
    i = 0
    acc = 0
    while i < n then
        c = char_at(s, i)
        acc = acc + c
        i = i + 1
    end
    return acc
end
"""
    ir_text = _to_ir(src)
    assert "char_at_proven_ptr" in ir_text
    assert "char_at_oob" not in ir_text
    assert "char_at_len" not in ir_text


def test_unproven_char_at_keeps_bounds_check() -> None:
    src = """
def main(s: string, i: int): int
    return char_at(s, i)
end
"""
    ir_text = _to_ir(src)
    assert "char_at_oob" in ir_text
    assert "char_at_len" in ir_text


def test_unproven_char_at_reuses_local_strlen_cache() -> None:
    src = """
def main(s: string, i: int): int
    n = strlen(s)
    return char_at(s, i)
end
"""
    ir_text = _to_ir(src)
    assert "char_at_oob" in ir_text
    assert "char_at_len" not in ir_text


def test_strlen_cache_invalidates_when_length_var_changes() -> None:
    src = """
def main(s: string, i: int): int
    n = strlen(s)
    n = 0
    return char_at(s, i)
end
"""
    ir_text = _to_ir(src)
    assert "char_at_oob" in ir_text
    assert "char_at_len" in ir_text


def test_strlen_cache_invalidates_when_source_changes() -> None:
    src = """
def main(s: string, i: int): int
    n = strlen(s)
    s = "x"
    return char_at(s, i)
end
"""
    ir_text = _to_ir(src)
    assert "char_at_oob" in ir_text
    assert "char_at_len" in ir_text


def test_branch_local_strlen_cache_does_not_leak() -> None:
    src = """
def main(s: string, i: int): int
    n = 0
    if i > 0 then
        n = strlen(s)
    end
    return char_at(s, i)
end
"""
    ir_text = _to_ir(src)
    assert "char_at_oob" in ir_text
    assert "char_at_len" in ir_text


def test_nonrecursive_functions_do_not_emit_recursion_guard() -> None:
    src = """
def add(a: int, b: int): int
    return a + b
end

def main(): int
    return add(2, 3)
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_recursion_depth" not in ir_text


def test_recursive_function_keeps_recursion_guard() -> None:
    src = """
def fact(n: int): int
    if n <= 1 then
        return 1
    end
    return n * fact(n - 1)
end

def main(): int
    return fact(5)
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_recursion_depth" in ir_text


def test_llvm_helpers_are_internal_unless_exported() -> None:
    src = """
def helper(): int
    return 1
end

@export
def exported(): int
    return helper()
end

def main(): int
    return exported()
end
"""
    ir_text = _to_ir(src)
    assert 'define internal i64 @"helper"()' in ir_text
    assert 'define external i64 @"exported"()' in ir_text
    assert 'define i64 @"main"(i32 %".1", i8** %".2")' in ir_text


def test_digit_guard_proves_ascii_subtraction_no_overflow() -> None:
    src = """
def scan(s: string): int
    n = strlen(s)
    i = 0
    acc = 0
    while i < n then
        d = char_at(s, i)
        if d >= 48 then
            if d <= 57 then
                acc = acc + (d - 48)
            end
        end
        i = i + 1
    end
    return acc
end
"""
    ir_text = _to_ir(src)
    assert "sub_proven" in ir_text
    assert "llvm.ssub.with.overflow" not in ir_text


def test_protocol_parser_arithmetic_is_proven_through_modulo_accumulators() -> None:
    src = """
def scan_packet(body: string): int
    n = strlen(body)
    i = 0
    acc = 0
    while i < n then
        c = char_at(body, i)
        if c >= 48 then
            if c <= 57 then
                value = 0
                while i < n then
                    d = char_at(body, i)
                    if d < 48 then
                        break
                    end
                    if d > 57 then
                        break
                    end
                    value = value * 10 + (d - 48)
                    i = i + 1
                end
                acc = (acc * 131 + value) % 1000000007
            else
                i = i + 1
            end
        else
            i = i + 1
        end
    end
    return acc
end

def protocol_scan(iterations: int): int
    packet = "ADAPTC1 700 42 100 987 654 321 88 77 66 55 44 33 22 11 999\\n"
    acc = 0
    i = 0
    while i < iterations then
        acc = (acc + scan_packet(packet) + i) % 1000000007
        i = i + 1
    end
    return acc
end

def main(): int
    return protocol_scan(1200000)
end
"""
    ir_text = _to_ir(src)
    assert "llvm.sadd.with.overflow" not in ir_text
    assert "llvm.ssub.with.overflow" not in ir_text
    assert "llvm.smul.with.overflow" not in ir_text
    assert "mod_by_zero" not in ir_text
    assert "mod_overflow" not in ir_text
    assert "mul_proven" in ir_text
    assert "add_proven" in ir_text
    assert "srem_proven" in ir_text
    assert "393291961" in ir_text
    protocol_body = ir_text.split('define internal i64 @"protocol_scan"')[1].split(
        'define i64 @"main"'
    )[0]
    assert 'call i64 @"scan_packet"' not in protocol_body


def test_numeric_recurrence_arithmetic_is_proven_through_branch_relations() -> None:
    src = """
def numeric_mix(seed: int, iterations: int): int
    x = seed % 1000003
    y = 911382323 % 1000003
    z = 972663749 % 1000003
    acc = 0
    i = 0
    while i < iterations then
        x = (x * 110351 + 12345 + i) % 1000003
        y = (y + x * 31 + i * 17) % 1000033
        if x > y then
            z = (z + x - y + 97) % 1000037
        else
            z = (z + y - x + 193) % 1000037
        end
        if z % 7 == 0 then
            acc = (acc + z * 3 + x) % 1000000007
        else
            acc = (acc + y * 5 + z) % 1000000007
        end
        i = i + 1
    end
    return acc
end

def main(): int
    return numeric_mix(1234567, 8000000)
end
"""
    ir_text = _to_ir(src)
    assert "llvm.sadd.with.overflow" not in ir_text
    assert "llvm.ssub.with.overflow" not in ir_text
    assert "llvm.smul.with.overflow" not in ir_text
    assert "mod_by_zero" not in ir_text
    assert "mod_overflow" not in ir_text
    assert "add_proven" in ir_text
    assert "sub_proven" in ir_text
    assert "mul_proven" in ir_text
    assert "srem_proven" in ir_text


def test_numeric_program_without_string_allocations_skips_main_arena() -> None:
    src = """
def main(): int
    x = 1
    y = (x * 3 + 7) % 11
    return y
end
"""
    ir_text = _to_ir(src)
    assert "arena_total_size" not in ir_text
    assert "arena_oom" not in ir_text
    assert "request_arena_slot" not in ir_text


def test_string_allocation_program_keeps_main_arena() -> None:
    src = """
def main(): int
    s = "pkt_" + str(7)
    print(strlen(s))
    return 0
end
"""
    ir_text = _to_ir(src)
    assert "arena_total_size" in ir_text
    assert "request_arena_slot" in ir_text

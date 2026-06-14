from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser  # noqa: E402

from lexer.scan import tokenize  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402


def _to_c(src: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    return CTranspiler().transpile(ast, "<inline>")


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


def test_safe_add_elided_for_range_bounded_loop_increment() -> None:
    src = """
def main():
    i := 0..100 = 0
    while i < 100 then
        i = i + 1
    end
    print(i)
    return 0
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" not in c_code
    assert "i = (i + 1LL);" in c_code or "i = (i + 1);" in c_code


def test_safe_add_kept_when_no_range_proof_exists() -> None:
    src = """
def add(a, b):
    return a + b
end

def main(n: int):
    print(add(n, 9))
    return 0
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" in c_code


def test_safe_add_elided_with_constant_helper_call_propagation() -> None:
    src = """
def add(a, b):
    return a + b
end

def main():
    x = 7
    y = 9
    print(add(x, y))
    return 0
end
"""
    c_code = _to_c(src)
    assert "add(7LL, 9LL)" in c_code or "add(x, y)" in c_code or "16LL" in c_code
    assert "ailang_safe_add(" not in c_code


def test_safe_add_elided_for_fixed_array_reduction_loop() -> None:
    src = """
type Arr8 = [int; 8]

def bench(iterations):
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

def main():
    return bench(1000)
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" not in c_code


def test_safe_add_elided_for_slice_reduction_loop() -> None:
    src = """
def bench(iterations):
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

def main():
    return bench(1000)
end
"""
    c_code = _to_c(src)
    assert "ailang_safe_add(" not in c_code


def test_safe_mod_elided_for_numeric_modulo_recurrence() -> None:
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
    c_code = _to_c(src)
    assert "ailang_safe_add(" not in c_code
    assert "ailang_safe_sub(" not in c_code
    assert "ailang_safe_mul(" not in c_code
    assert "ailang_safe_mod(" not in _c_function_body(c_code, "numeric_mix")


def test_c_backend_proven_loop_char_at_lowers_to_direct_load() -> None:
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
    c_code = _to_c(src)
    scan_body = _c_function_body(c_code, "scan")
    assert "char_at(s, i, -1LL)" not in scan_body
    assert "((int64_t)(unsigned char)(s)[i])" in scan_body

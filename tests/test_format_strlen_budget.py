from __future__ import annotations

import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_literal_divisor_elision import _c_function_body, _to_c  # noqa: E402


def test_bounded_hex_length_sink_elides_overflow_helper() -> None:
    src = """
def format_hex_bench(iterations: int): int
    i = 0
    sink = 0
    while i < iterations then
        h = hex(i)
        sink = sink + len(h)
        i = i + 1
    end
    return sink
end

def caller(): int
    return format_hex_bench(1000)
end
"""
    body = _c_function_body(_to_c(src), "format_hex_bench")
    assert "ailang_safe_add(sink" not in body
    assert "(sink + __ailang_strlen_h)" in body


def test_bounded_decimal_length_sink_elides_overflow_helper() -> None:
    src = """
def format_str_bench(iterations: int): int
    i = 0
    sink = 0
    while i < iterations then
        s = str(i)
        sink = sink + len(s)
        i = i + 1
    end
    return sink
end

def caller(): int
    return format_str_bench(1000)
end
"""
    body = _c_function_body(_to_c(src), "format_str_bench")
    assert "ailang_safe_add(sink" not in body
    assert "(sink + __ailang_strlen_s)" in body


def test_unknown_string_length_sink_keeps_overflow_helper() -> None:
    src = """
def unknown_string_len(s: string, limit: int): int
    i = 0
    sink = 0
    while i < limit then
        sink = sink + len(s)
        i = i + 1
    end
    return sink
end
"""
    body = _c_function_body(_to_c(src), "unknown_string_len")
    assert "ailang_safe_add(sink, ailang_strlen(s))" in body

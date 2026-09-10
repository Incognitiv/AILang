from __future__ import annotations

import re
import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_literal_divisor_elision import _c_function_body, _to_c  # noqa: E402


def _sink_update_using(body: str, needle: str) -> str:
    """Return the generated sink assignment that consumes ``needle``.

    The C backend is free to insert canonical integer casts and to select a
    width-specific checked-arithmetic helper. These tests care about whether
    the sink update is checked, not the incidental textual representation.
    """
    match = re.search(rf"sink\s*=\s*([^;]*{re.escape(needle)}[^;]*);", body)
    assert match is not None, body
    return match.group(1)


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
    sink_update = _sink_update_using(body, "__ailang_strlen_h")
    assert "ailang_safe_add" not in sink_update


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
    sink_update = _sink_update_using(body, "__ailang_strlen_s")
    assert "ailang_safe_add" not in sink_update


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
    sink_update = _sink_update_using(body, "ailang_strlen(s)")
    assert "ailang_safe_add" in sink_update

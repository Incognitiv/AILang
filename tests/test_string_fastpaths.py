from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from transpiler.core import transpile_file  # noqa: E402

FASTPATH_SOURCE = """\
def main(): int
    packet = "ADAPT/1|type=1"
    if streq(substr(packet, 0, 7), "ADAPT/1") then
        return streq(packet, "ADAPT/1|type=1")
    end
    return 0
end
"""


def _transpile_c(source: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "string_fastpath.ail"
        src.write_text(source, encoding="utf-8")
        return transpile_file(str(src))


def test_c_backend_lowers_literal_streq_without_strcmp_or_substr_alloc() -> None:
    c_text = _transpile_c(FASTPATH_SOURCE)
    assert "ailang_streq_lit(" in c_text
    assert "ailang_streq_slice_lit(" in c_text
    assert "ailang_substr(" not in c_text
    assert "strcmp(" not in c_text


def test_llvm_lowers_literal_streq_without_strcmp_or_substr_alloc() -> None:
    ir_text = compile_to_ir_fast(FASTPATH_SOURCE, source_file="string_fastpath.ail")
    assert '@"strcmp"' not in ir_text
    assert 'declare i32 @"strcmp"' not in ir_text
    assert "substr_buf" not in ir_text
    assert "streq_slice" in ir_text


def test_c_backend_treats_user_string_returns_as_string_values() -> None:
    source = """\
def crlf(): string
    return "\\r\\n"
end

def main(): int
    c = crlf()
    sep = c + c
    return strlen(sep)
end
"""
    c_text = _transpile_c(source)
    assert "ailang_safe_add(c, c)" not in c_text
    assert "ailang_strcat" in c_text


def test_c_backend_explicit_dealloc_clears_mixed_ownership_flag() -> None:
    source = """\
def main(): int
    x = "borrowed"
    if 1 == 1 then
        x = "owned" + str(7)
    end
    dealloc(x)
    return 0
end
"""
    c_text = _transpile_c(source)
    assert "int __x_owned = 0;" in c_text
    assert "x = 0;  /* null after free */" in c_text
    assert "__x_owned = 0;" in c_text


def test_c_backend_index_of_from_has_zero_start_strstr_fast_path() -> None:
    source = """\
def main(): int
    s = "abc abc"
    return index_of_from(s, "abc", 0)
end
"""
    c_text = _transpile_c(source)
    assert "if (start <= 0)" in c_text
    assert "strstr(haystack, needle)" in c_text
    assert "if ((size_t)start > hlen) return -1LL;" in c_text


def test_llvm_index_of_from_has_zero_start_strstr_fast_path() -> None:
    source = """\
def main(): int
    s = "abc abc"
    return index_of_from(s, "abc", 0)
end
"""
    ir_text = compile_to_ir_fast(source, source_file="index_of_from_fastpath.ail")
    assert "idxf_start_le_zero" in ir_text
    assert "idxf_direct_strstr" in ir_text
    assert "idxf_positive" in ir_text

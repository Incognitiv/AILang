from __future__ import annotations

import tempfile
from pathlib import Path

from c_string_ownership_helpers import (
    _compile_c,
    _compile_c_with_generated_source,
    _live_bytes,
    _run_with_leak_report,
)

def test_c_backend_cleans_redeclared_owned_string_local_in_loop() -> None:
    source = """\
def main(): int
    acc = 0
    i = 0
    while i < 100 then
        string text = "item_" + str(i)
        acc = acc + strlen(text)
        i = i + 1
    end
    return acc % 251
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "loop_redecl_string")
        rc, output = _run_with_leak_report(exe)
    assert rc == 188
    assert _live_bytes(output) == 0


def test_c_backend_cleans_redeclared_dict_literal_local_in_loop() -> None:
    source = """\
def main(): int
    acc = 0
    i = 0
    while i < 100 then
        dict d = {"a": i}
        acc = acc + dict_size(d)
        i = i + 1
    end
    return acc
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "loop_redecl_dict")
        rc, output = _run_with_leak_report(exe)
    assert rc == 100
    assert _live_bytes(output) == 0


def test_c_backend_cleans_redeclared_dynamic_array_local_in_loop() -> None:
    source = """\
def main(): int
    acc = 0
    i = 0
    while i < 100 then
        array values = array_new(1)
        values = array_push(values, i)
        acc = acc + array_len(values)
        i = i + 1
    end
    return acc
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "loop_redecl_array")
        rc, output = _run_with_leak_report(exe)
    assert rc == 100
    assert _live_bytes(output) == 0


def test_c_backend_fuses_literal_plus_str_i64_without_temp_string() -> None:
    source = """\
def main(): int
    string text = "pkt_" + str(42)
    return strlen(text)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "lit_i64_concat"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0
    assert 'ailang_strcat_lit_i64("pkt_", 4u, 42LL)' in c_text
    assert 'ailang_strcat_consuming("pkt_", ailang_int_to_str' not in c_text


def test_c_backend_treats_const_string_plus_as_concat() -> None:
    source = """\
const string TOOL_DIR = "/System/Tools"
const string SEP = "/"

def main(): int
    string cmd = "echo"
    string path = TOOL_DIR + SEP + cmd
    return strlen(path)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "const_string_concat"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 18
    assert _live_bytes(output) == 0
    assert "ailang_strcat" in c_text
    assert "(TOOL_DIR + SEP)" not in c_text


def test_c_backend_virtualizes_strlen_literal_plus_str_i64() -> None:
    source = """\
def main(): int
    int i = 42
    return strlen("pkt_" + str(i))
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "virtual_strlen_lit_i64"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0
    assert "ailang_i64_decimal_len(i)" in c_text
    assert "ailang_strcat_lit_i64(" not in c_text
    assert "ailang_strlen(ailang_strcat" not in c_text


def test_c_backend_length_only_str_int_local_skips_materialization() -> None:
    source = """\
def main(): int
    i = 0
    sink = 0
    while i < 10 then
        s = str(i)
        sink = sink + len(s)
        i = i + 1
    end
    return sink
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "length_only_str_int_local"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 10
    assert _live_bytes(output) == 0
    assert "ailang_int_to_str(i)" not in c_text
    assert "ailang_strlen(s)" not in c_text
    assert "__ailang_strlen_s = ailang_i64_decimal_len(i);" in c_text


def test_c_backend_str_int_used_only_for_interpolation_length_skips_materialization() -> None:
    source = """\
def main(): int
    i = 0
    sink = 0
    while i < 10 then
        si = str(i)
        s = "v=#{si}"
        sink = sink + len(s)
        i = i + 1
    end
    return sink
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "str_int_interpolation_length_only"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 30
    assert _live_bytes(output) == 0
    assert "__pre_assign = ailang_int_to_str(i);" not in c_text
    assert 'ailang_strcat("v=", si)' not in c_text
    assert "__ailang_strlen_si = ailang_i64_decimal_len(i);" in c_text
    assert "__ailang_strlen_s = (2LL + __ailang_strlen_si);" in c_text


def test_c_backend_direct_baseconv_strlen_skips_materialization() -> None:
    source = """\
def main(): int
    return len(hex(-1)) + len(bin(-1)) + len(oct(-1))
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "direct_baseconv_strlen"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 108
    assert _live_bytes(output) == 0
    assert "ailang_hex_len_u64" in c_text
    assert "ailang_bin_len_u64" in c_text
    assert "ailang_oct_len_u64" in c_text
    assert "static char *ailang_to_hex" not in c_text
    assert "static char *ailang_to_bin" not in c_text
    assert "static char *ailang_to_oct" not in c_text
    assert "ailang_strlen(ailang_to_" not in c_text


def test_c_backend_length_only_baseconv_local_skips_materialization() -> None:
    source = """\
def main(): int
    i = 0
    sink = 0
    while i < 10 then
        h = hex(i)
        sink = sink + len(h)
        i = i + 1
    end
    return sink
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "length_only_baseconv_local"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 30
    assert _live_bytes(output) == 0
    assert "static char *ailang_to_hex" not in c_text
    assert "ailang_to_hex(i)" not in c_text
    assert "ailang_strlen(h)" not in c_text
    assert "__ailang_strlen_h = ailang_hex_len_u64((uint64_t)(i));" in c_text


def test_c_backend_cached_str_int_length_is_assignment_time_value() -> None:
    source = """\
def main(): int
    i = 9
    s = str(i)
    i = 1000
    n = len(s)
    if n != 1 then
        return 1
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "str_int_len_stable")
        rc, output = _run_with_leak_report(exe)
    assert rc == 0
    assert _live_bytes(output) == 0


def test_c_backend_branch_assignment_drops_outer_strlen_cache() -> None:
    source = """\
def main(): int
    i = 7
    s = "seed"
    if i > 0 then
        s = str(i)
    end
    n = len(s)
    if n != 1 then
        return n
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "branch_strlen_cache_drop"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 0
    assert _live_bytes(output) == 0
    # Checked assignment lowering may wrap the RHS; require the real strlen call,
    # not one exact generated-C statement shape.
    assert "ailang_strlen(s)" in c_text


def test_c_backend_caches_string_field_length_through_constructor() -> None:
    source = """\
class Packet:
    string label
    public def init(label_arg: string):
        this.label = label_arg
    end
    public def score(): int
        return strlen(this.label)
    end
end

def main(): int
    int i = 42
    Packet p = new Packet("pkt_" + str(i))
    return p.score()
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "field_strlen_cache"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0
    assert "int64_t __ailang_label_len;" in c_text
    assert "int64_t __ailang_label_arg_len" in c_text
    assert "__field_owner->__ailang_label_len = __ailang_label_arg_len;" in c_text
    assert "return self->__ailang_label_len;" in c_text
    assert (
        "Packet_init(&__ailang_stack_p, NULL, (4LL + ailang_i64_decimal_len(i)), 0);"
        in c_text
    )
    assert "ailang_strlen(self->label)" not in c_text
    assert "ailang_strlen(p->label)" not in c_text


def test_c_backend_cleans_class_returned_from_function() -> None:
    source = """\
class Packet:
    string label
    public def init(label_arg: string):
        this.label = label_arg
    end
    public def score(): int
        return strlen(this.label)
    end
end

def make(): Packet
    Packet p = new Packet("pkt_" + str(7))
    return p
end

def main(): int
    Packet p = make()
    return p.score()
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "returned_class_cleanup"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 5
    assert "__ailang_stack_p" not in c_text
    assert "if (p) { Packet_destructor(p); ailang_safe_free(p); }" in c_text
    assert _live_bytes(output) == 0


def test_c_backend_mutating_array_method_blocks_stack_array_scalarization() -> None:
    source = """\
class Packet:
    array values
    public def init(seed: int):
        this.values = array_new(4)
        this.values = array_push(this.values, seed)
    end
    public def grow(seed: int): int
        this.values = array_push(this.values, seed)
        return array_len(this.values)
    end
end

def main(): int
    Packet p = new Packet(3)
    return p.grow(4)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "mutating_method_barrier"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 2
    assert "__ailang_stack_p_values_data" not in c_text
    assert "Packet_init(&__ailang_stack_p" in c_text
    assert "ailang_dyn_array_free(&self->values);" in c_text
    assert _live_bytes(output) == 0


def test_c_backend_lowers_self_field_array_push_in_place() -> None:
    source = """\
class Bag:
    array values
    public def init():
        this.values = array_new(2)
        this.values = array_push(this.values, 41)
    end
end

def main(): int
    Bag b = new Bag()
    return array_get(b.values, 0)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "field_array_push_in_place"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 41
    assert _live_bytes(output) == 0
    assert (
        "__field_owner->values.data[__field_owner->values.length++] = 41LL;" in c_text
    )
    assert "array_push(self->values, 41LL)" not in c_text

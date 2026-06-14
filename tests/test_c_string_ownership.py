from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
LIVE_AT_EXIT_RE = re.compile(r"live at exit:\s*(\d+)\s*bytes", re.IGNORECASE)


def _compile_c(source: str, out_stem: Path) -> Path:
    src_path = out_stem.with_suffix(".ail")
    src_path.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src_path),
            "--backend=c",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out_stem.with_suffix(".exe") if os.name == "nt" else out_stem


def _compile_c_with_generated_source(source: str, out_stem: Path) -> tuple[Path, str]:
    generated_dir = REPO_ROOT / "out" / "generated" / "c_backend"
    before = set(generated_dir.glob("*.c")) if generated_dir.exists() else set()
    exe = _compile_c(source, out_stem)
    after = set(generated_dir.glob("*.c")) if generated_dir.exists() else set()
    candidates = list(after - before) or list(after)
    assert candidates, "C backend did not emit a generated .c file"
    generated = max(candidates, key=lambda path: path.stat().st_mtime)
    return exe, generated.read_text(encoding="utf-8")


def _run_with_leak_report(exe: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["AILANG_LEAK_REPORT"] = "1"
    proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}"


def _live_bytes(output: str) -> int:
    match = LIVE_AT_EXIT_RE.search(output)
    assert match is not None, output
    return int(match.group(1))


def test_c_backend_does_not_free_literal_string_return() -> None:
    source = """\
def crlf(): string
    return "\\r\\n"
end

def main(): int
    c = crlf()
    return strlen(c)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "literal_return")
        rc, output = _run_with_leak_report(exe)
    assert rc == 2
    assert _live_bytes(output) == 0


def test_c_backend_frees_owned_user_string_return() -> None:
    source = """\
def make_text(): string
    return "value_" + str(7)
end

def main(): int
    text = make_text()
    return strlen(text)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "owned_return")
        rc, output = _run_with_leak_report(exe)
    assert rc == 7
    assert _live_bytes(output) == 0


def test_c_backend_frees_typed_owned_string_local() -> None:
    source = """\
def main(): int
    string text = "value_" + str(7)
    return strlen(text)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "typed_owned_string")
        rc, output = _run_with_leak_report(exe)
    assert rc == 7
    assert _live_bytes(output) == 0


def test_c_backend_runs_destructor_for_typed_class_local() -> None:
    source = """\
class Session then
    string token
    public def ~Session():
        dealloc(this.token)
    end
end

def main(): int
    Session s = new Session("owned" + str(7))
    return strlen(s.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "typed_class_dtor")
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_owned_string_field_without_destructor() -> None:
    source = """\
class Session then
    string token
end

def main(): int
    Session s = new Session("owned" + str(7))
    return strlen(s.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "auto_string_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0


def test_c_backend_does_not_free_borrowed_string_field_without_destructor() -> None:
    source = """\
class Session then
    string token
end

def main(): int
    Session s = new Session("literal")
    return strlen(s.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "borrowed_string_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 7
    assert _live_bytes(output) == 0


def test_c_backend_empty_destructor_still_auto_cleans_owned_string_field() -> None:
    source = """\
class Session then
    string token
    ~Session()
end

def main(): int
    Session s = new Session("owned" + str(7))
    return strlen(s.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "empty_dtor_auto_string_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0


def test_c_backend_transfers_owned_init_string_param_to_field() -> None:
    source = """\
class Session then
    string token
    public def init(text: string):
        this.token = text
    end
end

def main(): int
    Session s = new Session("owned" + str(7))
    return strlen(s.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "init_param_string_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0


def test_c_backend_transfers_owned_method_string_param_to_field() -> None:
    source = """\
class Session then
    string token
    public def init():
        this.token = ""
    end
    public def set_token(text: string):
        this.token = text
    end
end

def main(): int
    Session s = new Session()
    s.set_token("owned" + str(7))
    return strlen(s.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "method_param_string_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_nested_class_field() -> None:
    source = """\
class Child then
    string token
end

class Parent then
    Child child
    public def init(c: Child):
        this.child = c
    end
end

def main(): int
    Parent p = new Parent(new Child("owned" + str(7)))
    return strlen(p.child.token)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "nested_class_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 6
    assert _live_bytes(output) == 0


def test_c_backend_transfers_owned_locals_to_fields() -> None:
    source = """\
class Child then
    string token
end

class Holder then
    string text
    Child child
    array values
    public def init():
        local_text = "owned" + str(7)
        this.text = local_text
        Child local_child = new Child("kid" + str(8))
        this.child = local_child
        local_values = array_new(2)
        local_values = array_push(local_values, 9)
        this.values = local_values
    end
end

def main(): int
    Holder h = new Holder()
    return strlen(h.text) + strlen(h.child.token) + array_len(h.values)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "owned_local_field_transfer")
        rc, output = _run_with_leak_report(exe)
    assert rc == 11
    assert _live_bytes(output) == 0


def test_c_backend_cleans_untransferred_owned_class_param_before_return() -> None:
    source = """\
class Child then
    string token
end

class Sink then
    public def discard(c: Child): int
        return 3
    end
end

def main(): int
    Sink s = new Sink()
    result = s.discard(new Child("owned" + str(7)))
    dealloc(s)
    return result
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "untransferred_class_param")
        rc, output = _run_with_leak_report(exe)
    assert rc == 3
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_dynamic_array_field() -> None:
    source = """\
class Bag then
    array values
    public def init():
        this.values = array_new(2)
        this.values = array_push(this.values, 41)
    end
end

def main(): int
    Bag b = new Bag()
    return array_len(b.values)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "array_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 1
    assert _live_bytes(output) == 0


def test_c_backend_stack_backs_fixed_constructor_array_field() -> None:
    source = """\
class Packet then
    string label
    array values
    public def init(label_arg: string, seed: int):
        this.label = label_arg
        this.values = array_new(4)
        this.values = array_push(this.values, seed)
        this.values = array_push(this.values, seed + 1)
        this.values = array_push(this.values, seed + 2)
    end
    public def score(): int
        return strlen(this.label) + array_get(this.values, 0) + array_get(this.values, 1) + array_get(this.values, 2)
    end
end

def main(): int
    Packet p = new Packet("pkt_" + str(7), 5)
    return p.score()
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source,
            Path(td) / "stack_array_field",
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 23
    assert "__ailang_stack_p_values_data[4]" in c_text
    assert "Packet_init(&__ailang_stack_p" not in c_text
    assert "Packet_score(p)" not in c_text
    assert "ailang_strlen(p->label)" not in c_text
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_str_array_field() -> None:
    source = """\
class Words then
    str_array parts
    public def init():
        this.parts = str_array_new(2)
        this.parts = str_array_push(this.parts, "a")
    end
end

def main(): int
    Words w = new Words()
    return str_array_len(w.parts)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "str_array_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 1
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_dict_field() -> None:
    source = """\
class Store then
    dict values
    public def init():
        this.values = dict_new()
    end
end

def main(): int
    Store s = new Store()
    return dict_size(s.values)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "dict_field")
        rc, output = _run_with_leak_report(exe)
    assert rc == 0
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_typed_dict_literal_local() -> None:
    source = """\
def main(): int
    dict d = {"a": 1}
    return dict_size(d)
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "typed_dict_literal")
        rc, output = _run_with_leak_report(exe)
    assert rc == 1
    assert _live_bytes(output) == 0


def test_c_backend_cleans_redeclared_class_local_in_loop() -> None:
    source = """\
class Packet then
    string label
    public def init(text: string):
        this.label = text
    end
    public def score(): int
        return strlen(this.label)
    end
end

def main(): int
    acc = 0
    i = 0
    while i < 100 then
        Packet p = new Packet("pkt_" + str(i))
        acc = acc + p.score()
        i = i + 1
    end
    return acc % 251
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe = _compile_c(source, Path(td) / "loop_redecl_class")
        rc, output = _run_with_leak_report(exe)
    assert rc == 88
    assert _live_bytes(output) == 0


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
    assert "n = ailang_strlen(s);" in c_text


def test_c_backend_caches_string_field_length_through_constructor() -> None:
    source = """\
class Packet then
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
    assert "Packet_init(&__ailang_stack_p, NULL, (4LL + ailang_i64_decimal_len(i)), 0);" in c_text
    assert "ailang_strlen(self->label)" not in c_text
    assert "ailang_strlen(p->label)" not in c_text


def test_c_backend_cleans_class_returned_from_function() -> None:
    source = """\
class Packet then
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
class Packet then
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
class Bag then
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
    assert "__field_owner->values.data[__field_owner->values.length++] = 41LL;" in c_text
    assert "array_push(self->values, 41LL)" not in c_text

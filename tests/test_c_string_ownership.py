from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from c_string_ownership_helpers import (
    AILANG,
    REPO_ROOT,
    _compile_c,
    _compile_c_with_generated_source,
    _live_bytes,
    _run_with_leak_report,
)

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


def test_c_backend_materializes_string_borrowed_from_returned_split_element() -> None:
    source = """\
def second(text: string): string
    parts = split(text, " ")
    return split_str_get(parts, 1)
end

def main(): int
    picked = second("alpha veltrax")
    if streq(picked, "veltrax") == 0 then
        return 7
    end
    dealloc(picked)
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        exe, c_text = _compile_c_with_generated_source(
            source, Path(td) / "returned_split_element"
        )
        rc, output = _run_with_leak_report(exe)
    assert rc == 0, output
    assert 'ailang_strcat("", (parts).data[1LL])' in c_text
    assert c_text.index('ailang_strcat("", (parts).data[1LL])') < c_text.rindex(
        "ailang_str_array_free(&parts);"
    )
    assert _live_bytes(output) == 0


def test_jit_string_borrowed_from_local_split_element_survives_return() -> None:
    source = """\
def second(text: string): string
    parts = split(text, " ")
    return split_str_get(parts, 1)
end

def main(): int
    picked = second("alpha veltrax")
    if streq(picked, "veltrax") == 1 then
        return 0
    end
    return 7
end
"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "returned_split_element_jit.ail"
        src.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr


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
class Session:
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
class Session:
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
class Session:
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
class Session:
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
class Session:
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
class Session:
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
class Child:
    string token
end

class Parent:
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
class Child:
    string token
end

class Holder:
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
class Child:
    string token
end

class Sink:
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
class Bag:
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
class Packet:
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
    # Correctness phase: do not require trivial method inlining.
    # The runtime result and ownership/layout assertions below are the contract.
    assert "ailang_strlen(p->label)" not in c_text
    assert _live_bytes(output) == 0


def test_c_backend_auto_cleans_str_array_field() -> None:
    source = """\
class Words:
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
class Store:
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
class Packet:
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



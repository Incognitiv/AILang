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


def test_llvm_fuses_literal_plus_str_i64_without_sprintf() -> None:
    src = """
def main(): int
    string text = "pkt_" + str(42)
    return strlen(text)
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_i64_to_cstr" in ir_text
    assert 'call void @"__ailang_i64_to_cstr"' in ir_text
    assert "sprintf_lit_i64" not in ir_text
    assert "strcat" not in ir_text


def test_llvm_virtualizes_strlen_literal_plus_str_i64() -> None:
    src = """
def main(i: int): int
    return strlen("pkt_" + str(i))
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_i64_decimal_len" in ir_text
    assert 'call i64 @"__ailang_i64_decimal_len"' in ir_text
    assert "__ailang_i64_to_cstr" not in ir_text
    assert "str_lit_i64_buf" not in ir_text
    assert "strlen_val" not in ir_text


def test_llvm_length_only_str_int_local_skips_materialization() -> None:
    src = """
def main(limit: int): int
    i = 0
    sink = 0
    while i < limit then
        s = str(i)
        sink = sink + len(s)
        i = i + 1
    end
    return sink
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_i64_decimal_len" in ir_text
    assert "s_i64_strlen" in ir_text
    assert "sprintf" not in ir_text
    assert 'call i64 @"strlen"' not in ir_text


def test_llvm_length_only_str_int_cache_is_assignment_time_value() -> None:
    src = """
def main(): int
    i = 9
    s = str(i)
    i = 1000
    return len(s)
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_i64_decimal_len" in ir_text
    assert "s_i64_strlen" in ir_text
    assert "sprintf" not in ir_text
    assert 'call i64 @"strlen"' not in ir_text


def test_llvm_branch_assignment_does_not_leak_strlen_cache() -> None:
    src = """
def main(flag: int): int
    s = "seed"
    if flag > 0 then
        s = str(flag)
    end
    return len(s)
end
"""
    ir_text = _to_ir(src)
    assert 'call i64 @"strlen"' in ir_text


def test_llvm_stack_lowers_non_escaping_class_local() -> None:
    src = """
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
    Packet p = new Packet("pkt_" + str(7))
    return p.score()
end
"""
    ir_text = _to_ir(src)
    assert "p_stack" in ir_text
    assert "Packet_mem" not in ir_text


def test_llvm_nonrecursive_class_method_has_no_recursion_guard() -> None:
    src = """
class Packet then
    public def score(): int
        return 7
    end
end

def main(): int
    Packet p = new Packet()
    return p.score()
end
"""
    ir_text = _to_ir(src)
    method_start = ir_text.index('define i64 @"Packet_score"')
    method_end = ir_text.index('define i64 @"main"')
    method_ir = ir_text[method_start:method_end]
    assert "__ailang_recursion_depth" not in method_ir
    assert "rec_depth_dec" not in method_ir


def test_llvm_caches_string_field_length_through_constructor() -> None:
    src = """
class Packet then
    string label
    public def init(label_arg: string):
        this.label = label_arg
    end
    public def score(): int
        return strlen(this.label)
    end
end

def main(i: int): int
    Packet p = new Packet("pkt_" + str(i))
    return p.score()
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_label_len" in ir_text
    assert "__ailang_label_arg_len" in ir_text
    assert "__ailang_i64_decimal_len" in ir_text
    assert "__ailang_i64_to_cstr" not in ir_text
    assert "str_lit_i64_buf" not in ir_text
    assert 'call i64 @"Packet_init"' in ir_text
    assert "i8* null" in ir_text
    score_start = ir_text.index('define i64 @"Packet_score"')
    score_end = ir_text.find("\n}\n", score_start + 1)
    score_ir = ir_text[score_start : score_end if score_end >= 0 else len(ir_text)]
    assert "strlen_val" not in score_ir


def test_llvm_virtual_string_constructor_length_uses_inferred_int_local() -> None:
    src = """
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
    i = 0
    Packet p = new Packet("pkt_" + str(i))
    return p.score()
end
"""
    ir_text = _to_ir(src)
    assert "__ailang_i64_decimal_len" in ir_text
    assert 'call i64 @"Packet_init"' in ir_text
    assert "i8* null" in ir_text
    assert "call i64 @\"strlen\"(i8* null)" not in ir_text
    assert "virtual_concat_strlen" in ir_text


def test_llvm_cached_field_strlen_works_for_local_class_variables() -> None:
    src = """
class Session then
    string token
    public def init(text: string):
        this.token = text
    end
end

def main(): int
    Session first = new Session("owned" + str(7))
    return strlen(first.token)
end
"""
    ir_text = _to_ir(src)
    assert "i8* null" in ir_text
    assert "__ailang_i64_decimal_len" in ir_text
    assert 'call i64 @"strlen"' not in ir_text
    assert "token_len_ptr" in ir_text


def test_llvm_keeps_escaping_class_local_on_heap() -> None:
    src = """
class Packet then
    string label
    public def init(label_arg: string):
        this.label = label_arg
    end
end

def make(): Packet
    Packet p = new Packet("pkt_" + str(7))
    return p
end

def main(): int
    Packet p = make()
    return strlen(p.label)
end
"""
    ir_text = _to_ir(src)
    assert "Packet_mem" in ir_text
    assert "p_stack" not in ir_text


def test_llvm_stack_cleanup_does_not_free_arena_backed_string_fields() -> None:
    src = """
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
    int i = 0
    int total = 0
    while i < 4 then
        Packet p = new Packet("pkt_" + str(i))
        i = i + 1
        if i == 1 then
            continue
        end
        if i == 3 then
            break
        end
        total = total + p.score()
    end
    return total
end
"""
    ir_text = _to_ir(src)
    assert "label_cleanup_text" not in ir_text
    assert 'call void @"free"(i8* %"label_cleanup_text' not in ir_text


def test_llvm_loop_exit_edges_cleanup_stack_class_array_fields() -> None:
    src = """
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
    int i = 0
    while i < 4 then
        Packet p = new Packet(i)
        i = i + 1
        if i == 1 then
            continue
        end
        if i == 3 then
            break
        end
    end
    return i
end
"""
    ir_text = _to_ir(src)
    assert "values_free_array" in ir_text
    assert ir_text.count("values_raw_base") >= 3


def test_llvm_stack_cleanup_skips_virtual_string_field_in_helper_function() -> None:
    src = """
class Packet then
    string label
    array values
    public def init(label_arg: string, seed: int):
        this.label = label_arg
        this.values = array_new(4)
        this.values = array_push(this.values, seed)
    end
    public def score(): int
        return strlen(this.label) + array_get(this.values, 0)
    end
end

def churn(iterations: int): int
    i = 0
    total = 0
    while i < iterations then
        Packet p = new Packet("pkt_" + str(i), i)
        total = total + p.score()
        i = i + 1
    end
    return total
end

def main(): int
    return churn(4)
end
"""
    ir_text = _to_ir(src)
    assert "p_values_stack_array" in ir_text
    assert 'call i64 @"Packet_init"' not in ir_text
    assert 'call i64 @"Packet_score"' not in ir_text
    assert "values_free_array" not in ir_text
    assert "label_cleanup_text" not in ir_text
    assert 'call void @"free"(i8* %"label_cleanup_text' not in ir_text


def test_llvm_scalarizes_direct_stack_array_field_get() -> None:
    src = """
class Packet then
    array values
    public def init(seed: int):
        this.values = array_new(4)
        this.values = array_push(this.values, seed)
        this.values = array_push(this.values, seed + 1)
    end
end

def main(): int
    Packet p = new Packet(5)
    return array_get(p.values, 1)
end
"""
    ir_text = _to_ir(src)
    main_start = ir_text.index('define i64 @"main"')
    main_end = ir_text.find("\n}\n", main_start + 1)
    main_ir = ir_text[main_start : main_end if main_end >= 0 else len(ir_text)]
    assert "p_values_stack_array" in ir_text
    assert "arr_get_ptr" not in main_ir

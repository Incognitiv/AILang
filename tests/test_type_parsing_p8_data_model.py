from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser import ast as A
from parser.parser import Parser

from lexer.scan import tokenize


def _parse_type(type_expr: str):
    tokens = tokenize(type_expr)
    parser = Parser(tokens)
    parsed = parser.parse_type()
    assert parser.pos == len(tokens), f"unconsumed tokens for {type_expr!r}"
    return parsed


def test_parse_fixed_array_type() -> None:
    assert _parse_type("[byte;16]") == ("fixed_array", "u8", 16)
    assert _parse_type("[int;4]") == ("fixed_array", "i64", 4)


def test_parse_slice_and_view_types() -> None:
    assert _parse_type("slice[byte]") == ("slice", "u8")
    assert _parse_type("view[int]") == ("slice", "i64")


def test_parse_callback_type_alias_surface() -> None:
    assert _parse_type("fn(hwnd: ptr, msg: int): int @stdcall") == (
        "fn",
        [("hwnd", "ptr"), ("msg", "i64")],
        "i64",
        ("stdcall",),
    )


def test_ptr_return_type_and_pascalcase_call_statement() -> None:
    src = """
def run_destroy(hwnd: ptr): ptr
    DestroyWindow(hwnd)
    return hwnd
end
"""
    parser = Parser(tokenize(src))
    nodes = parser.parse_program()
    fn = next(node for node in nodes if isinstance(node, A.Function))
    assert fn.return_type == "ptr"
    assert isinstance(fn.body[0], A.Call)
    assert fn.body[0].name == "DestroyWindow"


def test_function_declaration_surface_accepts_def_and_type_prefix_forms() -> None:
    src = """
def plain_main():
    return 0
end

int add(int a, int b):
    return a + b
end

void no_args():
    print("ok")
end

float half(float x):
    return x / 2.0
end

double twice(double x):
    return x * 2.0
end

quad widen(quad x):
    return x
end
"""
    parser = Parser(tokenize(src))
    nodes = parser.parse_program()
    functions = {node.name: node for node in nodes if isinstance(node, A.Function)}

    assert functions["plain_main"].return_type == "i64"
    assert functions["add"].return_type == "i64"
    assert functions["add"].params == [("a", "i64", None), ("b", "i64", None)]
    assert functions["no_args"].return_type == "void"
    assert functions["half"].return_type == "f32"
    assert functions["half"].params == [("x", "f32", None)]
    assert functions["twice"].return_type == "f64"
    assert functions["widen"].return_type == "f128"
    assert functions["widen"].params == [("x", "f128", None)]


def test_type_alias_accepts_fixed_array_and_slice_types() -> None:
    src = """
type Arr8 = [int; 8]
type ViewI = slice[int]
typedef Count = int
typedef byte ByteAlias
typedef [byte; 4] Hash4
"""
    parser = Parser(tokenize(src))
    nodes = parser.parse_program()
    aliases = [n for n in nodes if isinstance(n, A.TypeAlias)]
    assert len(aliases) == 5
    assert aliases[0].name == "Arr8"
    assert aliases[0].target_type == ("fixed_array", "i64", 8)
    assert aliases[1].name == "ViewI"
    assert aliases[1].target_type == ("slice", "i64")
    assert aliases[2].name == "Count"
    assert aliases[2].target_type == "i64"
    assert aliases[3].name == "ByteAlias"
    assert aliases[3].target_type == "u8"
    assert aliases[4].name == "Hash4"
    assert aliases[4].target_type == ("fixed_array", "u8", 4)


def test_parse_extern_record_with_c_name_and_layout_metadata() -> None:
    src = """
extern record NativeRect = "struct RECT" layout size 16 align 4:
    left offset 0 size 4
    top offset 4 size 4
end
opaque record NativeHandle = "HANDLE"
"""
    parser = Parser(tokenize(src))
    nodes = parser.parse_program()
    records = [n for n in nodes if isinstance(n, A.ExternRecordDef)]
    assert len(records) == 2
    rect, handle = records
    assert rect.name == "NativeRect"
    assert rect.c_name == "struct RECT"
    assert rect.is_opaque is False
    assert rect.layout_size == 16
    assert rect.layout_align == 4
    assert rect.field_offsets == {"left": 0, "top": 4}
    assert rect.field_sizes == {"left": 4, "top": 4}
    assert handle.name == "NativeHandle"
    assert handle.c_name == "HANDLE"
    assert handle.is_opaque is True


def test_unannotated_def_infers_return_type_from_values_and_void_body() -> None:
    src = """
def no_value():
    print("ok")
end

def flag():
    return false
end

def text():
    return "hello"
end

def narrow():
    u8 x = 7
    return x
end

def joined(bool pick):
    if pick then
        i8 a = -1
        return a
    else
        u8 b = 200
        return b
    end
end
"""
    functions = {
        node.name: node
        for node in Parser(tokenize(src)).parse_program()
        if isinstance(node, A.Function)
    }
    assert functions["no_value"].return_type == "void"
    assert functions["flag"].return_type == "bool"
    assert functions["text"].return_type == "string"
    assert functions["narrow"].return_type == "u8"
    # i8 + u8 needs i16 to preserve the full value domain of both branches.
    assert functions["joined"].return_type == "i16"


def test_unannotated_def_inference_resolves_forward_calls_and_methods() -> None:
    src = """
def outer():
    return inner()
end

def inner():
    return "ok"
end

class Box:
    u8 value = 7

    def get():
        return this.value
    end
end
"""
    nodes = Parser(tokenize(src)).parse_program()
    functions = {n.name: n for n in nodes if isinstance(n, A.Function)}
    box = next(n for n in nodes if isinstance(n, A.ClassDef))
    methods = {m.name: m for m in box.methods}
    assert functions["inner"].return_type == "string"
    assert functions["outer"].return_type == "string"
    assert methods["get"].return_type == "u8"


def test_class_record_and_extern_record_reject_then_surface() -> None:
    bad_sources = [
        "class Box then\n    int x\nend\n",
        "record Pair then\n    int x\nend\n",
        'extern record Native = "struct Native" layout size 8 align 8 then\n    x offset 0 size 8\nend\n',
    ]
    for src in bad_sources:
        try:
            Parser(tokenize(src)).parse_program()
        except SyntaxError as exc:
            assert "Expected ':'" in str(exc)
        else:
            raise AssertionError(f"legacy then declaration unexpectedly accepted: {src!r}")


def test_inferred_return_type_rejects_lossy_or_incompatible_join() -> None:
    src = """
def bad(bool pick):
    if pick then
        return "text"
    else
        return 7
    end
end
"""
    try:
        Parser(tokenize(src)).parse_program()
    except SyntaxError as exc:
        assert "Cannot infer one lossless return type" in str(exc)
    else:
        raise AssertionError("incompatible inferred return types unexpectedly accepted")


def test_return_contracts_distinguish_void_early_exit_from_value_returns() -> None:
    accepted = [
        "void f():\n    return\nend\n",
        "def f():\n    return\nend\n",
        "int f(bool x):\n    if x then\n        return 1\n    else\n        return 2\n    end\nend\n",
    ]
    for src in accepted:
        Parser(tokenize(src)).parse_program()

    rejected = [
        "void f():\n    return 1\nend\n",
        "int f(bool x):\n    if x then\n        return\n    end\n    return 1\nend\n",
        "int f(bool x):\n    if x then\n        return 1\n    end\nend\n",
        "def f(bool x):\n    if x then\n        return\n    end\n    return 123\nend\n",
    ]
    for src in rejected:
        try:
            Parser(tokenize(src)).parse_program()
        except SyntaxError:
            pass
        else:
            raise AssertionError(f"invalid return contract unexpectedly accepted: {src!r}")

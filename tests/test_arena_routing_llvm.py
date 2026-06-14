from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser  # noqa: E402

from codegen.codegen import CodeGen  # noqa: E402
from lexer.scan import tokenize  # noqa: E402


def _to_ir(src: str) -> str:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    return CodeGen().generate(ast, "<inline>")


def test_arena_use_routes_string_alloc_via_request_arena_slot() -> None:
    src = """
def main(): int
    a = arena_create(4096)
    arena_use(a)
    s = "value=" + str(123456)
    print(s)
    return arena_used(a)
end
"""
    ir_text = _to_ir(src)
    slot_refs = [ln for ln in ir_text.splitlines() if "request_arena_slot" in ln]
    assert slot_refs
    assert any("store i8* null" in ln for ln in slot_refs)
    assert any("store i8*" in ln and "null" not in ln for ln in slot_refs)
    assert "str_alloc_req_arena" in ir_text
    assert "str_alloc_fallback" in ir_text


def test_arena_destroy_clears_active_request_arena_slot() -> None:
    src = """
def main(): int
    a = arena_create(4096)
    arena_use(a)
    arena_destroy(a)
    s = str(7)
    return 0
end
"""
    ir_text = _to_ir(src)
    assert "destroy_active_req_arena" in ir_text
    null_stores = [
        ln
        for ln in ir_text.splitlines()
        if "store i8* null" in ln and "request_arena_slot" in ln
    ]
    # One init store + one clear-on-destroy store.
    assert len(null_stores) >= 2

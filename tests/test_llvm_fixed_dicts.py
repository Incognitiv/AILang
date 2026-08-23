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
    return CodeGen().generate(Parser(tokenize(src)).parse_program(), "<inline>")


def test_literal_dict_with_literal_keys_is_scalarized_in_llvm() -> None:
    src = """
def main(): int
    d = {"a": 1, "b": 2}
    d["b"] = d["a"] + 40
    return d["a"] + d["b"]
end
"""
    ir_text = _to_ir(src)
    assert "fdict_d_" in ir_text
    assert "_ailang_dict_create" not in ir_text
    assert "_ailang_dict_get" not in ir_text
    assert "_ailang_dict_set" not in ir_text


def test_dynamic_dict_key_keeps_runtime_dict_in_llvm() -> None:
    src = """
def main(k: string): int
    d = {"a": 1, "b": 2}
    return d[k]
end
"""
    ir_text = _to_ir(src)
    assert "_ailang_dict_create" in ir_text
    assert "_ailang_dict_get" in ir_text


def test_literal_dict_modulo_uses_range_single_subtract() -> None:
    src = """
def main(): int
    d = {"a": 1, "b": 2}
    modulus = 1000000007
    d["a"] = (d["a"] + d["b"]) % modulus
    return d["a"]
end
"""
    ir_text = _to_ir(src)
    assert "fdict_d_" in ir_text
    assert "mod_range_select" in ir_text
    assert "srem" not in ir_text
    assert "urem" not in ir_text

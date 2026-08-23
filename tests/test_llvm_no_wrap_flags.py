from __future__ import annotations

import re
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


def test_proven_signed_add_emits_nsw_and_skips_overflow_intrinsic() -> None:
    src = """
def main():
    i := 0..100 = 0
    i = i + 1
    return i
end
"""
    ir_text = _to_ir(src)
    assert re.search(r"\badd nsw i(?:32|64)\b", ir_text)
    assert "llvm.sadd.with.overflow" not in ir_text


def test_unproven_add_keeps_overflow_intrinsic() -> None:
    src = """
def add(a, b):
    return a + b
end

def main(n):
    return add(n, 9)
end
"""
    ir_text = _to_ir(src)
    assert "llvm.sadd.with.overflow.i64" in ir_text


def test_proven_unsigned_add_emits_nuw() -> None:
    src = """
def main():
    uint i = 0
    i = i + 1
    return i
end
"""
    ir_text = _to_ir(src)
    assert re.search(r"\badd nuw i(?:32|64)\b", ir_text)

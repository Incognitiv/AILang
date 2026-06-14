from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402


PROTOCOL_SOURCE = """
def scan_packet(body: string): int
    n = strlen(body)
    i = 0
    acc = 0
    while i < n then
        c = char_at(body, i)
        if c >= 48 then
            if c <= 57 then
                value = 0
                while i < n then
                    d = char_at(body, i)
                    if d < 48 then
                        break
                    end
                    if d > 57 then
                        break
                    end
                    value = value * 10 + (d - 48)
                    i = i + 1
                end
                acc = (acc * 131 + value) % 1000000007
            else
                i = i + 1
            end
        else
            i = i + 1
        end
    end
    return acc
end

def protocol_scan(iterations: int): int
    packet = "ADAPTC1 700 42 100 987 654 321 88 77 66 55 44 33 22 11 999\\n"
    acc = 0
    i = 0
    while i < iterations then
        acc = (acc + scan_packet(packet) + i) % 1000000007
        i = i + 1
    end
    return acc
end

def main(): int
    return protocol_scan(1200000)
end
"""


def _to_c(src: str) -> str:
    tokens = tokenize(src)
    ast = Parser(tokens).parse_program()
    return CTranspiler().transpile(ast, "<inline>")


def test_c_backend_erases_pure_literal_protocol_call() -> None:
    c_text = _to_c(PROTOCOL_SOURCE)
    assert "393291961" in c_text
    protocol_body = c_text.split("int64_t protocol_scan(")[1].split("int64_t main(")[0]
    assert "scan_packet(packet)" not in protocol_body

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402
from codegen.codegen import CodeGen  # noqa: E402


OPTIMIZER_SOURCE = """\
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
    int i = 7
    Packet p = new Packet("pkt_" + str(i), 5)
    return p.score()
end
"""


def _optimizer_report(source: str) -> dict[str, object]:
    tokens = tokenize(source)
    parser = Parser(tokens)
    ast = parser.parse_program()
    transpiler = CTranspiler()
    transpiler.transpile(ast, "<inline>")
    return transpiler.get_optimizer_report()


def _llvm_optimizer_report(source: str) -> dict[str, object]:
    tokens = tokenize(source)
    parser = Parser(tokens)
    ast = parser.parse_program()
    codegen = CodeGen()
    codegen.generate(ast, "<inline>")
    return codegen.get_optimizer_report()


def test_optimizer_report_records_stack_and_virtual_decisions() -> None:
    report = _optimizer_report(OPTIMIZER_SOURCE)
    summary = report["summary"]
    assert summary["stack_class:stack_lowered"] == 1
    assert summary["stack_array_field:scalarized"] == 1
    assert summary["virtual_string:elided_materialization"] == 1
    assert summary["method_inline:inlined"] == 1

    decisions = report["decisions"]
    assert any(
        row["opt_kind"] == "stack_array_field"
        and row["target"] == "p.values"
        and row["decision"] == "scalarized"
        for row in decisions
    )


def test_llvm_optimizer_report_records_matching_decision_kinds() -> None:
    report = _llvm_optimizer_report(OPTIMIZER_SOURCE)
    summary = report["summary"]
    assert summary["stack_class:stack_lowered"] == 1
    assert summary["stack_array_field:scalarized"] == 1
    assert summary["virtual_string:elided_materialization"] == 1
    assert summary["method_inline:inlined"] == 1


def test_optimizer_report_json_cli_is_machine_parseable() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "optimizer_report.ail"
        src.write_text(OPTIMIZER_SOURCE, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "ailang.py"),
                str(src),
                "--optimizer-report-json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["decision_count"] >= 4
    assert payload["summary"]["stack_class:stack_lowered"] == 1
    assert payload["backends"]["llvm"]["summary"]["stack_class:stack_lowered"] == 1

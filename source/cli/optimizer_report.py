"""Optimizer/materialization CLI report."""

from __future__ import annotations

import json
from pathlib import Path


def report_optimizer(source_file: str, *, as_json: bool = False) -> bool:
    """Generate source optimizer/materialization report without native build."""
    try:
        from parser.parser import Parser

        from codegen.codegen import CodeGen
        from lexer.scan import tokenize
        from transpiler.core import CTranspiler
    except ImportError:
        print("Error: transpiler/parser modules unavailable for optimizer reporting")
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()
        transpiler = CTranspiler()
        _ = transpiler.transpile(ast, source_file)
        c_report = transpiler.get_optimizer_report()

        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()
        codegen = CodeGen()
        _ = codegen.generate(ast, source_file)
        llvm_report = codegen.get_optimizer_report()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: failed to build optimizer report: {exc}")
        return False

    c_summary = dict(c_report.get("summary", {}))
    c_decisions = list(c_report.get("decisions", []))
    llvm_summary = dict(llvm_report.get("summary", {}))
    llvm_decisions = list(llvm_report.get("decisions", []))
    if as_json:
        payload = {
            "source": str(Path(source_file).resolve()),
            "summary": c_summary,
            "decision_count": len(c_decisions),
            "decisions": c_decisions,
            "backends": {
                "c": {
                    "summary": c_summary,
                    "decision_count": len(c_decisions),
                    "decisions": c_decisions,
                },
                "llvm": {
                    "summary": llvm_summary,
                    "decision_count": len(llvm_decisions),
                    "decisions": llvm_decisions,
                },
            },
        }
        print(json.dumps(payload, indent=2))
        return True

    print(f"Optimizer report for: {source_file}")
    print("")
    _print_backend_report("c", c_summary, c_decisions)
    print("")
    _print_backend_report("llvm", llvm_summary, llvm_decisions)
    return True


def _print_backend_report(
    backend: str, summary: dict[str, object], decisions: list[object]
) -> None:
    print(f"[{backend}]")
    if not summary:
        print("summary: no optimizer decisions recorded")
    else:
        print("summary:")
        for key in sorted(summary):
            print(f"  {key}={summary[key]}")
    print("")
    print("decisions:")
    if not decisions:
        print("  (none)")
        return
    for raw in decisions:
        row = raw if isinstance(raw, dict) else {}
        line = int(row.get("line", 0) or 0)
        col = int(row.get("col", 0) or 0)
        func = str(row.get("function", "<global>"))
        kind = str(row.get("opt_kind", "unknown"))
        target = str(row.get("target", "?"))
        decision = str(row.get("decision", "unknown"))
        reason = str(row.get("reason", "unknown"))
        details = row.get("details")
        detail_text = ""
        if details:
            detail_text = f" details={json.dumps(details, sort_keys=True)}"
        print(
            f"  line={line} col={col} func={func} "
            f"kind={kind} target={target} decision={decision} "
            f"reason={reason}{detail_text}"
        )

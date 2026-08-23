"""LLVM optimizer/materialization report helpers."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Dict


class _CodeGenOptimizerReportMixin:
    def _record_optimizer_decision(
        self: Any,
        node: A.ASTNode,
        *,
        opt_kind: str,
        target: str,
        decision: str,
        reason: str,
        details: Dict[str, Any] | None = None,
    ) -> None:
        line = int(getattr(node, "line", 0) or 0)
        col = int(getattr(node, "col", 0) or 0)
        func = getattr(self, "_current_function_name", None) or "<global>"
        row: Dict[str, Any] = {
            "opt_kind": opt_kind,
            "target": target,
            "decision": decision,
            "reason": reason,
            "line": line,
            "col": col,
            "function": func,
        }
        if details:
            row["details"] = dict(details)
        self._optimizer_decisions.append(row)
        key = f"{opt_kind}:{decision}"
        self._optimizer_summary[key] = int(self._optimizer_summary.get(key, 0)) + 1

    def get_optimizer_report(self: Any) -> Dict[str, Any]:
        return {
            "summary": dict(self._optimizer_summary),
            "decisions": list(self._optimizer_decisions),
        }

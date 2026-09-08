"""CTranspiler optimizer/materialization reports."""

from __future__ import annotations

from parser import ast as A
from typing import Any


class _CTranspilerOptimizerReportMixin:
    def _record_optimizer_decision(
        self: Any,
        node: A.ASTNode,
        *,
        opt_kind: str,
        target: str,
        decision: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record a source-level optimizer/materialization decision."""
        line = int(getattr(node, "line", 0) or 0)
        col = int(getattr(node, "col", 0) or 0)
        func = self.current_function or "<global>"
        row: dict[str, Any] = {
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

    def get_optimizer_report(self: Any) -> dict[str, Any]:
        """Return collected optimizer/materialization decisions."""
        return {
            "summary": dict(self._optimizer_summary),
            "decisions": list(self._optimizer_decisions),
        }

    def _class_locals_constructed_by_new(
        self: Any, body: list[A.ASTNode], class_locals: list[tuple[str, str]]
    ) -> set[str]:
        return self.ownership.class_locals_constructed_by_new(body, class_locals)

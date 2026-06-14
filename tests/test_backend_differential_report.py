from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from backend_differential import (  # noqa: E402
    BackendRun,
    DifferentialResult,
    render_markdown_report,
)


def test_backend_differential_markdown_report_summarizes_results() -> None:
    results = [
        DifferentialResult(
            name="ok_case",
            expected_lines=["7"],
            c=BackendRun("c", 0, 0, ["7"], "", 0),
            llvm=BackendRun("llvm", 0, 0, ["7"], ""),
            ok=True,
            reason="ok",
        ),
        DifferentialResult(
            name="bad_case",
            expected_lines=[],
            c=BackendRun("c", 0, 0, ["1"], "", 0),
            llvm=BackendRun("llvm", 0, 0, ["2"], ""),
            ok=False,
            reason="backend_output_mismatch",
        ),
    ]

    report = render_markdown_report(results, seed=123, generated=1)

    assert "# Backend Differential Report" in report
    assert "| `ok` | 1 |" in report
    assert "| `backend_output_mismatch` | 1 |" in report
    assert "| `bad_case` | fail | `backend_output_mismatch` | 0 |" in report

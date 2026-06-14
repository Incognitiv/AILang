from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _run_format_report(src: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "case.ail"
        src_path.write_text(src, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src_path), "--format-report-json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)


def test_format_report_marks_direct_writer_for_known_shape_print() -> None:
    src = """
def main(): int
    print("value", 42)
    return 0
end
"""
    payload = _run_format_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("print:direct_writer", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("format_kind") == "print"
        and row.get("decision") == "direct_writer"
        and row.get("reason") in {"known_shape", "empty_newline", "expr_single_arg"}
        for row in decisions
    )


def test_format_report_marks_printf_fallback_for_float_print() -> None:
    src = """
def main(): int
    print(3.14159)
    return 0
end
"""
    payload = _run_format_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("print:format_fallback", 0)) >= 1
    assert int(summary.get("fallback:printf", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("format_kind") == "print"
        and row.get("decision") == "format_fallback"
        and row.get("fallback_func") == "printf"
        for row in decisions
    )


def test_format_report_marks_interpolation_concat_path() -> None:
    src = """
def main(): int
    name = "AILang"
    msg = "Hello #{name}"
    print(msg)
    return 0
end
"""
    payload = _run_format_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("interpolation:direct_writer", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("format_kind") == "interpolation"
        and row.get("decision") == "direct_writer"
        and row.get("reason") == "concat_runtime"
        for row in decisions
    )


def test_format_report_marks_interpolation_literal_segment_writer() -> None:
    src = """
def main(): int
    name = "AILang"
    print("Hello #{name} value=#{42}")
    return 0
end
"""
    payload = _run_format_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("interpolation:direct_writer", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("format_kind") == "interpolation"
        and row.get("decision") == "direct_writer"
        and row.get("reason") == "literal_typed_segments"
        for row in decisions
    )

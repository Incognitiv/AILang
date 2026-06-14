from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _run_report(src: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "case.ail"
        src_path.write_text(src, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src_path), "--report-checks-json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)


def test_report_checks_marks_proven_elision() -> None:
    src = """
def main():
    i := 0..100 = 0
    i = i + 1
    return i
end
"""
    payload = _run_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("overflow:elided", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("check_kind") == "overflow"
        and row.get("operation") == "+"
        and row.get("decision") == "elided"
        and row.get("reason") == "range_proven"
        for row in decisions
    )


def test_report_checks_marks_unproven_inserted() -> None:
    src = """
def main(): int
    i = 9223372036854775807
    x = i + 1
    i = 0
    return x
end
"""
    payload = _run_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("overflow:inserted", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("check_kind") == "overflow"
        and row.get("operation") == "+"
        and row.get("decision") == "inserted"
        and row.get("reason") in {"result_out_of_bounds", "range_unknown"}
        for row in decisions
    )


def test_report_checks_uses_loop_unknown_reason() -> None:
    src = """
def main(): int
    i = 0
    while true then
        i = i + 1
    end
    return i
end
"""
    payload = _run_report(src)
    decisions = payload.get("decisions", [])
    assert any(
        row.get("check_kind") == "overflow"
        and row.get("operation") == "+"
        and row.get("decision") == "inserted"
        and row.get("reason") == "loop_unknown"
        for row in decisions
    )


def test_report_checks_marks_slice_bounds_elided_when_proven() -> None:
    src = """
def main(): int
    arr = [1, 2, 3, 4]
    slice[int] view = arr
    i := 0..3 = 0
    s = 0
    while i < 4 then
        s = s + view[i]
        i = i + 1
    end
    return s
end
"""
    payload = _run_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("bounds:elided", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("check_kind") == "bounds"
        and row.get("operation") == "[]"
        and row.get("decision") == "elided"
        and row.get("reason") == "range_proven"
        for row in decisions
    )


def test_report_checks_marks_slice_bounds_inserted_when_unproven() -> None:
    src = """
def main(): int
    arr = [1, 2, 3, 4]
    slice[int] view = arr
    i = 0
    s = 0
    while i < 8 then
        s = s + view[i]
        i = i + 1
    end
    return s
end
"""
    payload = _run_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("bounds:inserted", 0)) >= 1
    decisions = payload.get("decisions", [])
    assert any(
        row.get("check_kind") == "bounds"
        and row.get("operation") == "[]"
        and row.get("decision") == "inserted"
        and row.get("reason")
        in {"loop_unknown", "index_out_of_bounds", "range_unknown"}
        for row in decisions
    )


def test_report_checks_marks_fixed_array_reduction_overflow_elided() -> None:
    src = """
type Arr8 = [int; 8]

def bench(iterations):
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    acc = 0
    i = 0
    while i < iterations then
        j := 0..7 = 0
        while true then
            acc = acc + arr[j]
            if j == 7 then
                break
            end
            j = j + 1
        end
        i = i + 1
    end
    return acc
end

def main():
    return bench(1000)
end
"""
    payload = _run_report(src)
    summary = payload.get("summary", {})
    assert int(summary.get("overflow:inserted", 0)) == 0
    assert int(summary.get("overflow:elided", 0)) >= 3
    decisions = payload.get("decisions", [])
    assert all(
        row.get("decision") != "inserted"
        for row in decisions
        if row.get("check_kind") == "overflow"
    )
    reasons = {
        str(row.get("reason"))
        for row in decisions
        if row.get("check_kind") == "overflow"
    }
    assert "loop_accumulator_proven" in reasons
    assert "loop_counter_proven" in reasons or "loop_guard_proven" in reasons


def test_report_checks_marks_call_hint_proven_for_local_helper() -> None:
    src = """
def add(a, b): int
    return a + b
end

def main(): int
    return add(7, 9)
end
"""
    payload = _run_report(src)
    decisions = payload.get("decisions", [])
    assert any(
        row.get("check_kind") == "overflow"
        and row.get("operation") == "+"
        and row.get("decision") == "elided"
        and row.get("reason") == "call_hint_proven"
        for row in decisions
    )

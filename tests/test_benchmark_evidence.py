"""Negative evidence tests independent of any compiler or native toolchain."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

from benchmark_evidence import matrix_errors, measurement_errors


def row(**overrides: object) -> dict[str, object]:
    return {"status": "ok", "runs_ms": [0.25, 0.5], "checksum": 17, **overrides}


def test_complete_matrix_accepts_measured_parity() -> None:
    results = {"case": {"aot": row(), "jit": row()}}
    assert not matrix_errors(results, ["case"], ["aot", "jit"], 2, check_output=True)


@pytest.mark.parametrize("results", [{}, {"case": {}}, {"case": {"aot": row()}}])
def test_partial_or_empty_matrix_fails(results: dict) -> None:
    assert matrix_errors(results, ["case"], ["aot", "jit"], 2, check_output=True)


@pytest.mark.parametrize(
    "samples",
    [None, [], [1.0], [1.0, 2.0, 3.0], [True, 0.5], ["0.1", 0.5],
     [float("nan"), 0.5], [float("inf"), 0.5], [-1.0, 0.5], [10**1000, 0.5]],
)
def test_invalid_timing_evidence_fails(samples: object) -> None:
    assert measurement_errors(row(runs_ms=samples), 2, check_output=True)


@pytest.mark.parametrize("checksum", [None, "17", True, 17.0])
def test_invalid_checksum_fails(checksum: object) -> None:
    assert measurement_errors(row(checksum=checksum), 2, check_output=True)


def test_checksum_is_optional_only_without_output_check() -> None:
    assert not measurement_errors(row(checksum=None), 2, check_output=False)


def test_mismatched_checksum_fails() -> None:
    results = {"case": {"aot": row(), "jit": row(checksum=18)}}
    assert matrix_errors(results, ["case"], ["aot", "jit"], 2, check_output=True)


@pytest.mark.parametrize("status", ["skipped", "fail", "unsupported", None])
def test_non_success_status_fails(status: object) -> None:
    assert measurement_errors(row(status=status), 2, check_output=True)


def test_generators_cover_all_requested_cases() -> None:
    results = {"one": {"aot": row()}, "two": {}}
    assert matrix_errors(
        results, iter(["one", "two"]), iter(["aot"]), 2, check_output=True
    )


def test_empty_selection_fails() -> None:
    assert matrix_errors({}, [], ["aot"], 2, check_output=True)

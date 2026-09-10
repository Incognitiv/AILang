from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "benchmarks" / "run_benchmarks.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("ailang_benchmark_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_result_parser_ignores_jit_exit_diagnostic_after_result() -> None:
    runner = _load_runner()
    stdout = "7750000\n\n=== Program exited with code: 0 ===\n"
    assert runner._extract_result_int(stdout) == 7_750_000


def test_result_parser_ignores_jit_exit_diagnostic_before_result() -> None:
    runner = _load_runner()
    stdout = "=== Program exited with code: 0 ===\n7750000\n"
    assert runner._extract_result_int(stdout) == 7_750_000


def test_result_parser_rejects_diagnostic_only_output() -> None:
    runner = _load_runner()
    assert runner._extract_result_int("=== Program exited with code: 0 ===\n") is None

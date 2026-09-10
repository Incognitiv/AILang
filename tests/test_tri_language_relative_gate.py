from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tri_language_benchmark", ROOT / "tools" / "tri_language_benchmark.py"
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def _row(impl: str, ns: float):
    return mod.RunResult(impl=impl, kernel="numeric", status="ok", ns_per_op=ns)


def test_relative_c_gate_is_hardware_relative() -> None:
    rows = [_row("c", 20.0), _row("ailang_c", 24.0), _row("ailang_llvm", 28.0)]
    assert mod._relative_to_c_failures(rows, 1.5) == []


def test_relative_c_gate_rejects_large_slowdown() -> None:
    rows = [_row("c", 10.0), _row("ailang_c", 16.0), _row("ailang_llvm", 12.0)]
    failures = mod._relative_to_c_failures(rows, 1.5)
    assert failures == [("numeric", "ailang_c", 1.6)]

from __future__ import annotations

import re
import subprocess
import sys
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
GATES_AIL = REPO_ROOT / "source" / "hardening_gates.ail"
HARD_GATE = REPO_ROOT / "tools" / "hard_gate.py"


def _const_string(source: str, name: str) -> str:
    match = re.search(rf'^const string {name}\s*=\s*"([^"]*)"', source, re.M)
    if match is None:
        raise AssertionError(f"missing string constant {name}")
    return match.group(1)


def _const_int(source: str, name: str) -> int:
    match = re.search(rf"^const int {name}\s*=\s*(\d+)", source, re.M)
    if match is None:
        raise AssertionError(f"missing integer constant {name}")
    return int(match.group(1))


def _hard_gate_step_ids() -> list[str]:
    spec = importlib.util.spec_from_file_location("ailang_hard_gate", HARD_GATE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {HARD_GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    step_ids = getattr(module, "GATE_STEP_IDS", None)
    if not isinstance(step_ids, list):
        raise AssertionError("GATE_STEP_IDS must be a list")
    return [str(step_id) for step_id in step_ids]


def test_hardening_gate_seed_matches_python_gate_metadata() -> None:
    ail_source = GATES_AIL.read_text(encoding="utf-8")
    gate_step_ids = _hard_gate_step_ids()
    names = [
        "AILANG_GATE_SOURCE_STRICT",
        "AILANG_GATE_VERIFIER_STRICT",
        "AILANG_GATE_C23_HOSTED",
        "AILANG_GATE_C23_FREESTANDING",
        "AILANG_GATE_FOCUSED_PYTEST",
        "AILANG_GATE_C23_WORKLOAD_COMPARE",
    ]

    actual_count = _const_int(ail_source, "AILANG_HARD_GATE_STEP_COUNT")
    if actual_count != len(gate_step_ids):
        raise AssertionError(f"gate step count mismatch: {actual_count}")
    actual_names = [_const_string(ail_source, name) for name in names]
    if actual_names != gate_step_ids:
        raise AssertionError(f"gate step names mismatch: {actual_names}")


def test_hardening_gate_seed_passes_check() -> None:
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(GATES_AIL), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"--check failed for hardening_gates.ail\n{proc.stdout}\n{proc.stderr}"
        )

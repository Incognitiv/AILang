"""A missing proof tool or incomplete output must not count as semantic evidence."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("literal_proof_bridge", ROOT / "proof" / "check_literal_semantics.py")
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BRIDGE
SPEC.loader.exec_module(BRIDGE)


def case():
    return BRIDGE.LiteralCase("sample", r'"\x41"')


def test_proof_contains_raw_source_and_observed_value() -> None:
    proof = BRIDGE.render_proof([BRIDGE.Observation(case(), "ast", "A")])
    assert "decode [92,120,52,49] = [65] := by decide" in proof
    assert "native_decide" not in proof
    assert "sorry" not in proof
    assert "axiom" not in proof


def test_changed_output_is_not_replaced_with_expected_output() -> None:
    proof = BRIDGE.render_proof([BRIDGE.Observation(case(), "llvm", "B")])
    assert "decode [92,120,52,49] = [66] := by decide" in proof


def test_empty_certificate_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        BRIDGE.render_proof([])


def test_input_text_cannot_inject_lean_commands() -> None:
    source = BRIDGE.LiteralCase("untrusted", '"; axiom injected : False"')
    proof = BRIDGE.render_proof([BRIDGE.Observation(source, "ast", "")])
    assert "axiom" not in proof
    assert "injected" not in proof


def test_complete_native_frame() -> None:
    text = "driver banner\nAILANG_LITERAL_PROOF_0\n1\n65\nAILANG_LITERAL_PROOF_DONE\n"
    rows = BRIDGE.parse_native_output(text, [case()], "llvm")
    assert len(rows) == 1
    assert rows[0].value == "A"
    assert rows[0].case.literal == case().literal


@pytest.mark.parametrize("text", [
    "", "all good", "AILANG_LITERAL_PROOF_0\n", "AILANG_LITERAL_PROOF_0\n-1\n",
    "AILANG_LITERAL_PROOF_0\n999999\n", "AILANG_LITERAL_PROOF_0\n1\n256\n",
    "AILANG_LITERAL_PROOF_0\n1\n65\n", "AILANG_LITERAL_PROOF_1\n1\n65\n",
    "AILANG_LITERAL_PROOF_0\n1\n255\nAILANG_LITERAL_PROOF_DONE\n",
    "AILANG_LITERAL_PROOF_0\n0\nAILANG_LITERAL_PROOF_DONE\nAILANG_LITERAL_PROOF_0\n",
])
def test_incomplete_or_corrupt_native_output_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        BRIDGE.parse_native_output(text, [case()], "llvm")


def test_missing_native_cases_are_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        BRIDGE.parse_native_output("", [], "llvm")


@pytest.mark.parametrize("code,diagnostics", [
    (0, ""), (127, "lake missing"), (1, "unknown module AILangProof"),
    (-6, "aborted"), (1, "syntax error"),
])
def test_tool_failure_is_not_a_successful_negative_proof(code: int, diagnostics: str) -> None:
    result = subprocess.CompletedProcess([], code, diagnostics, "")
    with pytest.raises(RuntimeError):
        BRIDGE.require_rejected(result)


def test_false_equation_is_a_successful_negative_proof() -> None:
    result = subprocess.CompletedProcess([], 1, "tactic 'decide' proved that the proposition is false", "")
    BRIDGE.require_rejected(result)


def test_corpus_has_semantic_regressions_and_all_byte_values() -> None:
    cases = BRIDGE.frontend_cases()
    texts = {row.literal for row in cases}
    assert len(texts) == len(cases)
    assert r'"\0BACKSLASH\0"' in texts
    assert all(f'"\\x{value:02x}"' in texts for value in range(256))
    assert set(BRIDGE.CHANNELS) == {"lexer", "ast", "parser", "llvm", "c", "jit"}
    assert len(BRIDGE.native_source(BRIDGE.native_cases()).splitlines()) < 750


def test_golden_gate_requires_real_semantic_workflow() -> None:
    hard = (ROOT / ".github/workflows/ailang-hard-gate.yml").read_text()
    proof = (ROOT / ".github/workflows/ailang-lean-proof.yml").read_text()
    assert "needs: lean-proof" in hard
    assert "uses: ./.github/workflows/ailang-lean-proof.yml" in hard
    assert "workflow_call:" in proof
    assert "python proof/check_literal_semantics.py" in proof
    assert "python proof/check_ir_certificate.py" in proof
    assert "python proof/check_python_conformance.py" in proof
    assert "paths:" not in proof

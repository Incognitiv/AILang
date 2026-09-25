"""Tool crashes, invalid evidence and stale verdicts must never certify code."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verifier import core
from verifier.cache import VerificationCache


def clean_results() -> dict:
    """Synthetic runner results for orchestration tests, not real tool evidence."""
    return {
        "pyflakes": {"issues_count": 0},
        "strict_extras": {"issues_count": 0},
        "mypy": {"errors_count": 0},
        "bandit": {"high_severity": 0, "medium_severity": 0, "low_severity": 0},
        "radon": {"max_complexity": 1},
        "black": {"passed": True},
        "isort": {"passed": True},
        "ruff": {"passed": True},
        "vulture": {"passed": True},
        "cohesion": {"passed": True},
        "nesting": {"passed": True},
        "clone": {"passed": True},
        "magic_index": {"magic_index_count": 0},
        "positional_access": {"passed": True},
        "consistency": {"passed": True},
        "todo": {"passed": True},
        "detect_secrets": {"issues": []},
    }


def syntax_check(code: str) -> dict:
    try:
        ast.parse(code)
    except SyntaxError:
        return {"valid": False}
    return {"valid": True}


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch):
    """Test the real coordinator while replacing only external tool boundaries."""
    instance = object.__new__(core.EnhancedPythonVerifier)
    instance.available_tools = dict.fromkeys(clean_results(), True)
    instance._cache_warning_shown = False
    monkeypatch.setattr(core, "check_syntax", syntax_check)
    monkeypatch.setattr(core, "detect_suppressions", lambda code: {"total": 0})
    monkeypatch.setattr(core, "generate_summary", lambda result: "synthetic tool summary")
    return instance


def verdict(results: dict) -> dict:
    return {"overall_score": 100.0, "syntax": {"valid": True}, **results}


def test_clean_scoring_contract_still_passes(verifier) -> None:
    results = verdict(clean_results())
    results["overall_score"] = verifier._calculate_score(results)
    assert results["overall_score"] == 100
    assert verifier._determine_pass(results) == (True, [])


@pytest.mark.parametrize("tool", list(clean_results()))
def test_reported_tool_error_is_not_a_pass(verifier, tool: str) -> None:
    results = verdict(clean_results())
    results[tool] = {"error": "injected runner failure", "passed": True}
    passed, reasons = verifier._determine_pass(results)
    assert not passed
    assert any(tool in reason and "injected runner failure" in reason for reason in reasons)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf"),
                                     101, 1000, -1, None, True, "100"])
def test_invalid_aggregate_score_cannot_pass(verifier, score: object) -> None:
    results = verdict(clean_results())
    results["overall_score"] = score
    passed, reasons = verifier._determine_pass(results)
    assert not passed
    assert reasons


@pytest.mark.parametrize("bad", [None, [], "ok", 100, True])
def test_nondictionary_tool_result_does_not_crash_or_pass(verifier, bad: object) -> None:
    results = verdict(clean_results())
    results["black"] = bad
    passed, reasons = verifier._determine_pass(results)
    assert not passed
    assert any("black" in reason for reason in reasons)


@pytest.mark.parametrize("bad", [{}, {"passed": "yes"}, {"passed": 1}, {"passed": None}])
def test_formatter_must_supply_boolean_verdict(verifier, bad: dict) -> None:
    results = verdict(clean_results())
    results["black"] = bad
    assert verifier._determine_pass(results)[0] is False


@pytest.mark.parametrize("count", [-1, True, 1.5, "0", None, float("nan")])
def test_invalid_typecheck_count_cannot_certify(verifier, count: object) -> None:
    results = verdict(clean_results())
    results["mypy"] = {"errors_count": count}
    assert verifier._determine_pass(results)[0] is False


def test_malformed_payload_fails_before_scoring(verifier, monkeypatch) -> None:
    results = clean_results()
    results["mypy"] = {"errors_count": "not a number"}
    monkeypatch.setattr(verifier, "_execute_tools_parallel", lambda *a, **k: results)
    score = Mock(side_effect=AssertionError("invalid evidence must not be scored"))
    monkeypatch.setattr(verifier, "_calculate_score", score)
    result = verifier._run_verification("x = 1", "source.py", "source.py", "strict", False, None)
    assert result["passed"] is False
    assert "mypy" in result["summary"]
    score.assert_not_called()


def test_missing_requested_tool_is_not_silently_omitted(verifier, monkeypatch) -> None:
    results = clean_results()
    del results["clone"]
    monkeypatch.setattr(verifier, "_execute_tools_parallel", lambda *a, **k: results)
    result = verifier._run_verification("x = 1", "a.py", "a.py", "strict", False, None)
    assert result["passed"] is False
    assert any("clone" in reason for reason in result["fail_reasons"])


@pytest.mark.parametrize("check_imports", [False, True])
def test_contextless_cached_pass_never_skips_checks(verifier, monkeypatch, tmp_path, check_imports):
    cache = VerificationCache(tmp_path / "cache")
    code = "import missing_dependency\n"
    cache.set(code, "strict", {"passed": True, "overall_score": 100})
    results = clean_results()
    results["mypy"] = {"errors_count": 1}
    runner = Mock(return_value=results)
    monkeypatch.setattr(verifier, "_execute_tools_parallel", runner)
    result = verifier._run_verification(code, "a.py", "a.py", "strict", check_imports, cache)
    assert result["passed"] is False
    assert result["from_cache"] is False
    assert result["cache_status"] == "bypassed-incomplete-context"
    runner.assert_called_once()
    assert runner.call_args.kwargs["check_imports"] is check_imports


def test_cached_pass_cannot_bypass_even_syntax(verifier, tmp_path) -> None:
    cache = VerificationCache(tmp_path / "cache")
    code = "def !"
    cache.set(code, "strict", {"passed": True})
    result = verifier._run_verification(code, "a.py", "a.py", "strict", True, cache)
    assert result["passed"] is False
    assert result["syntax"]["valid"] is False


def test_cache_warning_once_and_no_unbound_result_io(verifier, monkeypatch, capsys) -> None:
    cache = Mock(spec=VerificationCache)
    monkeypatch.setattr(verifier, "_execute_tools_parallel", lambda *a, **k: clean_results())
    for _ in range(2):
        result = verifier._run_verification("x=1", "a.py", "a.py", "strict", False, cache)
        assert result["passed"] is True
        assert result["from_cache"] is False
    cache.get.assert_not_called()
    cache.set.assert_not_called()
    assert capsys.readouterr().err.count("full-result cache bypassed") == 1


def test_runner_value_error_is_reported(verifier) -> None:
    def broken(path):
        raise ValueError("invalid tool JSON")

    results = verifier._execute_tools_parallel([("black", broken, False)], "a.py", "a.py")
    assert "invalid tool JSON" in results["black"]["error"]
    assert verifier._determine_pass(verdict(results))[0] is False


def test_empty_job_queue_does_not_construct_zero_worker_pool(verifier) -> None:
    assert verifier._execute_tools_parallel([], "a.py", "a.py") == {}


@pytest.mark.parametrize("count", [None, "0", True, -1, float("nan"), 10**1000])
def test_invalid_magic_index_count_fails_before_score(verifier, monkeypatch, count):
    results = clean_results()
    results["magic_index"] = {"magic_index_count": count}
    monkeypatch.setattr(verifier, "_execute_tools_parallel", lambda *a, **k: results)
    result = verifier._run_verification("x=1", "a.py", "a.py", "strict", False, None)
    assert result["passed"] is False
    assert any("magic_index" in reason for reason in result["fail_reasons"])


@pytest.mark.parametrize("tool,field", [("mypy", "errors_count"),
                                       ("pyflakes", "issues_count"),
                                       ("strict_extras", "issues_count")])
def test_unrepresentable_score_counter_is_invalid(verifier, monkeypatch, tool, field):
    results = clean_results()
    results[tool] = {field: 10**1000}
    monkeypatch.setattr(verifier, "_execute_tools_parallel", lambda *a, **k: results)
    result = verifier._run_verification("x=1", "a.py", "a.py", "strict", False, None)
    assert result["passed"] is False
    assert any(tool in reason for reason in result["fail_reasons"])

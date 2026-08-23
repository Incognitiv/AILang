from __future__ import annotations

from pathlib import Path

from verifier.tools.clone import run_clone_check, run_project_clone_audit
from verifier.tools.quality import (
    run_magic_index_check,
    run_positional_access_audit,
)


def _write(tmp_path: Path, code: str) -> Path:
    target = tmp_path / "sample.py"
    target.write_text(code, encoding="utf-8")
    return target


def test_positional_access_audit_catches_chained_and_attribute_indexes(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        """
def parse(tokens, func):
    current = tokens[i][0]
    first_arg = func.args[1]
    return current, first_arg
""",
    )

    result = run_positional_access_audit(str(path), "sample.py")

    assert result["positional_access_count"] == 2
    assert result["passed"] is True
    assert any("tokens[i][0]" in issue for issue in result["issues"])
    assert any("func.args[1]" in issue for issue in result["issues"])


def test_magic_index_gate_still_catches_direct_structured_literals(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        """
def parse(token):
    return token[0]
""",
    )

    result = run_magic_index_check(str(path), "sample.py")

    assert result["passed"] is False
    assert result["magic_index_count"] == 1


def test_clone_check_detects_repeated_statement_windows(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
def first(value):
    total = value + 1
    total = total * 2
    total = total - 3
    total = total + 4
    total = total * 5
    total = total - 6
    total = total + 7
    total = total * 8
    return total

def second(other):
    score = other + 10
    score = score * 20
    score = score - 30
    score = score + 40
    score = score * 50
    score = score - 60
    score = score + 70
    score = score * 80
    return score
""",
    )

    result = run_clone_check(str(path), "sample.py")

    assert result["passed"] is True
    assert result["informational"] is True
    assert result["clone_count"] >= 1
    assert any("first:" in issue and "second:" in issue for issue in result["issues"])


def test_clone_check_ignores_short_repetitions(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
def first(value):
    total = value + 1
    total = total * 2
    return total

def second(other):
    score = other + 10
    score = score * 20
    return score
""",
    )

    result = run_clone_check(str(path), "sample.py")

    assert result["passed"] is True
    assert result["clone_count"] == 0


def test_project_clone_audit_detects_cross_file_repetition(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text(
        """
def first(value):
    total = value + 1
    total = total * 2
    total = total - 3
    total = total + 4
    total = total * 5
    total = total - 6
    total = total + 7
    total = total * 8
    return total
""",
        encoding="utf-8",
    )
    second.write_text(
        """
def second(other):
    score = other + 10
    score = score * 20
    score = score - 30
    score = score + 40
    score = score * 50
    score = score - 60
    score = score + 70
    score = score * 80
    return score
""",
        encoding="utf-8",
    )

    result = run_project_clone_audit([str(first), str(second)], str(tmp_path))

    assert result["passed"] is True
    assert result["informational"] is True
    assert result["clone_count"] >= 1
    assert any(
        "first.py" in issue and "second.py" in issue for issue in result["issues"]
    )

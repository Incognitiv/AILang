from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from syntax_surface_audit import (
    build_keyword_smoke_cases,
    build_surface_entries,
    main,
    run_keyword_smoke,
)

# Evidence for punctuation-only safe navigation; keyword smoke covers words.
SAFE_DOT_EVIDENCE = "object?.field"


def test_syntax_surface_audit_finds_language_entries():
    entries = {entry.token: entry for entry in build_surface_entries()}

    assert "RECORD" in entries
    assert "ALLOC" in entries
    assert "CIMPORT" in entries
    assert entries["RECORD"].covered
    assert all(entry.covered for entry in entries.values())


def test_syntax_surface_audit_can_write_markdown(tmp_path: Path):
    report = tmp_path / "syntax_surface.md"

    assert main(["--markdown", str(report)]) == 0

    text = report.read_text(encoding="utf-8")
    assert "# AILang Syntax Surface Audit" in text
    assert "`RECORD`" in text
    assert "`CIMPORT`" in text


def test_keyword_surface_smoke_parse_checks_all_keywords():
    cases = build_keyword_smoke_cases()
    results = run_keyword_smoke(cases)
    failures = [result for result in results if result.status != "parse-pass"]

    assert len(results) == len(cases)
    assert not failures
    assert {"SECTION", "VEC16B", "WHERE"} <= {result.token for result in results}

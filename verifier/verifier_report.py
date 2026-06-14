"""Reporting and formatting for verification results."""

import json
from typing import Dict, List, Optional

CHECK = "[OK]"
CROSS = "[X]"


def _tool_status(results: Dict, key: str, label: str) -> str:
    """Generate standard tool status line."""
    data = results.get(key)
    if not data:
        return f"[{label}] - Tool not available"
    if data.get("passed", True):
        return f"[{label}] {CHECK}"
    return f"[{label}] {CROSS}"


def _format_tool(
    results: Dict, key: str, label: str, count_key: str, unit: str
) -> List[str]:
    """Format a tool result with count."""
    data = results.get(key)
    if not data:
        return [f"[{label}] - Tool not available"]
    count = data.get(count_key, 0)
    if count == 0:
        return [f"[{label}] {CHECK} No {unit} found"]
    return [f"[{label}] {CROSS} {count} {unit} found"]


def syntax_lines(results: Dict) -> List[str]:
    """Format syntax check results."""
    if not results["syntax"]["valid"]:
        return ["[SYNTAX] INVALID - Code has syntax errors"]
    return [f"[SYNTAX] {CHECK} Valid Python syntax"]


def pyflakes_lines(results: Dict) -> List[str]:
    """Format pyflakes results."""
    return _format_tool(results, "pyflakes", "PYFLAKES", "issues_count", "issues")


def pylint_lines(results: Dict) -> List[str]:
    """Format the lint slot - prefers strict_extras (W0201 + W1114) over pylint."""
    extras = results.get("strict_extras")
    if extras is not None and "issues_count" in extras:
        n = extras["issues_count"]
        if n == 0:
            return ["[STRICT_EXTRAS] [OK] No W0201/W1114 issues"]
        return [f"[STRICT_EXTRAS] {n} issue(s)"]
    pyl = results.get("pylint")
    if not pyl or "score" not in pyl:
        return ["[STRICT_EXTRAS] - Tool not available"]
    return [
        f"[PYLINT] Score: {pyl['score']:.2f}/10 ({pyl.get('issues_count', 0)} issues)"
    ]


def mypy_lines(results: Dict) -> List[str]:
    """Format mypy results."""
    return _format_tool(results, "mypy", "MYPY", "errors_count", "type errors")


def bandit_lines(results: Dict) -> List[str]:
    """Format bandit security results."""
    bandit = results.get("bandit")
    if not bandit or "total_issues" not in bandit:
        return ["[SECURITY] - Tool not available"]
    h = bandit.get("high_severity", 0)
    m = bandit.get("medium_severity", 0)
    low = bandit.get("low_severity", 0)
    if bandit["total_issues"] == 0:
        return [f"[SECURITY] {CHECK} No vulnerabilities"]
    return [f"[SECURITY] {CROSS} {bandit['total_issues']} issues ({h}H, {m}M, {low}L)"]


def complexity_lines(results: Dict) -> List[str]:
    """Format complexity metrics."""
    comp = results.get("radon")
    if not comp or "avg_complexity" not in comp:
        return ["[COMPLEXITY] - Tool not available"]
    cc, mi = comp["avg_complexity"], comp.get("maintainability_index", 0.0)
    return [f"[COMPLEXITY] CC: {cc:.1f}, MI: {mi:.1f}"]


def vulture_lines(results: Dict) -> List[str]:
    """Format vulture dead code results."""
    return _format_tool(
        results, "vulture", "VULTURE", "dead_code_count", "dead code items"
    )


def nesting_lines(results: Dict) -> List[str]:
    """Format nesting depth results."""
    nest = results.get("nesting")
    if not nest:
        return ["[NESTING] - Tool not available"]
    depth = nest.get("max_depth", 0)
    if nest.get("passed", False):
        return [f"[NESTING] {CHECK} Max depth {depth}"]
    return [f"[NESTING] {CROSS} Max depth {depth}"]


def formatter_lines(results: Dict) -> List[str]:
    """Format black/isort/ruff results."""
    lines = []
    for key, label in [("black", "BLACK"), ("isort", "ISORT"), ("ruff", "RUFF")]:
        lines.append(_tool_status(results, key, label))
    return lines


def suppression_lines(results: Dict) -> List[str]:
    """Format suppression detection results."""
    supp = results.get("suppressions")
    if not supp:
        return [f"[SUPPRESSIONS] {CHECK} No suppressions detected"]
    total = supp.get("total", 0)
    if total == 0:
        return [f"[SUPPRESSIONS] {CHECK} No suppressions detected"]
    return [f"[SUPPRESSIONS] {CROSS} {total} suppression(s) found (technical debt)"]


def pip_audit_lines(results: Dict) -> List[str]:
    """Format pip-audit dependency vulnerability results.

    If requirements.txt is found, only those deps are checked.
    Otherwise checks entire environment (informational only).
    """
    audit = results.get("pip_audit")
    if not audit:
        return ["[PIP_AUDIT] - Tool not available"]
    vulns = audit.get("vulnerabilities_count", 0)
    source = audit.get("source", "environment")
    if vulns == 0:
        return [f"[PIP_AUDIT] {CHECK} No vulnerable dependencies"]
    if source == "requirements.txt":
        # Project deps - this is a real issue
        return [f"[PIP_AUDIT] {CROSS} {vulns} vulnerable project dependencies"]
    # Environment-wide - informational only
    return [f"[PIP_AUDIT] (~) {vulns} vulnerable deps (env-wide, not scored)"]


def detect_secrets_lines(results: Dict) -> List[str]:
    """Format detect-secrets results."""
    secrets = results.get("detect_secrets")
    if not secrets:
        return ["[SECRETS] - Tool not available"]
    count = secrets.get("secrets_count", 0)
    if count == 0:
        return [f"[SECRETS] {CHECK} No hardcoded secrets found"]
    return [f"[SECRETS] {CROSS} {count} potential secrets found"]


def magic_index_lines(results: Dict) -> List[str]:
    """Format magic indexing check results."""
    magic = results.get("magic_index")
    if not magic:
        return ["[MAGIC_INDEX] - Tool not available"]
    count = magic.get("magic_index_count", 0)
    if count == 0:
        return [f"[MAGIC_INDEX] {CHECK} No magic indexing found"]
    return [f"[MAGIC_INDEX] {CROSS} {count} magic index patterns (use named fields)"]


def positional_access_lines(results: Dict) -> List[str]:
    """Format broad positional access audit results."""
    access = results.get("positional_access")
    if not access:
        return ["[POSITIONAL_ACCESS] - Tool not available"]
    count = access.get("positional_access_count", 0)
    if count == 0:
        return [f"[POSITIONAL_ACCESS] {CHECK} No broad positional access found"]
    return [f"[POSITIONAL_ACCESS] (~) {count} positional access sites to refactor"]


def clone_lines(results: Dict) -> List[str]:
    """Format copy-paste clone check results."""
    clone = results.get("clone")
    if not clone:
        return ["[CLONES] - Tool not available"]
    count = clone.get("clone_count", 0)
    if count == 0:
        return [f"[CLONES] {CHECK} No structural copy-paste clones found"]
    return [f"[CLONES] (~) {count} structural copy-paste clone patterns"]


def consistency_lines(results: Dict) -> List[str]:
    """Format consistency check results."""
    cons = results.get("consistency")
    if not cons:
        return ["[CONSISTENCY] - Tool not available"]
    count = cons.get("consistency_issues", 0)
    if count == 0:
        return [f"[CONSISTENCY] {CHECK} Code style is consistent"]
    return [f"[CONSISTENCY] {CROSS} {count} consistency issues"]


def todo_lines(results: Dict) -> List[str]:
    """Format TODO/FIXME check results.

    This is informational only - doesn't affect pass/fail.
    """
    todo = results.get("todo")
    if not todo:
        return ["[TODO] - Tool not available"]
    total = todo.get("total_count", 0)
    if total == 0:
        return [f"[TODO] {CHECK} No TODO/FIXME markers found"]
    # Break down by type
    counts = todo.get("counts_by_type", {})
    parts = []
    for marker in ["TODO", "FIXME", "HACK", "BUG", "INCOMPLETE"]:
        count = counts.get(marker, 0)
        if count > 0:
            parts.append(f"{count} {marker}")
    summary = ", ".join(parts) if parts else f"{total} markers"
    return [f"[TODO] (~) {summary} (informational)"]


def generate_summary(results: Dict) -> str:
    """Generate complete human-readable summary."""
    lines = syntax_lines(results)
    if not results["syntax"]["valid"]:
        return "\n".join(lines)
    summary_sections = [
        pyflakes_lines,
        pylint_lines,
        mypy_lines,
        bandit_lines,
        complexity_lines,
        vulture_lines,
        nesting_lines,
        formatter_lines,
        suppression_lines,
        pip_audit_lines,
        detect_secrets_lines,
        clone_lines,
        magic_index_lines,
        positional_access_lines,
        consistency_lines,
        todo_lines,
    ]
    for section in summary_sections:
        lines.extend(section(results))
    score = results.get("overall_score", 0.0)
    lines.append(f"TOTAL: {score:.2f}/100")
    return "\n".join(lines)


def _print_section(title: str, items: Optional[List]) -> None:
    """Print a section with items."""
    if not items:
        return
    print(f"\n[{title}]")
    for entry in items:
        print(f"  - {entry}")


def _print_suppression_details(suppressions: Optional[Dict]) -> None:
    """Print detailed suppression information."""
    if not suppressions or suppressions.get("total", 0) == 0:
        return
    print("\n[Suppressions (Technical Debt)]")
    print("  These comments hide issues instead of fixing them:")
    for line_num, line_text, supp_type in suppressions.get("details", []):
        # Truncate long lines
        display_line = line_text[:60] + "..." if len(line_text) > 60 else line_text
        print(f"  Line {line_num}: {supp_type}")
        print(f"    {display_line}")


def print_report(results: Dict, json_mode: bool = False) -> None:
    """Print detailed verification report or JSON."""
    if json_mode:
        print(json.dumps(results, indent=2))
        return
    print("=" * 70)
    print("PYTHON CODE VERIFICATION REPORT")
    print("=" * 70)
    print(results.get("summary", ""))
    if results.get("overall_score", 100) < 100:
        print("\n" + "=" * 70)
        print("ISSUES")
        print("=" * 70)
        sections = [
            ("Pyflakes", "pyflakes", "issues"),
            ("Pylint", "pylint", "issues"),
            ("Mypy", "mypy", "errors"),
            ("Security", "bandit", "issues"),
            ("Dead Code", "vulture", "issues"),
            ("Copy-Paste Clones", "clone", "issues"),
            # Magic-index issues already carry `file:line: ...` text; just
            # print them so the user sees WHERE the magic happened, not
            # only that some did. Same shape as the other sections so the
            # report stays uniform.
            ("Magic Indexing", "magic_index", "issues"),
            ("Positional Access Audit", "positional_access", "issues"),
        ]
        for title, key, subkey in sections:
            data = results.get(key, {})
            _print_section(title, data.get(subkey) if data else None)

        # Print suppression details
        _print_suppression_details(results.get("suppressions"))

    # Always print TODO items if any exist (informational)
    todo_data = results.get("todo", {})
    if todo_data and todo_data.get("total_count", 0) > 0:
        print("\n" + "=" * 70)
        print("TODO/FIXME MARKERS (Incomplete Implementations)")
        print("=" * 70)
        for item in todo_data.get("issues", []):
            print(f"  {item}")
    print("=" * 70)

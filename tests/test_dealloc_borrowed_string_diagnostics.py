from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _check_json(tmp_path: Path, source: str) -> list[dict[str, object]]:
    src = tmp_path / "case.ail"
    src.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check-json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)["diagnostics"]


def test_check_json_warns_for_dealloc_direct_string_literal(tmp_path: Path) -> None:
    diagnostics = _check_json(
        tmp_path,
        """\
def main(): int
    dealloc("borrowed")
    return 0
end
""",
    )
    assert any(
        d["severity"] == "warning"
        and "dealloc() called on a string literal" in str(d["message"])
        for d in diagnostics
    )


def test_check_json_warns_for_dealloc_variable_assigned_literal(
    tmp_path: Path,
) -> None:
    diagnostics = _check_json(
        tmp_path,
        """\
def main(): int
    remote_a = "pgrep -f rvs-agentd"
    dealloc(remote_a)
    return 0
end
""",
    )
    assert any(
        d["severity"] == "warning"
        and "dealloc(remote_a) may free a borrowed string literal" in str(d["message"])
        for d in diagnostics
    )


def test_check_json_allows_dealloc_of_concat_owned_string(tmp_path: Path) -> None:
    diagnostics = _check_json(
        tmp_path,
        """\
def main(): int
    token_prefix = "" + ""
    remote_a = token_prefix + "pgrep -f rvs-agentd"
    dealloc(remote_a)
    return 0
end
""",
    )
    messages = [str(d["message"]) for d in diagnostics]
    assert not any("dealloc(remote_a) may free" in msg for msg in messages)

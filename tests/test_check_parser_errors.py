from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_plain_check_rejects_reserved_identifier_caught_by_parser(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reserved.ail"
    source.write_text(
        """\
int identity(end: int):
    return end
end
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(AILANG), str(source), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1
    assert "Expected IDENT, got END" in result.stdout

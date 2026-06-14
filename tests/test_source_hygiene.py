from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
MECHANICAL_PART_RE = re.compile(r"(^|_)part\d+($|_)")


def test_active_python_sources_avoid_mechanical_part_names() -> None:
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and MECHANICAL_PART_RE.search(path.stem)
    ]

    assert offenders == []

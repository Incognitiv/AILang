"""Tracked implementation inventory with an explicit 750-physical-line limit.

No directory names or generated-code comments exempt tracked source files.
Documentation/data are outside this code metric; the public-tree audit still
checks its wider text policy independently.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MAX_SOURCE_LINES = 750
SOURCE_SUFFIXES = frozenset(
    {
        ".ail", ".asm", ".c", ".cc", ".cmake", ".cpp", ".cxx", ".go",
        ".h", ".hpp", ".inc", ".java", ".js", ".jsx", ".lean", ".ps1",
        ".py", ".pyi", ".qml", ".rs", ".s", ".sh", ".ts", ".tsx",
    }
)
SOURCE_NAMES = frozenset({"CMakeLists.txt", "Makefile"})


def count_physical_lines(text: str) -> int:
    """Count LF, CRLF and CR lines, not Unicode separators inside literals."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.count("\n") + int(bool(normalized) and not normalized.endswith("\n"))


def inventory_paths(root: Path, paths: Iterable[str]) -> dict[str, Any]:
    """Inspect every source path provided by the version-control inventory."""
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for name in sorted(set(paths)):
        relative = Path(name)
        if relative.suffix.lower() not in SOURCE_SUFFIXES and relative.name not in SOURCE_NAMES:
            continue
        if relative.is_absolute() or ".." in relative.parts:
            issues.append(f"invalid source path: {name}")
            continue
        path = root / relative
        try:
            if path.is_symlink():
                raise ValueError("source symlink is not a self-contained source file")
            with path.open("r", encoding="utf-8", newline="") as source:
                line_count = count_physical_lines(source.read())
        except (OSError, ValueError) as error:
            issues.append(f"source inventory read failed: {name}: {error}")
            continue
        rows.append({"path": relative.as_posix(), "lines": line_count})
    if not rows:
        issues.append("source inventory is empty")
    oversized = [row for row in rows if row["lines"] > MAX_SOURCE_LINES]
    issues.extend(f"source line limit: {row['path']}:{row['lines']}" for row in oversized)
    largest = sorted(rows, key=lambda row: (-row["lines"], row["path"]))[:10]
    return {
        "passed": not issues,
        "scope": "all tracked implementation files, no path exemptions",
        "limit": MAX_SOURCE_LINES,
        "scanned_files": len(rows),
        "max_file_line_count": max((row["lines"] for row in rows), default=0),
        "oversized_files": oversized,
        "largest_files": largest,
        "files": rows,
        "issues": issues,
    }


def audit_tracked_sources(root: Path) -> dict[str, Any]:
    """Fail visibly when the repository's tracked-source list is unavailable."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=30,
        )
        paths = result.stdout.decode("utf-8").split("\0")
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        return {
            "passed": False, "scanned_files": 0, "max_file_line_count": 0,
            "oversized_files": [], "largest_files": [], "files": [],
            "issues": [f"tracked-source inventory unavailable: {error}"],
        }
    return inventory_paths(root, (path for path in paths if path))

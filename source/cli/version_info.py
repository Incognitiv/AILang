"""Canonical project-version lookup for CLI entry points."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 compatibility
    import tomli as tomllib

DEFAULT_VERSION = "1.8.0"


def read_project_version() -> str:
    """Read the package version with a source-tree fallback."""
    try:
        import version as project_version

        raw = getattr(project_version, "__version__", "")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except ImportError:
        pass

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return DEFAULT_VERSION

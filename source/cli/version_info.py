"""Canonical project-version lookup for CLI entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_VERSION = "1.8.0"


def _load_toml(text: str) -> dict[str, Any]:
    """Parse TOML on Python 3.10+ without redefining compatibility imports."""
    try:
        import tomllib
    except ImportError:
        import importlib

        parser = importlib.import_module("tomli")
        loader = getattr(parser, "loads")
        return loader(text)
    return tomllib.loads(text)


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
        data = _load_toml(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except (OSError, ValueError):
        pass
    return DEFAULT_VERSION

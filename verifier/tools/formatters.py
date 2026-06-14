"""Formatter tool runners: black, isort."""

from __future__ import annotations

from typing import Any, Dict

from .common import read_file, validate_filepath


def run_black(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Run black using its Python API."""
    try:
        import black
    except ImportError:
        return {"error": "black not installed"}
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    try:
        code = read_file(validated_path)
        try:
            mode = black.Mode()
            black.format_file_contents(code, fast=False, mode=mode)
            passed = False
        except black.NothingChanged:
            passed = True
        return {"passed": passed, "issues": [] if passed else ["Formatting required"]}
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


def run_isort(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Run isort using its Python API."""
    try:
        import isort
        from isort.exceptions import FileSkipComment
    except ImportError:
        return {"error": "isort not installed"}
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    try:
        passed = isort.check_file(validated_path, profile="black")
        return {"passed": passed, "issues": [] if passed else ["Import sorting needed"]}
    except FileSkipComment:
        # File has isort:skip_file comment - treat as passed
        return {"passed": True, "issues": [], "skipped": True}

"""
AILang Code Verifier Module

This module provides multi-tool code quality verification for Python.
Run as ``python -m verifier.cli`` from the project root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "1.0.0"

if TYPE_CHECKING:
    from .core import EnhancedPythonVerifier

__all__ = ["EnhancedPythonVerifier"]


def __getattr__(name: str):
    if name == "EnhancedPythonVerifier":
        from .core import EnhancedPythonVerifier

        return EnhancedPythonVerifier
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""
AILang Code Verifier Module

This module provides multi-tool code quality verification for Python.
Run as ``python -m verifier.cli`` from the project root.
"""

__version__ = "1.0.0"

from .core import EnhancedPythonVerifier

__all__ = ["EnhancedPythonVerifier"]

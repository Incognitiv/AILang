"""
Shared types and constants for the LLVM expression-generator family.

Lives here so both ``emit_expressions.py`` (the host class) and the
extracted builtin mixins (``expr_simd.py`` and any future siblings)
can import without forming a circular dependency.
"""

from __future__ import annotations


class ExprGenError(Exception):
    """Exception raised during LLVM expression code generation."""


# Builtin function argument positions (avoid magic-index warnings in callers).
ARG_FIRST = 0
ARG_SECOND = 1
ARG_THIRD = 2
ARG_FOURTH = 3
ARG_FIFTH = 4

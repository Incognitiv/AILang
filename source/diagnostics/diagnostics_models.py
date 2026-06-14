"""Diagnostic model objects."""

from __future__ import annotations

from typing import Optional


class Fix:
    """Represents an auto-fixable change."""

    def __init__(self, line: int, old_text: str, new_text: str):
        self.line = line
        self.old_text = old_text
        self.new_text = new_text

    def __str__(self) -> str:
        return f"Line {self.line}: '{self.old_text}' -> '{self.new_text}'"


class Diagnostic:
    """A single diagnostic message with optional fix."""

    def __init__(
        self,
        line: int,
        column: int,
        message: str,
        suggestion: Optional[str] = None,
        severity: str = "error",  # error, warning, hint
        fix: Optional[Fix] = None,  # Auto-fix if available
    ):
        self.line = line
        self.column = column
        self.message = message
        self.suggestion = suggestion
        self.severity = severity
        self.fix = fix

    def __str__(self) -> str:
        result = f"Line {self.line}:{self.column}: [{self.severity}] {self.message}"
        if self.suggestion:
            result += f"\n  Hint: {self.suggestion}"
        if self.fix:
            result += " [auto-fixable]"
        return result


# -----------------------------------------------------------------------------
# DiagnosticEngine - the main analyzer
# -----------------------------------------------------------------------------

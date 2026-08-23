"""Data and presentation models for transpile validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls) -> None:
        """Disable colors for non-ANSI terminals."""
        cls.HEADER = ""
        cls.BLUE = ""
        cls.CYAN = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.RED = ""
        cls.BOLD = ""
        cls.DIM = ""
        cls.RESET = ""


@dataclass
class FunctionInfo:
    """Information about a function/method in source code."""

    name: str
    params: List[str]
    return_type: Optional[str]
    body_lines: int
    body_hash: str
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_method: bool = False
    class_name: Optional[str] = None
    start_line: int = 0
    end_line: int = 0


@dataclass
class ClassInfo:
    """Information about a class in source code."""

    name: str
    bases: List[str]
    methods: List[FunctionInfo]
    fields: Dict[str, str]
    start_line: int = 0
    end_line: int = 0


@dataclass
class TranspileResult:
    """Result of transpiling a single file."""

    source_file: str
    success: bool
    python_functions: List[FunctionInfo]
    ailang_functions: List[FunctionInfo]
    python_classes: List[ClassInfo]
    ailang_records: List[str]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    functions_matched: int = 0
    functions_missing: int = 0
    functions_extra: int = 0
    body_mismatches: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def total_functions(self) -> int:
        return len(self.python_functions)

    @property
    def match_percentage(self) -> float:
        if self.total_functions == 0:
            return 100.0
        return (self.functions_matched / self.total_functions) * 100


@dataclass
class ValidationReport:
    """Complete validation report for multiple files."""

    files_processed: int = 0
    files_passed: int = 0
    files_failed: int = 0
    total_functions: int = 0
    total_matched: int = 0
    results: List[TranspileResult] = field(default_factory=list)

    @property
    def overall_match_rate(self) -> float:
        if self.total_functions == 0:
            return 100.0
        return (self.total_matched / self.total_functions) * 100

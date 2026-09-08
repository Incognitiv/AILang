"""Shared paths and parsing constants for the legacy-main comparison tool."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD_PROTOTYPE_ROOT = REPO_ROOT.parent / "AILang-main" / "prototype"
DEFAULT_NEW_ENTRY = REPO_ROOT / "ailang.py"
DEFAULT_OUT_JSON = (
    REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.json"
)
DEFAULT_OUT_MD = REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.md"
LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)
INT_TOKEN_RE = re.compile(r"[-+]?\d+")
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"

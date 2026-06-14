"""Utility helpers for diagnostics (tokenize + typo matching)."""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Callable, List, Optional, Tuple

# Import AILang lexer - handle both package and direct execution
TokenList = List[Tuple[Any, ...]]
TokenizeFunc = Callable[[str], TokenList]

_tokenize_func: TokenizeFunc

try:
    from lexer.scan import tokenize as _ailang_tokenize

    _tokenize_func = _ailang_tokenize
except ImportError:
    # Direct script execution - lexer.py in same directory
    _lexer_path = os.path.join(os.path.dirname(__file__), "lexer.py")
    _spec = importlib.util.spec_from_file_location("lexer", _lexer_path)
    if _spec and _spec.loader:
        _lexer_mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_lexer_mod)
        _tokenize_func = _lexer_mod.tokenize
    else:
        raise ImportError("Cannot load lexer module") from None


def tokenize(source: str) -> TokenList:
    """Wrapper for lexer tokenize function."""
    return _tokenize_func(source)


# -----------------------------------------------------------------------------
# Levenshtein Distance - finds typos like "pritn" -> "print"
# -----------------------------------------------------------------------------


def levenshtein(s1: str, s2: str) -> int:
    """Calculate edit distance between two strings. O(n*m) but small strings."""
    if len(s1) < len(s2):
        # Intentional swap: keep the longer string as s1 to simplify the loop.
        # Use keyword args so static checkers can see this is deliberate.
        return levenshtein(s1=s2, s2=s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost is 0 if chars match, 1 otherwise
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def find_closest(name: str, candidates: set, max_distance: int = 2) -> Optional[str]:
    """Find the closest match to 'name' from candidates."""
    best_match = None
    best_distance = max_distance + 1

    for candidate in candidates:
        dist = levenshtein(name, candidate)
        if dist < best_distance:
            best_distance = dist
            best_match = candidate

    return best_match if best_distance <= max_distance else None


# -----------------------------------------------------------------------------
# Pattern Database - common mistakes and their fixes
# -----------------------------------------------------------------------------

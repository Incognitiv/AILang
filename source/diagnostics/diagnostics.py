"""AILang Diagnostics entrypoint and orchestration."""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Tuple

from .diagnostics_engine_checks import (
    CONTINUATION_OPERATORS,
)
from .diagnostics_engine_checks import (
    _check_concurrency_hints as _m_check_concurrency_hints,
)
from .diagnostics_engine_checks import _check_dead_code as _m_check_dead_code
from .diagnostics_engine_checks import (
    _check_dealloc_borrowed_strings as _m_check_dealloc_borrowed_strings,
)
from .diagnostics_engine_checks import (
    _check_division_by_zero as _m_check_division_by_zero,
)
from .diagnostics_engine_checks import _check_infinite_loops as _m_check_infinite_loops
from .diagnostics_engine_checks import _check_patterns as _m_check_patterns
from .diagnostics_engine_checks import _check_token_hints as _m_check_token_hints
from .diagnostics_engine_checks import (
    _check_unknown_identifiers as _m_check_unknown_identifiers,
)
from .diagnostics_engine_checks import _check_unused_globals as _m_check_unused_globals
from .diagnostics_engine_checks import (
    _check_unused_variables as _m_check_unused_variables,
)
from .diagnostics_engine_ffi_checks import (
    _check_opaque_record_by_value_use as _m_check_opaque_record_by_value_use,
)
from .diagnostics_engine_symbols import _collect_symbols as _m_collect_symbols
from .diagnostics_engine_symbols import (
    _collect_symbols_from_file as _m_collect_symbols_from_file,
)
from .diagnostics_engine_symbols import _resolve_imports as _m_resolve_imports
from .diagnostics_models import Diagnostic, Fix
from .diagnostics_utils import tokenize


class DiagnosticEngine:
    """Analyzes AILang code and provides helpful diagnostics."""

    _CONTINUATION_OPERATORS = CONTINUATION_OPERATORS

    def __init__(self):
        self.user_symbols: set = set()
        self.diagnostics: List[Diagnostic] = []
        self.filepath: str = ""

    def analyze(self, source: str, filepath: str = "") -> List[Diagnostic]:
        """Analyze source code and return diagnostics."""
        self.diagnostics = []
        self.user_symbols = set()
        self.filepath = filepath

        try:
            tokens = tokenize(source)
        except (SyntaxError, ValueError) as e:
            self.diagnostics.append(
                Diagnostic(
                    line=1, column=1, message=f"Lexer error: {e}", severity="error"
                )
            )
            return self.diagnostics

        token_list: List[Tuple[str, str, int, int]] = []
        for raw_tok in tokens:
            parts = tuple(raw_tok)
            if len(parts) >= 4:
                ttype, tval, tline_raw, tcol_raw = parts[0:4]
                ttype, tval = str(ttype), str(tval)
                tline, tcol = int(tline_raw), int(tcol_raw)
            elif len(parts) >= 3:
                ttype, tval, tline_raw = parts[0:3]
                ttype, tval, tline = str(ttype), str(tval), int(tline_raw)
                tcol = 1
            elif len(parts) >= 2:
                ttype_raw, tval_raw = parts[0:2]
                ttype, tval = str(ttype_raw), str(tval_raw)
                tline = 1
                tcol = 1
            else:
                continue
            token_list.append((ttype, tval, tline, tcol))

        self._collect_symbols(token_list)
        self._resolve_imports(token_list)
        self._check_unknown_identifiers(token_list)
        self._check_patterns(token_list)
        self._check_token_hints(token_list)
        self._check_dealloc_borrowed_strings(token_list)
        self._check_division_by_zero(token_list)
        self._check_unused_variables(token_list)
        self._check_unused_globals(token_list)
        self._check_dead_code(token_list)
        self._check_infinite_loops(token_list)
        self._check_concurrency_hints(token_list)
        self._check_opaque_record_by_value_use(source, token_list)

        return self.diagnostics

    _collect_symbols = _m_collect_symbols
    _resolve_imports = _m_resolve_imports
    _collect_symbols_from_file = _m_collect_symbols_from_file
    _check_unknown_identifiers = _m_check_unknown_identifiers
    _check_patterns = _m_check_patterns
    _check_token_hints = _m_check_token_hints
    _check_dealloc_borrowed_strings = _m_check_dealloc_borrowed_strings
    _check_division_by_zero = _m_check_division_by_zero
    _check_unused_variables = _m_check_unused_variables
    _check_unused_globals = _m_check_unused_globals
    _check_dead_code = _m_check_dead_code
    _check_infinite_loops = _m_check_infinite_loops
    _check_concurrency_hints = _m_check_concurrency_hints
    _check_opaque_record_by_value_use = _m_check_opaque_record_by_value_use


# -----------------------------------------------------------------------------
# Auto-fix functionality
# -----------------------------------------------------------------------------


def apply_fixes(filepath: str, diagnostics: List[Diagnostic]) -> Tuple[int, str]:
    """
    Apply auto-fixes to a file.

    Returns (num_fixes_applied, new_content).
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Collect all fixes, grouped by line
    fixes_by_line: Dict[int, List[Fix]] = {}
    for diag in diagnostics:
        if diag.fix:
            line_num = diag.fix.line
            if line_num not in fixes_by_line:
                fixes_by_line[line_num] = []
            fixes_by_line[line_num].append(diag.fix)

    # Apply fixes (in reverse order to preserve line numbers)
    num_applied = 0
    for line_num in sorted(fixes_by_line.keys(), reverse=True):
        if 1 <= line_num <= len(lines):
            line_idx = line_num - 1  # Convert to 0-based
            line = lines[line_idx]
            for fix in fixes_by_line[line_num]:
                if fix.old_text in line:
                    line = line.replace(fix.old_text, fix.new_text, 1)
                    num_applied += 1
            lines[line_idx] = line

    return num_applied, "".join(lines)


def fix_file(filepath: str) -> int:
    """
    Analyze and fix a file in place.

    Returns number of fixes applied.
    """
    diagnostics = analyze_file(filepath)
    fixable = [d for d in diagnostics if d.fix]

    if not fixable:
        print(f"{filepath}: No auto-fixable issues")
        return 0

    num_applied, new_content = apply_fixes(filepath, diagnostics)

    if num_applied > 0:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"{filepath}: Applied {num_applied} fix(es)")

    return num_applied


# -----------------------------------------------------------------------------
# Main - command line interface
# -----------------------------------------------------------------------------


def analyze_file(filepath: str) -> List[Diagnostic]:
    """Analyze a file and return diagnostics."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    engine = DiagnosticEngine()
    return engine.analyze(source, filepath=os.path.abspath(filepath))


def main() -> None:
    """Command-line interface."""
    if len(sys.argv) < 2:
        # Demo mode - show example
        demo_code = """
def factorial(n):
    if n = 1 then
        return 1
    end
    return n * factoril(n - 1)
end

def main():
    x = 10
    pritn(factorial(x))
    return 0
end
"""
        print("=== AILang Diagnostics Demo ===")
        print("Input code:")
        print(demo_code)
        print("\n=== Diagnostics ===")

        engine = DiagnosticEngine()
        diagnostics = engine.analyze(demo_code)

        if not diagnostics:
            print("No issues found!")
        else:
            for d in diagnostics:
                print(d)
                print()

        return

    # Check for --fix flag
    fix_mode = "--fix" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("Usage: python -m diagnostics.diagnostics <file.ail> [--fix]")
        print("  --fix  Apply auto-fixes for trivial issues")
        sys.exit(1)

    filepath, *_rest = args

    try:
        if fix_mode:
            # Fix mode - apply auto-fixes
            diagnostics = analyze_file(filepath)
            fixable = [d for d in diagnostics if d.fix]

            if not fixable:
                print(f"{filepath}: No auto-fixable issues")
                # Still show other issues
                other = [d for d in diagnostics if not d.fix]
                if other:
                    print(f"\nNon-fixable issues ({len(other)}):")
                    for d in other:
                        print(d)
                        print()
            else:
                print(f"Found {len(fixable)} auto-fixable issue(s):")
                for d in fixable:
                    print(f"  {d.fix}")

                num_applied, new_content = apply_fixes(filepath, diagnostics)

                if num_applied > 0:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"\nApplied {num_applied} fix(es) to {filepath}")

                # Show remaining issues
                remaining = analyze_file(filepath)
                if remaining:
                    print(f"\nRemaining issues ({len(remaining)}):")
                    for d in remaining:
                        print(d)
                        print()
        else:
            # Check mode - just report issues
            diagnostics = analyze_file(filepath)

            if not diagnostics:
                print(f"{filepath}: No issues found!")
            else:
                for d in diagnostics:
                    print(d)
                    print()

                fixable = [d for d in diagnostics if d.fix]
                print(f"Total: {len(diagnostics)} issue(s)")
                if fixable:
                    print(f"  ({len(fixable)} auto-fixable - run with --fix)")

    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        sys.exit(1)
    except (OSError, UnicodeDecodeError) as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

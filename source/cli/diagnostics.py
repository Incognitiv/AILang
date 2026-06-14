"""CLI diagnostics helpers."""

from __future__ import annotations

import re
from parser.parser import Parser

from cli.cinclude_diagnostics import collect_cinclude_diagnostics
from diagnostics.diagnostics import analyze_file, apply_fixes
from diagnostics.effect_policy import (
    collect_effect_policy_violations,
    violations_to_diagnostics,
)
from diagnostics.static_analysis import analyze_ast
from lexer.scan import tokenize
from runtime.modes import CompilationContext

DIAGNOSTICS_AVAILABLE = True


def _parse_program_ast(source_file: str, *, resolve_imports: bool = False) -> list:
    """Parse a source file, optionally expanding AILang imports/cimports."""
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()
    tokens = tokenize(source)
    parser = Parser(tokens)
    program_ast = parser.parse_program()
    if not resolve_imports:
        return program_ast

    from transpiler.import_resolver import ImportResolver

    return ImportResolver().run(program_ast, source_file)


def run_prepass(source_file: str, warnings_as_errors: bool = False) -> bool:
    """

    Run diagnostic prepass before compilation.



    Runs token-level diagnostics AND AST-level static analysis

    (null dereference + race detection) in a single pass.

    Returns True if compilation should proceed, False if errors found.

    """
    if not DIAGNOSTICS_AVAILABLE:
        return True  # Can't check, proceed anyway
    try:
        diagnostics = analyze_file(source_file)
        # Also run AST-level static analysis (null flow + race detection)
        static_warnings = _run_ast_analysis(source_file)
        if static_warnings:
            for sw in static_warnings:
                diagnostics.append(sw)
        effect_diags = _collect_effect_policy_diagnostics(source_file)
        if effect_diags:
            diagnostics.extend(effect_diags)
        cinclude_diags = _collect_cinclude_diagnostics(source_file)
        if cinclude_diags:
            diagnostics.extend(cinclude_diags)
        if not diagnostics:
            return True  # Clean, proceed
        # Separate errors from warnings
        errors = [
            d for d in diagnostics if getattr(d, "severity", "warning") == "error"
        ]
        warnings = [
            d for d in diagnostics if getattr(d, "severity", "warning") == "warning"
        ]
        if errors:
            print(f"=== Prepass found {len(errors)} error(s) ===\n")
            for d in errors:
                print(d)
                print()
            fixable = [d for d in errors if hasattr(d, "fix") and d.fix]
            if fixable:
                print(f"({len(fixable)} auto-fixable - run with --fix)")
            print("\nFix errors before compilation.")
            return False
        if warnings:
            if warnings_as_errors:
                print(
                    f"=== Prepass found {len(warnings)} warning(s) (treated as errors) ===\n"
                )
                for d in warnings:
                    print(d)
                    print()
                print("\nFix warnings before compilation (or remove -W flag).")
                return False
            # Show warnings but continue
            print(f"=== Prepass: {len(warnings)} warning(s) ===")
            for d in warnings:
                line = getattr(d, "line", 0)
                col = getattr(d, "column", 0)
                msg = getattr(d, "message", str(d))
                print(f"  {line}:{col}: {msg}")
            print()  # Continue to compilation
        return True
    except (OSError, ValueError, AttributeError):
        # Prepass is auxiliary  -  never block the compilation pipeline
        # if the diagnostics machinery itself trips. Specific catches
        # rather than bare Exception so genuine programming errors
        # still surface.
        return True


def _collect_effect_policy_diagnostics(source_file: str) -> list:
    """Collect hosted/freestanding capability violations as diagnostics."""
    try:
        program_ast = _parse_program_ast(source_file, resolve_imports=True)
        mode = CompilationContext.get_mode()
        violations = collect_effect_policy_violations(program_ast, mode)
        # Keep compile/check compatibility for existing programs:
        # missing @effect(...) is advisory and reported via
        # --effect-policy; hard mode-policy violations remain errors.
        enforced = [row for row in violations if row.kind != "missing_effect"]
        return violations_to_diagnostics(enforced)
    except (OSError, ValueError, SyntaxError):
        # Best-effort prepass helper.
        return []


def _collect_cinclude_diagnostics(source_file: str) -> list:
    """Collect directive-level `#cinclude` diagnostics."""
    try:
        return collect_cinclude_diagnostics(source_file)
    except (OSError, RuntimeError, SyntaxError, ValueError):
        return []


def run_effect_policy_gate(source_file: str) -> bool:
    """Enforce capability/effect policy even when --no-prepass is used."""
    diags = _collect_effect_policy_diagnostics(source_file)
    errors = [d for d in diags if getattr(d, "severity", "warning") == "error"]
    if not errors:
        return True
    print(f"=== Effect policy found {len(errors)} violation(s) ===\n")
    for d in errors:
        print(d)
        print()
    print("Fix effect/mode violations before compilation.")
    return False


def _run_ast_analysis(source_file: str) -> list:
    """Run AST-level static analysis (null flow + race detection).



    Returns a list of AnalysisWarning objects, or empty list on failure.

    """
    try:
        program_ast = _parse_program_ast(source_file, resolve_imports=True)
        return analyze_ast(program_ast)
    except (OSError, ValueError, SyntaxError):
        # Best-effort: AST analysis is auxiliary; never block the
        # pipeline if the source has syntax issues the prepass missed.
        return []


def run_diagnostics(source_file: str, fix_mode: bool = False) -> int:
    """Run diagnostics on a source file and print helpful hints."""
    if not DIAGNOSTICS_AVAILABLE:
        print("Warning: Diagnostics module not available")
        return 1
    try:
        diagnostics = analyze_file(source_file)
        diagnostics.extend(_run_ast_analysis(source_file))
        diagnostics.extend(_collect_effect_policy_diagnostics(source_file))
        diagnostics.extend(_collect_cinclude_diagnostics(source_file))
        if not diagnostics:
            print(f"{source_file}: No issues found")
            return 0
        if fix_mode:
            # Fix mode - apply auto-fixes
            fixable = [d for d in diagnostics if getattr(d, "fix", None)]
            if not fixable:
                print(f"{source_file}: No auto-fixable issues")
            else:
                print(f"Found {len(fixable)} auto-fixable issue(s):")
                for d in fixable:
                    print(f"  {getattr(d, 'fix', None)}")
                num_applied, new_content = apply_fixes(source_file, fixable)
                if num_applied > 0:
                    with open(source_file, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"\nApplied {num_applied} fix(es) to {source_file}")
            # Show remaining issues
            remaining = analyze_file(source_file)
            remaining.extend(_run_ast_analysis(source_file))
            remaining.extend(_collect_effect_policy_diagnostics(source_file))
            remaining.extend(_collect_cinclude_diagnostics(source_file))
            if remaining:
                print(f"\nRemaining issues ({len(remaining)}):")
                for d in remaining:
                    print(d)
                    print()
            return len(remaining) if remaining else 0
        # Check mode - just report issues
        print(f"=== Diagnostics for {source_file} ===\n")
        for d in diagnostics:
            print(d)
            print()
        fixable = [d for d in diagnostics if getattr(d, "fix", None)]
        print(f"Total: {len(diagnostics)} issue(s)")
        if fixable:
            print(f"  ({len(fixable)} auto-fixable - run with --fix)")
        return len(diagnostics)
    except FileNotFoundError:
        print(f"Error: File not found: {source_file}")
        return 1
    except (OSError, UnicodeDecodeError) as e:
        print(f"Diagnostics error: {e}")
        return 1


def run_diagnostics_on_error(source_file: str) -> None:
    """Run diagnostics when compilation fails to provide helpful hints."""
    if not DIAGNOSTICS_AVAILABLE:
        return
    print("\n--- Running diagnostics for hints ---")
    try:
        diagnostics = analyze_file(source_file)
        if diagnostics:
            print()
            for d in diagnostics:
                print(d)
                print()
    except (OSError, ValueError, AttributeError):
        # Diagnostics-on-error is best-effort; if the diagnostics
        # machinery itself fails we silently skip the hint.
        pass


def run_diagnostics_json(source_file: str) -> dict:
    """Run all diagnostics and return structured JSON for editor integration.



    Also attempts to parse the file to catch parser errors that the

    diagnostics prepass doesn't cover (undefined variables, type errors, etc).



    Returns dict: {"file": str, "diagnostics": [{"line", "col", "end_col",

                   "message", "hint", "severity"}]}

    """
    results: list[dict] = []
    # 1. Token-level diagnostics (diagnostics.py)
    if DIAGNOSTICS_AVAILABLE:
        try:
            diags = analyze_file(source_file)
            for d in diags:
                entry: dict = {
                    "line": getattr(d, "line", 1),
                    "col": getattr(d, "column", 1),
                    "message": getattr(d, "message", str(d)),
                    "severity": getattr(d, "severity", "error"),
                }
                hint = getattr(d, "suggestion", None)
                if hint:
                    entry["hint"] = hint
                results.append(entry)
        except (OSError, ValueError, AttributeError):
            # Best-effort: if diagnostics blow up, the editor still
            # gets the AST-level + parser results below.
            pass
    # 2. AST-level static analysis (null flow, race detection)
    try:
        static_warnings = _run_ast_analysis(source_file)
        for sw in static_warnings:
            entry = {
                "line": getattr(sw, "line", 1),
                "col": getattr(sw, "column", 1),
                "message": getattr(sw, "message", str(sw)),
                "severity": getattr(sw, "severity", "warning"),
            }
            results.append(entry)
    except (OSError, ValueError, AttributeError):
        pass
    # 2.5. Effect/capability policy diagnostics
    try:
        for d in _collect_effect_policy_diagnostics(source_file):
            results.append(
                {
                    "line": getattr(d, "line", 1),
                    "col": getattr(d, "column", 1),
                    "message": getattr(d, "message", str(d)),
                    "severity": getattr(d, "severity", "error"),
                    "hint": getattr(d, "suggestion", None),
                }
            )
    except (OSError, ValueError, AttributeError):
        pass
    # 2.6. C include directive diagnostics
    try:
        for d in _collect_cinclude_diagnostics(source_file):
            entry = {
                "line": getattr(d, "line", 1),
                "col": getattr(d, "column", 1),
                "message": getattr(d, "message", str(d)),
                "severity": getattr(d, "severity", "warning"),
            }
            hint = getattr(d, "suggestion", None)
            if hint:
                entry["hint"] = hint
            results.append(entry)
    except (OSError, ValueError, AttributeError):
        pass
    # 3. Parser errors (catches syntax errors the prepass misses)
    try:
        _parse_program_ast(source_file, resolve_imports=True)
    except (OSError, ValueError, SyntaxError) as e:
        msg = str(e)
        # Try to extract line/col from error message: "Line N, Col M: ..."
        m = re.match(r"Line (\d+),?\s*Col (\d+):?\s*(.*)", msg)
        if m:
            results.append(
                {
                    "line": int(m.group(1)),
                    "col": int(m.group(2)),
                    "message": m.group(3).strip(),
                    "severity": "error",
                }
            )
        elif msg:
            results.append(
                {
                    "line": 1,
                    "col": 1,
                    "message": msg,
                    "severity": "error",
                }
            )
    return {"file": source_file, "diagnostics": results}


def run_static_analysis(filename: str, warnings_as_errors: bool = False) -> int:
    """Run static analysis on a source file."""
    print(f"=== Static Analysis: {filename} ===\n")
    try:
        program_ast = _parse_program_ast(filename, resolve_imports=True)
    except (ValueError, SyntaxError) as e:
        print(f"Parse error: {e}")
        return 1
    warnings = analyze_ast(program_ast)
    if not warnings:
        print("[OK] No issues found")
        print("  - No potential null dereferences detected")
        print("  - No shared variable conflicts in spawned functions")
        return 0
    null_warnings = [w for w in warnings if w.category == "null"]
    race_warnings = [
        w for w in warnings if w.category in ("write-write", "read-write", "shared")
    ]
    perf_warnings = [w for w in warnings if w.category == "perf"]
    if null_warnings:
        print(f"[NULL] Found {len(null_warnings)} potential null dereference(s):\n")
        for w in null_warnings:
            print(f"  {w}\n")
    if race_warnings:
        print(f"[RACE] Found {len(race_warnings)} data race(s):\n")
        for w in race_warnings:
            print(f"  {w}\n")
    if perf_warnings:
        print(f"[PERF] Found {len(perf_warnings)} performance issue(s):\n")
        for w in perf_warnings:
            print(f"  {w}\n")
    print(f"Total: {len(warnings)} warning(s)")
    # Race warnings are ALWAYS errors  -  unsynchronized shared access
    # is provably unsafe in concurrent code (Ada/SPARK enforcement model)
    if race_warnings:
        print(
            "\nData races are compile-time errors. "
            "Use atomic_load/atomic_store, mutex, or @synchronized."
        )
        return 1
    if warnings_as_errors:
        print("\n(-W flag: treating warnings as errors)")
        return 1
    return 0

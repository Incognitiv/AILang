"""
AILang Self-Hosting Analysis

This module analyzes what's needed to self-host AILang (compile AILang with AILang).
It examines each compiler component and reports what can/cannot be transpiled.

Usage:
    python -m tools.self_host_analysis
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class DependencyAnalysis:
    """Analysis of a Python file's dependencies."""

    file: str
    imports: List[str]  # All imports
    python_only: List[str]  # Python-specific (can't transpile)
    transpilable: List[str]  # Can be transpiled to AILang
    external_calls: List[str]  # Calls to external modules
    llvm_usage: List[str]  # LLVM-specific code
    feasibility: str  # "easy", "medium", "hard", "requires_ffi"


# Modules that are Python-specific and cannot be transpiled
PYTHON_ONLY_MODULES = {
    "typing",
    "__future__",
    "dataclasses",
    "abc",
    "functools",
    "contextlib",
    "collections",
    "enum",
    "ast",
    "re",
    "sys",
    "os",
    "pathlib",
}

# Modules that require FFI to work
FFI_REQUIRED_MODULES = {
    "ctypes",
    "cffi",
    "llvmlite",
    "llvmlite.ir",
    "llvmlite.binding",
}


def analyze_imports(source: str) -> tuple[List[str], List[str], List[str]]:
    """Analyze imports in Python source."""
    tree = ast.parse(source)
    all_imports: List[str] = []
    python_only: List[str] = []
    ffi_required: List[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                all_imports.append(alias.name)
                base = alias.name.split(".")[0]
                if base in PYTHON_ONLY_MODULES or alias.name in PYTHON_ONLY_MODULES:
                    python_only.append(alias.name)
                elif base in FFI_REQUIRED_MODULES or alias.name in FFI_REQUIRED_MODULES:
                    ffi_required.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            all_imports.append(module)
            base = module.split(".")[0]
            if base in PYTHON_ONLY_MODULES or module in PYTHON_ONLY_MODULES:
                python_only.append(module)
            elif base in FFI_REQUIRED_MODULES or module in FFI_REQUIRED_MODULES:
                ffi_required.append(module)

    return all_imports, python_only, ffi_required


def analyze_llvm_usage(source: str) -> List[str]:
    """Find LLVM-specific code patterns."""
    patterns: List[str] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # Look for ir.* or binding.* patterns
            if isinstance(node.value, ast.Name) and node.value.id in ("ir", "binding"):
                patterns.append(f"{node.value.id}.{node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("ir", "binding")
        ):
            patterns.append(f"{node.func.value.id}.{node.func.attr}()")

    return list(set(patterns))


def analyze_file(filepath: Path, base_dir: Path) -> DependencyAnalysis:
    """Analyze a single Python file for self-hosting feasibility."""
    source = filepath.read_text(encoding="utf-8")
    all_imports, python_only, ffi_required = analyze_imports(source)
    llvm_usage = analyze_llvm_usage(source)

    transpilable = [
        imp for imp in all_imports if imp not in python_only and imp not in ffi_required
    ]

    # Determine feasibility
    if ffi_required or llvm_usage:
        feasibility = "requires_ffi"
    elif python_only:
        feasibility = "medium" if len(python_only) <= 2 else "hard"
    else:
        feasibility = "easy"

    return DependencyAnalysis(
        file=filepath.relative_to(base_dir).as_posix(),
        imports=all_imports,
        python_only=python_only,
        transpilable=transpilable,
        external_calls=ffi_required,
        llvm_usage=llvm_usage[:10],  # Limit to first 10
        feasibility=feasibility,
    )


def analyze_ailang_codebase() -> Dict[str, DependencyAnalysis]:
    """Analyze the entire AILang codebase."""
    ailang_dir = Path(__file__).resolve().parents[1]
    results: Dict[str, DependencyAnalysis] = {}

    for py_file in sorted(ailang_dir.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        try:
            rel_path = py_file.relative_to(ailang_dir).as_posix()
            results[rel_path] = analyze_file(py_file, ailang_dir)
        except SyntaxError as e:
            print(f"Syntax error in {py_file.relative_to(ailang_dir).as_posix()}: {e}")

    return results


def print_report(results: Dict[str, DependencyAnalysis]) -> None:
    """Print a formatted report."""
    print("\n" + "=" * 70)
    print("AILANG SELF-HOSTING FEASIBILITY ANALYSIS")
    print("=" * 70)

    # Group by feasibility
    easy = [r for r in results.values() if r.feasibility == "easy"]
    medium = [r for r in results.values() if r.feasibility == "medium"]
    hard = [r for r in results.values() if r.feasibility == "hard"]
    ffi = [r for r in results.values() if r.feasibility == "requires_ffi"]

    print(f"\n[OK] EASY TO TRANSPILE ({len(easy)} files):")
    print("-" * 40)
    for r in easy:
        print(f"  {r.file}")

    print(f"\n[WARN] MEDIUM DIFFICULTY ({len(medium)} files):")
    print("-" * 40)
    for r in medium:
        print(f"  {r.file}")
        print(f"    Python-only: {', '.join(r.python_only)}")

    print(f"\n[HARD] HARD ({len(hard)} files):")
    print("-" * 40)
    for r in hard:
        print(f"  {r.file}")
        print(f"    Python-only: {', '.join(r.python_only[:5])}")

    print(f"\n[FFI] REQUIRES FFI ({len(ffi)} files):")
    print("-" * 40)
    for r in ffi:
        print(f"  {r.file}")
        if r.external_calls:
            print(f"    FFI modules: {', '.join(r.external_calls)}")
        if r.llvm_usage:
            print(f"    LLVM calls: {', '.join(r.llvm_usage[:5])}...")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total files: {len(results)}")
    print(f"  [OK] Easy:        {len(easy)}")
    print(f"  [WARN] Medium:      {len(medium)}")
    print(f"  [HARD] Hard:        {len(hard)}")
    print(f"  [FFI] Requires FFI: {len(ffi)}")

    # Self-hosting path
    print("\n" + "=" * 70)
    print("PATH TO SELF-HOSTING")
    print("=" * 70)
    print(
        """
Phase 1: Transpile "easy" files
  - These files have no Python-specific dependencies
  - Can be transpiled and run immediately

Phase 2: Handle "medium" files
  - Replace Python typing with AILang type syntax
  - Replace dataclasses with AILang records
  - Replace re module with AILang string operations

Phase 3: Handle "hard" files
  - Rewrite ast module usage with AILang AST
  - Rewrite pathlib with AILang file operations

Phase 4: FFI for LLVM (THE BIG ONE)
  - Option A: Generate LLVM IR as text (already works!)
  - Option B: Add FFI to AILang to call LLVM C API
  - Option C: Shell out to clang for final compilation

Recommended approach: Phase 4 Option A
  - AILang can already generate .ll files
  - Use system clang/llc to compile .ll to binary
  - This avoids needing runtime LLVM bindings
"""
    )


if __name__ == "__main__":
    results = analyze_ailang_codebase()
    print_report(results)

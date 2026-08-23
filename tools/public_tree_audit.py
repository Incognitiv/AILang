#!/usr/bin/env python3
"""Audit the public AILang tree for local/product leakage."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {
    ".ail",
    ".c",
    ".cmake",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".qml",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}

EXCLUDED_DIRS = {
    ".git",
    ".claude",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "archived",
    "build",
    "dist",
    "out",
}

EXCLUDED_PATH_PREFIXES = {
    "benchmarks/results",
    "benchmarks/sessions",
    "benchmarks/out",
    "source/out",
    "tools/out",
    "verifier/dist",
}

EXCLUDED_FILE_PATTERNS = (
    "benchmarks/benchmark_results.*",
    "benchmarks/benchmark_results_wsl.json",
    "benchmarks/ailang/*.c",
    "benchmarks/ailang/*.ll",
    "benchmarks/ailang/*.o",
    "benchmarks/ailang/*_opt.ll",
    "source/CODE_ANALYSIS.md",
    "tests/corpus/*.c",
    "tools/regression_baseline*.json",
    "wsl_tools_*",
)

LINE_LIMIT_EXCLUDED_PATH_PREFIXES = {
    "examples/ui/backends/generated",
    "source/ui/generated",
}

LINE_LIMIT_EXCLUDED_FILE_PATTERNS = (
    "*.cbind.json",
    "*.cbind.probe.json",
    "*.cbind.bindings.c",
    # Platform UI backends are intentionally kept as single ABI units until
    # the UI import/split contract is stable enough to refactor safely.
    "examples/ui/backends/ail_ui_win32_min.c",
    "examples/ui/backends/win32_pure.ail",
    "source/ui/wayland_pure.ail",
    "source/ui/win32_pure.ail",
    "stdlib/ui/paint.ail",
    # Benchmark orchestration is an internal release tool, not public API.
    "benchmarks/run_benchmarks.py",
    # Runtime helper emitters are generated C fragments grouped by contract;
    # splitting them is a separate compiler-maintenance task.
    "source/transpiler/runtime_emit_safety.py",
    "tests/corpus/*",
)

LOCAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s`\"']+|/home/[^/\s`\"']+|/mnt/[a-z]/Users/[^/\s`\"']+)",
    re.IGNORECASE,
)

PRODUCT_TERMS = ("Mixtar", "MixtarRVS", "MDDM")
MAX_PUBLIC_LINES = 750

ALLOWED_PRODUCT_HITS = {
    "README.md",
    "tools/public_tree_audit.py",
}

ALLOWED_LOCAL_PATH_HITS = {
    "tools/public_tree_audit.py",
}


def norm(path: Path) -> str:
    return str(path).replace("\\", "/")


def is_excluded(path: Path) -> bool:
    rel = norm(path.relative_to(REPO_ROOT))
    if any(part in EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts):
        return True
    if any(rel == prefix or rel.startswith(prefix + "/") for prefix in EXCLUDED_PATH_PREFIXES):
        return True
    return any(path.match(pattern) or rel == pattern for pattern in EXCLUDED_FILE_PATTERNS)


def is_line_limit_excluded(path: Path) -> bool:
    rel = norm(path.relative_to(REPO_ROOT))
    if any(rel == prefix or rel.startswith(prefix + "/") for prefix in LINE_LIMIT_EXCLUDED_PATH_PREFIXES):
        return True
    return any(path.match(pattern) or rel == pattern for pattern in LINE_LIMIT_EXCLUDED_FILE_PATTERNS)


def iter_text_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if is_excluded(path):
            continue
        files.append(path)
    files.sort(key=lambda p: norm(p.relative_to(REPO_ROOT)).lower())
    return files


def audit_root_docs() -> list[str]:
    errors: list[str] = []
    for path in REPO_ROOT.iterdir():
        if path.is_file() and path.suffix.lower() == ".md" and path.name != "README.md":
            errors.append(f"root-doc:{path.name}")
    return errors


def audit_content(*, enforce_line_limit: bool) -> list[str]:
    errors: list[str] = []
    for path in iter_text_files():
        rel = norm(path.relative_to(REPO_ROOT))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if enforce_line_limit and not is_line_limit_excluded(path) and len(lines) > MAX_PUBLIC_LINES:
            errors.append(f"line-limit:{rel}:{len(lines)}")
        for lineno, line in enumerate(lines, 1):
            if rel not in ALLOWED_LOCAL_PATH_HITS and LOCAL_PATH_RE.search(line):
                errors.append(f"local-path:{rel}:{lineno}")
            if rel not in ALLOWED_PRODUCT_HITS:
                for term in PRODUCT_TERMS:
                    if term in line:
                        errors.append(f"product-term:{term}:{rel}:{lineno}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--enforce-line-limit", action="store_true")
    args = parser.parse_args()

    errors = audit_root_docs() + audit_content(
        enforce_line_limit=bool(args.enforce_line_limit)
    )
    if args.verbose:
        print(f"scanned_files={len(iter_text_files())}")
    if errors:
        print("public tree audit failed:")
        for err in errors:
            print(err)
        return 1
    print("public tree audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Generate CODEMAP.md - a quick navigation index of every Python file in source/
plus the top-level ailang.py launcher.

For each file: lists top-level classes (with base classes), their methods, and
top-level functions, each with a line number. Lets us look up "where is X
defined" without grepping a 10K-line file.

Usage:
    python tools/codemap.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
LAUNCHER = ROOT / "ailang.py"
OUTPUT = ROOT / "CODEMAP.md"

EXCLUDE_DIRS = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def collect_files() -> list[Path]:
    """Find all .py files we want indexed (source/ tree + top-level launcher)."""
    files: list[Path] = []
    if LAUNCHER.exists():
        files.append(LAUNCHER)
    for p in SOURCE.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        files.append(p)
    return files


def base_name(node: ast.expr) -> str:
    """Return a short string for a base-class expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{base_name(node.value)}.{node.attr}"
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return "?"


def is_substantive(node: ast.AST) -> bool:
    """True for nodes we want in the index. Skip module docstrings."""
    return isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))


def render_file(path: Path, rel: Path) -> list[str]:
    """Return CODEMAP markdown lines for one file."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"### {rel.as_posix()}", "", "  *encoding error - could not parse*", ""]
    line_count = text.count("\n") + 1
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [
            f"### {rel.as_posix()}  ({line_count} lines)",
            "",
            f"  *syntax error: {exc.msg} at line {exc.lineno}*",
            "",
        ]

    items = [n for n in tree.body if is_substantive(n)]
    if not items:
        # Skip empty / docstring-only files (likely __init__.py)
        return []

    out = [f"### {rel.as_posix()}  ({line_count} lines)", ""]
    for node in items:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(base_name(b) for b in node.bases)
            suffix = f" : {bases}" if bases else ""
            out.append(f"- `class {node.name}{suffix}` - line {node.lineno}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decorators = ""
                    if any(
                        isinstance(d, ast.Name) and d.id == "staticmethod"
                        for d in item.decorator_list
                    ):
                        decorators = " *(static)*"
                    elif any(
                        isinstance(d, ast.Name) and d.id == "classmethod"
                        for d in item.decorator_list
                    ):
                        decorators = " *(classmethod)*"
                    out.append(f"  - `{item.name}`{decorators} - line {item.lineno}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(f"- `def {node.name}` - line {node.lineno}")
    out.append("")
    return out


def main() -> int:
    files = collect_files()
    files_by_package: dict[str, list[Path]] = {}
    for f in files:
        rel = f.relative_to(ROOT)
        if rel == Path("ailang.py"):
            pkg = "(launcher)"
        elif len(rel.parts) >= 3:
            pkg = rel.parts[1]  # source/<pkg>/file.py
        else:
            pkg = "(source root)"
        files_by_package.setdefault(pkg, []).append(f)

    lines: list[str] = [
        "# AILang Code Map",
        "",
        f"Auto-generated index of {len(files)} Python files. ",
        "Run `python tools/codemap.py` to regenerate.",
        "",
        "Lists top-level classes (with their methods) and free functions, ",
        "each with a line number. Use this to jump to any symbol without ",
        "grepping the larger files.",
        "",
        "## Packages",
        "",
    ]

    package_order = sorted(files_by_package.keys())
    for pkg in package_order:
        pkg_files = sorted(files_by_package[pkg])
        # Package header with file count and total line count
        total_lines = 0
        for f in pkg_files:
            try:
                total_lines += f.read_text(encoding="utf-8").count("\n") + 1
            except UnicodeDecodeError:
                pass
        lines.append(f"- **`{pkg}`** - {len(pkg_files)} file(s), {total_lines:,} lines")
    lines.append("")

    for pkg in package_order:
        lines.append(f"## {pkg}")
        lines.append("")
        for f in sorted(files_by_package[pkg]):
            lines.extend(render_file(f, f.relative_to(ROOT)))

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    size = OUTPUT.stat().st_size
    print(
        f"wrote {OUTPUT.relative_to(ROOT)} ({size:,} bytes, {len(files)} files indexed)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

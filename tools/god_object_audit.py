#!/usr/bin/env python3
"""
God-object audit for the active Python compiler tree.

The audit is heuristic and intentionally conservative. A file is marked as a
"god-object candidate" when one or more thresholds are exceeded.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = [REPO_ROOT / "source", REPO_ROOT / "ailang.py"]


@dataclass
class FileMetrics:
    path: str
    line_count: int
    class_count: int
    top_level_function_count: int
    total_method_count: int
    max_methods_in_class: int
    max_method_class_name: str | None
    total_defs: int
    parse_ok: bool
    parse_error: str | None
    god_object_candidate: bool
    reasons: list[str]


def _iter_py_files(targets: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            out.append(target)
            continue
        if target.is_dir():
            out.extend(sorted(target.rglob("*.py")))
    return sorted(set(out))


def _count_class_methods(node: ast.ClassDef) -> int:
    count = 0
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            count += 1
    return count


def _collect_metrics(
    path: Path,
    *,
    max_file_lines: int,
    max_class_methods: int,
    max_total_defs: int,
) -> FileMetrics:
    text = path.read_text(encoding="utf-8")
    line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
    class_count = 0
    top_level_function_count = 0
    total_method_count = 0
    max_methods_in_class = 0
    max_method_class_name: str | None = None
    parse_ok = True
    parse_error: str | None = None

    try:
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_count += 1
                methods = _count_class_methods(node)
                total_method_count += methods
                if methods > max_methods_in_class:
                    max_methods_in_class = methods
                    max_method_class_name = node.name
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top_level_function_count += 1
    except SyntaxError as exc:
        parse_ok = False
        parse_error = f"{exc.msg} (line {exc.lineno})"

    total_defs = top_level_function_count + total_method_count
    reasons: list[str] = []

    if line_count > max_file_lines:
        reasons.append(f"line_count>{max_file_lines} ({line_count})")
    if max_methods_in_class > max_class_methods:
        cls_name = max_method_class_name or "<unknown>"
        reasons.append(
            f"class_methods>{max_class_methods} ({cls_name}:{max_methods_in_class})"
        )
    if total_defs > max_total_defs:
        reasons.append(f"total_defs>{max_total_defs} ({total_defs})")
    if not parse_ok:
        reasons.append(f"parse_error ({parse_error})")

    return FileMetrics(
        path=str(path),
        line_count=line_count,
        class_count=class_count,
        top_level_function_count=top_level_function_count,
        total_method_count=total_method_count,
        max_methods_in_class=max_methods_in_class,
        max_method_class_name=max_method_class_name,
        total_defs=total_defs,
        parse_ok=parse_ok,
        parse_error=parse_error,
        god_object_candidate=bool(reasons),
        reasons=reasons,
    )


def _severity_key(row: FileMetrics) -> tuple[int, int, int]:
    return (
        row.line_count,
        row.max_methods_in_class,
        row.total_defs,
    )


def _render_markdown(
    rows: list[FileMetrics],
    *,
    max_file_lines: int,
    max_class_methods: int,
    max_total_defs: int,
) -> str:
    candidates = [r for r in rows if r.god_object_candidate]
    candidates_sorted = sorted(candidates, key=_severity_key, reverse=True)
    lines: list[str] = []
    lines.append("# God-Object Audit")
    lines.append("")
    lines.append(
        f"- thresholds: file_lines>{max_file_lines}, class_methods>{max_class_methods}, total_defs>{max_total_defs}"
    )
    lines.append(f"- scanned files: {len(rows)}")
    lines.append(f"- candidates: {len(candidates_sorted)}")
    lines.append("")
    lines.append(
        "| path | lines | classes | top funcs | methods | max class methods | total defs | reasons |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |")
    for row in candidates_sorted:
        max_cls = (
            f"{row.max_method_class_name}:{row.max_methods_in_class}"
            if row.max_method_class_name
            else str(row.max_methods_in_class)
        )
        lines.append(
            f"| {row.path} | {row.line_count} | {row.class_count} | {row.top_level_function_count} | "
            f"{row.total_method_count} | {max_cls} | {row.total_defs} | {'; '.join(row.reasons)} |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="God-object audit for Python files")
    p.add_argument(
        "targets",
        nargs="*",
        default=[str(p) for p in DEFAULT_TARGETS],
        help="files/directories to scan (default: source/ and ailang.py)",
    )
    p.add_argument(
        "--max-file-lines",
        type=int,
        default=750,
        help="line count threshold",
    )
    p.add_argument(
        "--max-class-methods",
        type=int,
        default=40,
        help="max methods threshold for a single class",
    )
    p.add_argument(
        "--max-total-defs",
        type=int,
        default=120,
        help="max defs threshold per file",
    )
    p.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="optional JSON output path",
    )
    p.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="optional markdown output path",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    targets = [Path(t).resolve() for t in args.targets]
    files = _iter_py_files(targets)

    rows = [
        _collect_metrics(
            path,
            max_file_lines=args.max_file_lines,
            max_class_methods=args.max_class_methods,
            max_total_defs=args.max_total_defs,
        )
        for path in files
    ]
    candidates = [r for r in rows if r.god_object_candidate]

    payload = {
        "thresholds": {
            "max_file_lines": args.max_file_lines,
            "max_class_methods": args.max_class_methods,
            "max_total_defs": args.max_total_defs,
        },
        "scanned_files": len(rows),
        "candidate_count": len(candidates),
        "candidates": [
            asdict(r) for r in sorted(candidates, key=_severity_key, reverse=True)
        ],
        "all_files": [asdict(r) for r in rows],
    }

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md = _render_markdown(
        rows,
        max_file_lines=args.max_file_lines,
        max_class_methods=args.max_class_methods,
        max_total_defs=args.max_total_defs,
    )
    if args.md_output:
        args.md_output.parent.mkdir(parents=True, exist_ok=True)
        args.md_output.write_text(md + "\n", encoding="utf-8")

    print(f"scanned files: {len(rows)}")
    print(f"god-object candidates: {len(candidates)}")
    top = sorted(candidates, key=_severity_key, reverse=True)[:10]
    for row in top:
        print(f"- {row.path} :: {', '.join(row.reasons)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create a cleaned source zip without caches/build artifacts."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import stat
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
}

DEFAULT_EXCLUDED_DIR_PATHS = {
    "out",
    "benchmarks/out",
    "benchmarks/results",
    "source/out",
    "tools/out",
    "verifier/dist",
    "benchmarks/sessions",
}

DEFAULT_EXCLUDED_FILE_PATHS = {
    "benchmarks/benchmark_results.json",
    "benchmarks/benchmark_results.md",
    "source/CODE_ANALYSIS.md",
    "test.db",
    "verifier/verifier_linux.spec",
    "verifier/verifier_output.txt",
    "verifier/verifier_windows.spec",
}

DEFAULT_EXCLUDED_FILE_GLOBS = {
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.py[cod]",
    "*$py.class",
    "*.egg-info",
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.o",
    "*.obj",
    "*.pdb",
    "*.ll",
    "*_opt.ll",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.sqlite",
    "*.profraw",
    "*.profdata",
    "*.log",
    "*.tmp",
    "tmp_*",
    "benchmarks/tmp_*.txt",
    "*.zip",
    "benchmarks/ailang/*.c",
    "tests/corpus/*.c",
}


@dataclass
class PackageStats:
    file_count: int = 0
    total_bytes: int = 0


def _norm_rel(path: Path) -> str:
    raw = str(path).replace("\\", "/").strip("/")
    return "" if raw == "." else raw


def _is_subpath(rel_lower: str, path_lower: str) -> bool:
    return rel_lower == path_lower or rel_lower.startswith(path_lower + "/")


def _should_exclude_dir(
    rel_dir_lower: str,
    dir_name: str,
    *,
    excluded_dir_names: set[str],
    excluded_dir_paths: set[str],
) -> bool:
    dir_name_lower = dir_name.lower()
    if dir_name_lower in excluded_dir_names:
        return True
    if fnmatch.fnmatch(dir_name_lower, "*.egg-info"):
        return True
    if dir_name_lower == "venv" or dir_name_lower.startswith("venv_"):
        return True
    if dir_name_lower == ".venv" or dir_name_lower.startswith(".venv"):
        return True
    child = dir_name if not rel_dir_lower else f"{rel_dir_lower}/{dir_name}"
    child_lower = child.lower()
    return any(_is_subpath(child_lower, p) for p in excluded_dir_paths)


def _matches_any_glob(rel_path: str, globs: Iterable[str]) -> bool:
    name = Path(rel_path).name
    for pattern in globs:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(name, pattern):
            return True
    return False


def _should_exclude_file(
    rel_path: str,
    *,
    excluded_file_paths: set[str],
    excluded_file_globs: set[str],
) -> bool:
    rel_lower = rel_path.lower()
    if rel_lower in excluded_file_paths:
        return True
    return _matches_any_glob(rel_path, excluded_file_globs)


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _collect_files(
    source_root: Path,
    output_zip: Path,
    *,
    excluded_dir_names: set[str],
    excluded_dir_paths: set[str],
    excluded_file_paths: set[str],
    excluded_file_globs: set[str],
) -> list[Path]:
    files: list[Path] = []
    out_resolved = _safe_resolve(output_zip)
    for root, dirnames, filenames in os.walk(source_root):
        root_path = Path(root)
        rel_dir = _norm_rel(root_path.relative_to(source_root))
        rel_dir_lower = rel_dir.lower()
        dirnames[:] = [
            d
            for d in dirnames
            if not _should_exclude_dir(
                rel_dir_lower,
                d,
                excluded_dir_names=excluded_dir_names,
                excluded_dir_paths=excluded_dir_paths,
            )
        ]
        for filename in filenames:
            file_path = root_path / filename
            file_resolved = _safe_resolve(file_path)
            if out_resolved is not None and file_resolved == out_resolved:
                continue
            rel_file = _norm_rel(file_path.relative_to(source_root))
            if _should_exclude_file(
                rel_file,
                excluded_file_paths=excluded_file_paths,
                excluded_file_globs=excluded_file_globs,
            ):
                continue
            if _safe_exists(file_path):
                files.append(file_path)
    files.sort(key=lambda p: _norm_rel(p.relative_to(source_root)).lower())
    return files


def _collect_includes(source_root: Path, include_paths: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for raw in include_paths:
        rel = raw.strip().replace("\\", "/").strip("/")
        if not rel:
            continue
        target = (source_root / rel).resolve()
        if not target.exists():
            print(f"warning: include path not found: {raw}")
            continue
        if target.is_file():
            if target not in seen:
                files.append(target)
                seen.add(target)
            continue
        for p in target.rglob("*"):
            if p.is_file() and p not in seen:
                files.append(p)
                seen.add(p)
    files.sort(key=lambda p: _norm_rel(p.relative_to(source_root)).lower())
    return files


def _looks_executable(rel_path: str, st_mode: int) -> bool:
    if st_mode & stat.S_IXUSR:
        return True
    name = Path(rel_path).name.lower()
    return name in {"ailangc", "ailang.bin", "ailangc.exe", "main.bin"}


def _human_size(n_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    size = float(n_bytes)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.2f} {units[idx]}"


def _write_zip(source_root: Path, output_zip: Path, files: list[Path]) -> PackageStats:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    stats = PackageStats()
    with zipfile.ZipFile(
        output_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        for file_path in files:
            rel = _norm_rel(file_path.relative_to(source_root))
            try:
                st = file_path.stat()
                mode = stat.S_IFREG | (stat.S_IMODE(st.st_mode) or 0o644)
                if _looks_executable(rel, st.st_mode):
                    mode = stat.S_IFREG | 0o755
                zi = zipfile.ZipInfo(rel)
                zi.create_system = 3  # unix
                zi.external_attr = mode << 16
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.date_time = time.localtime(st.st_mtime)[:6]
                data = file_path.read_bytes()
                zf.writestr(
                    zi, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
                )
                stats.file_count += 1
                stats.total_bytes += st.st_size
            except FileNotFoundError:
                # Temporary files from external tools can disappear between scan/write.
                continue
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO_ROOT,
        help="Source repository root (default: current repo root).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path (default: out/releases/AILang-Pure-clean-<timestamp>.zip).",
    )
    parser.add_argument(
        "--exclude-archived",
        action="store_true",
        help="Exclude archived/ from the package.",
    )
    parser.add_argument(
        "--extra-exclude",
        action="append",
        default=[],
        help="Additional file glob to exclude (repeatable).",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=[],
        help=(
            "Additional file/dir path relative to source root to force-include, "
            "even when excluded by default filters (repeatable)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write zip; only show summary.",
    )
    parser.add_argument(
        "--manifest-json",
        type=Path,
        default=None,
        help="Optional JSON manifest output path.",
    )
    parser.add_argument(
        "--show-first",
        type=int,
        default=25,
        help="How many first entries to print in dry-run mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source.resolve()
    if not source_root.exists() or not source_root.is_dir():
        print(f"error: source root not found or not a directory: {source_root}")
        return 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_zip = (
        args.output.resolve()
        if args.output is not None
        else (
            source_root / "out" / "releases" / f"AILang-Pure-clean-{ts}.zip"
        ).resolve()
    )

    excluded_dir_names = {n.lower() for n in DEFAULT_EXCLUDED_DIR_NAMES}
    excluded_dir_paths = {p.lower() for p in DEFAULT_EXCLUDED_DIR_PATHS}
    if args.exclude_archived:
        excluded_dir_paths.add("archived")
    excluded_file_paths = {p.lower() for p in DEFAULT_EXCLUDED_FILE_PATHS}
    excluded_file_globs = set(DEFAULT_EXCLUDED_FILE_GLOBS)
    excluded_file_globs.update(args.extra_exclude)

    files = _collect_files(
        source_root,
        output_zip,
        excluded_dir_names=excluded_dir_names,
        excluded_dir_paths=excluded_dir_paths,
        excluded_file_paths=excluded_file_paths,
        excluded_file_globs=excluded_file_globs,
    )
    include_files = _collect_includes(source_root, args.include_path)
    if include_files:
        existing = {p.resolve() for p in files}
        for p in include_files:
            rp = p.resolve()
            if rp not in existing:
                files.append(p)
                existing.add(rp)
        files.sort(key=lambda p: _norm_rel(p.relative_to(source_root)).lower())

    total_bytes = 0
    for path in files:
        try:
            total_bytes += path.stat().st_size
        except FileNotFoundError:
            continue
    print(f"source: {source_root}")
    print(f"output: {output_zip}")
    print(f"files: {len(files)}")
    print(f"uncompressed size: {_human_size(total_bytes)}")
    print(f"exclude archived: {args.exclude_archived}")

    if args.dry_run:
        limit = max(0, int(args.show_first))
        if limit > 0:
            print(f"first {min(limit, len(files))} entries:")
            for path in files[:limit]:
                print("  " + _norm_rel(path.relative_to(source_root)))
        if args.manifest_json is not None:
            payload = {
                "source": str(source_root),
                "output": str(output_zip),
                "exclude_archived": bool(args.exclude_archived),
                "file_count": len(files),
                "uncompressed_bytes": total_bytes,
                "files": [_norm_rel(p.relative_to(source_root)) for p in files],
            }
            args.manifest_json.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_json.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            print(f"manifest json: {args.manifest_json.resolve()}")
        return 0

    stats = _write_zip(source_root, output_zip, files)
    zip_size = output_zip.stat().st_size
    print(f"zip created: {output_zip}")
    print(f"packaged files: {stats.file_count}")
    print(f"input size: {_human_size(stats.total_bytes)}")
    print(f"zip size: {_human_size(zip_size)}")

    if args.manifest_json is not None:
        payload = {
            "source": str(source_root),
            "output": str(output_zip),
            "exclude_archived": bool(args.exclude_archived),
            "file_count": stats.file_count,
            "uncompressed_bytes": stats.total_bytes,
            "zip_bytes": zip_size,
            "files": [_norm_rel(p.relative_to(source_root)) for p in files],
        }
        args.manifest_json.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"manifest json: {args.manifest_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line interface for the Python code verifier."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "verifier"

from .cache import VerificationCache
from .core import EnhancedPythonVerifier
from .tools import run_project_clone_audit
from .verifier_report import print_report


def _refresh_codemap_if_stale(directory: Path) -> None:
    """Regenerate CODEMAP.md if any tracked source file is newer than it.

    Tied to verifier directory sweeps so the codemap stays in sync without
    a separate manual step. Best-effort: failures are swallowed because a
    stale codemap should never block verification.
    """
    try:
        # verifier/cli.py -> repo root is two parents up.
        repo_root = Path(__file__).resolve().parent.parent
        codemap_md = repo_root / "CODEMAP.md"
        codemap_script = repo_root / "tools" / "codemap.py"
        source_dir = repo_root / "source"
        if not codemap_script.exists() or not source_dir.exists():
            return

        # Only auto-refresh when the verifier sweep is actually inside the
        # tree the codemap covers. Avoids triggering refreshes from sweeps
        # over unrelated directories (e.g. verifier/ itself).
        try:
            directory.resolve().relative_to(source_dir)
        except ValueError:
            if directory.resolve() != source_dir:
                return

        if codemap_md.exists():
            cm_mtime = codemap_md.stat().st_mtime
            stale = any(
                p.stat().st_mtime > cm_mtime
                for p in source_dir.rglob("*.py")
                if "__pycache__" not in p.parts
            )
            if not stale:
                return

        subprocess.run(
            [sys.executable, str(codemap_script)],
            cwd=repo_root,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


# Module-level verifier cache for worker processes. Each worker process holds
# one verifier instance for its lifetime so the dependency probe (~hundreds of
# ms) only happens once per worker, not once per file.
_WORKER_VERIFIER: EnhancedPythonVerifier | None = None
_WORKER_CACHE: VerificationCache | None = None


def _worker_verify(
    file_path: str, preset: str, check_imports: bool, use_cache: bool
) -> dict:
    """Worker-process entry point. Constructs a verifier on first call, reuses it after.

    The cache is a directory of JSON files keyed by file-content hash; safe
    to share across worker processes (concurrent reads are fine; concurrent
    writes are idempotent since the key is the content hash). Each worker
    creates its own cache handle on first use.
    """
    global _WORKER_VERIFIER, _WORKER_CACHE
    if _WORKER_VERIFIER is None:
        _WORKER_VERIFIER = EnhancedPythonVerifier()
    if use_cache and _WORKER_CACHE is None:
        _WORKER_CACHE = VerificationCache()
    try:
        result = _WORKER_VERIFIER.verify_file(
            Path(file_path),
            preset=preset,
            result_cache=_WORKER_CACHE,
            check_imports=check_imports,
        )
        result["file"] = file_path
        return result
    except (OSError, ValueError, KeyError) as exc:
        return {"file": file_path, "error": str(exc), "passed": False}


EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "verifier_portable",
    "verifier_portable_linux",
    "verifier.build",
    "verifier.dist",
    # PyInstaller hook files are required to be named `hook-<modname>.py`
    # with a literal hyphen (third-party convention). That naming rule
    # collides with pylint's invalid-name check, so the hook would need
    # an inline `# pylint: disable` -- which our suppression policy bans.
    # Exclude the directory entirely; it's third-party scaffolding, not
    # application code that should pass our standards.
    "pyinstaller_hooks",
}

# Subdirectories that are project scaffolding rather than core source code:
# wrappers around C templates, generated bindings, or platform glue. Skipped
# by default; pass --include-extras to include them in a directory sweep.
EXTRAS_DIRS = {"ui", "sdl"}


def create_parser() -> argparse.ArgumentParser:
    """Create and return the argument parser."""
    parser = argparse.ArgumentParser(
        description="Verify Python code quality using multiple tools"
    )
    parser.add_argument("file", type=Path, nargs="?", help="Python file to verify")
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        help="Verify all Python files in directory (recursive)",
    )
    parser.add_argument(
        "--preset",
        choices=["strict", "fast"],
        default="strict",
        help=(
            "Verification preset. 'strict' (default) penalizes formatter "
            "failures; 'fast' skips those checks for a quicker informal pass."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of human-readable report",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Always exit with code 0, even on verification failure",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable result caching (default: off)",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear all cached results and exit",
    )
    parser.add_argument(
        "--check-imports",
        action="store_true",
        help="Enable import checking (catches hallucinated imports)",
    )
    parser.add_argument(
        "--include-extras",
        action="store_true",
        help=(
            "In directory mode, also verify scaffolding subdirectories "
            f"({', '.join(sorted(EXTRAS_DIRS))}). Skipped by default."
        ),
    )
    parser.add_argument(
        "--fail-on-debt",
        action="store_true",
        help=(
            "Fail when informational debt audits find positional access or "
            "copy-paste clones. Strict still reports these by default."
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=0,
        help=(
            "Parallel files in directory mode. 0 = auto (cpu_count, capped at 8). "
            "Set to 1 to force serial."
        ),
    )
    return parser


def _short_path(file_path: str, root: Path) -> str:
    """Render `file_path` relative to `root` if possible, else basename."""
    try:
        return str(Path(file_path).resolve().relative_to(root.resolve()))
    except ValueError:
        return Path(file_path).name


def _fail_reasons_summary(reasons: list[str], max_reasons: int = 3) -> str:
    """Compact one-line summary of fail reasons, dropping the redundant score one.

    The score reason ("score 91/100 (must be 100)") is always present when any
    other reason is — it's the aggregate restatement, not extra signal. Hide it
    so the per-tool reasons are visible without scrolling.
    """
    detail = [r for r in reasons if not r.startswith("score ")]
    if not detail:
        return ""
    head = detail[:max_reasons]
    suffix = (
        f" (+{len(detail) - max_reasons} more)" if len(detail) > max_reasons else ""
    )
    return "; ".join(head) + suffix


def _resolve_jobs(requested: int, file_count: int) -> int:
    """Decide how many files to verify in parallel.

    `requested == 0` means auto: cpu_count capped at 8 (above that the OS
    scheduler thrashes spawning subprocess-heavy tools). Always at least 1,
    never more than the number of files we have.
    """
    if requested > 0:
        return max(1, min(requested, file_count))
    auto = min(os.cpu_count() or 1, 8)
    return max(1, min(auto, file_count))


def _verify_one_serial(
    verifier: EnhancedPythonVerifier,
    py_file: Path,
    preset: str,
    cache: VerificationCache | None,
    check_imports: bool,
) -> dict:
    """Run the full tool battery on a single file. Used by the serial path."""
    try:
        result = verifier.verify_file(
            py_file,
            preset=preset,
            result_cache=cache,
            check_imports=check_imports,
        )
        result["file"] = str(py_file)
        return result
    except (OSError, ValueError, KeyError) as exc:
        return {"file": str(py_file), "error": str(exc), "passed": False}


def _emit_result(py_file: Path, result: dict, directory: Path, json_mode: bool) -> None:
    """Print one [PASS]/[FAIL]/[ERROR] line for a completed verification."""
    if json_mode:
        return
    rel = _short_path(str(py_file), directory)
    if "error" in result and "overall_score" not in result:
        print(f"[ERROR] {rel}: {result['error']}", flush=True)
        return
    status = "[PASS]" if result.get("passed") else "[FAIL]"
    score = result.get("overall_score", 0)
    why = _fail_reasons_summary(result.get("fail_reasons", []))
    tail = f" -- {why}" if why else ""
    print(f"{status} ({score:.0f}/100) - {rel}{tail}", flush=True)


def verify_directory(
    verifier: EnhancedPythonVerifier,
    directory: Path,
    preset: str,
    cache: VerificationCache | None,
    check_imports: bool,
    json_mode: bool,
    include_extras: bool = False,
    jobs: int = 0,
) -> list:
    """Verify all Python files in a directory.

    Parallelism uses ProcessPoolExecutor rather than threads. The verifier's
    inner per-tool pool is already a ThreadPool, and stacking another thread
    pool on top serializes badly (subprocess.Popen on Windows + shared stdout
    redirection inside some tool runners). Process-level isolation sidesteps
    both. Cost: each worker pays one verifier-init (~hundreds of ms) on its
    first job; that's amortized across the files it handles.
    """
    excludes = set(EXCLUDE_DIRS)
    if not include_extras:
        excludes |= EXTRAS_DIRS
    python_files = [
        f
        for f in directory.rglob("*.py")
        if not any(excluded in f.parts for excluded in excludes)
    ]
    if not python_files:
        print(f"No Python files found in {directory}")
        return []

    worker_count = _resolve_jobs(jobs, len(python_files))

    if not json_mode:
        print(
            f"Found {len(python_files)} Python file(s) to verify "
            f"(parallel: {worker_count})"
        )
        if not include_extras:
            print(
                f"Skipping extras: {', '.join(sorted(EXTRAS_DIRS))} "
                "(use --include-extras to include)"
            )
        print(f"Available tools: {verifier.available_tools}")
        print()

    results_list: list = []

    if worker_count == 1:
        for py_file in python_files:
            if not json_mode:
                print(f"[RUN] {_short_path(str(py_file), directory)}", flush=True)
            result = _verify_one_serial(verifier, py_file, preset, cache, check_imports)
            results_list.append(result)
            _emit_result(py_file, result, directory, json_mode)
        return results_list

    # Cache propagates to workers via a flag — each worker constructs its own
    # cache handle. The cache is a directory of JSON files keyed by content
    # hash, safe across processes (reads concurrent, writes idempotent).
    use_cache = cache is not None
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(_worker_verify, str(f), preset, check_imports, use_cache): f
            for f in python_files
        }
        for fut in as_completed(futures):
            py_file = futures[fut]
            try:
                result = fut.result()
            except (OSError, ValueError, KeyError) as exc:
                result = {"file": str(py_file), "error": str(exc), "passed": False}
            results_list.append(result)
            _emit_result(py_file, result, directory, json_mode)
    return results_list


def _print_project_clone_audit(project_clone_audit: dict | None) -> None:
    """Print project-level clone audit details."""
    if not project_clone_audit:
        return
    count = project_clone_audit.get("clone_count", 0)
    if count == 0:
        print("[PROJECT_CLONES] [OK] No cross-file structural clones found")
        return
    print(f"[PROJECT_CLONES] (~) {count} cross-file structural clone pattern(s)")
    for issue in project_clone_audit.get("issues", [])[:10]:
        print(f"  - {issue}")


def _debt_summary(results_list: list, project_clone_audit: dict | None = None) -> dict:
    """Aggregate informational debt audits from file and project results."""
    positional_access = 0
    per_file_clones = 0
    for result in results_list:
        positional = result.get("positional_access") or {}
        clone = result.get("clone") or {}
        if isinstance(positional, dict):
            positional_access += positional.get("positional_access_count", 0)
        if isinstance(clone, dict):
            per_file_clones += clone.get("clone_count", 0)

    project_clones = 0
    if project_clone_audit:
        project_clones = project_clone_audit.get("clone_count", 0)

    total = positional_access + per_file_clones + project_clones
    return {
        "positional_access": positional_access,
        "per_file_clones": per_file_clones,
        "project_clones": project_clones,
        "total": total,
        "clean": total == 0,
    }


def _print_debt_summary(debt: dict) -> None:
    """Print the explicit technical-debt status."""
    if debt.get("clean", False):
        print("[DEBT] [OK] No positional-access or clone debt detected")
        return
    print(
        "[DEBT] (~) "
        f"{debt.get('total', 0)} audit debt item(s): "
        f"{debt.get('positional_access', 0)} positional access, "
        f"{debt.get('per_file_clones', 0)} per-file clone windows, "
        f"{debt.get('project_clones', 0)} cross-file clone windows"
    )


def print_directory_summary(
    results_list: list, json_mode: bool, project_clone_audit: dict | None = None
) -> int:
    """Print summary and return count of failed files."""
    passed_count = sum(1 for r in results_list if r.get("passed", False))
    failed_count = len(results_list) - passed_count
    debt = _debt_summary(results_list, project_clone_audit)

    if json_mode:
        print(
            json.dumps(
                {
                    "total": len(results_list),
                    "passed": passed_count,
                    "failed": failed_count,
                    "project_clone_audit": project_clone_audit,
                    "debt": debt,
                    "results": results_list,
                },
                indent=2,
            )
        )
    else:
        print()
        print("=" * 70)
        print(f"SUMMARY: {passed_count}/{len(results_list)} files passed")
        print("=" * 70)
        if failed_count > 0:
            print("\nFailed files:")
            for r in results_list:
                if r.get("passed", False):
                    continue
                why = _fail_reasons_summary(r.get("fail_reasons", []))
                tail = f" -- {why}" if why else ""
                print(f"  - {Path(r['file']).name}{tail}")
        print()
        _print_debt_summary(debt)
        _print_project_clone_audit(project_clone_audit)
    return failed_count


def main() -> None:
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args()

    cache = VerificationCache() if args.cache or args.clear_cache else None
    if args.clear_cache:
        if cache is None:
            cache = VerificationCache()
        cleared = cache.clear()
        print(f"Cleared {cleared} cached result(s)")
        sys.exit(0)

    if not args.file and not args.directory:
        parser.error("one of the following arguments is required: file, --directory")

    verifier = EnhancedPythonVerifier()

    if args.directory:
        if not args.directory.exists():
            print(f"Error: Directory not found: {args.directory}", file=sys.stderr)
            sys.exit(2)
        if not args.directory.is_dir():
            print(f"Error: Not a directory: {args.directory}", file=sys.stderr)
            sys.exit(2)

        results_list = verify_directory(
            verifier,
            args.directory,
            args.preset,
            cache,
            args.check_imports,
            args.json,
            include_extras=args.include_extras,
            jobs=args.jobs,
        )
        if not results_list:
            sys.exit(0)
        project_clone_audit = run_project_clone_audit(
            [str(r["file"]) for r in results_list if "file" in r],
            str(args.directory),
        )
        failed_count = print_directory_summary(
            results_list, args.json, project_clone_audit
        )
        debt = _debt_summary(results_list, project_clone_audit)
        _refresh_codemap_if_stale(args.directory)
        debt_failed = args.fail_on_debt and not debt.get("clean", False)
        sys.exit(0 if args.exit_zero or (failed_count == 0 and not debt_failed) else 1)

    if not args.file.exists():
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(2)

    if not args.json:
        print(f"Available tools: {verifier.available_tools}")
        print()

    verification_results = verifier.verify_file(
        args.file,
        preset=args.preset,
        result_cache=cache,
        check_imports=args.check_imports,
    )

    if not args.json and verification_results.get("from_cache"):
        print("[Using cached results]\n")

    print_report(verification_results, json_mode=args.json)

    debt = _debt_summary([verification_results])
    if not args.json:
        _print_debt_summary(debt)

    if args.exit_zero:
        sys.exit(0)
    else:
        debt_failed = args.fail_on_debt and not debt.get("clean", False)
        sys.exit(
            0 if verification_results.get("passed", False) and not debt_failed else 1
        )


if __name__ == "__main__":
    main()

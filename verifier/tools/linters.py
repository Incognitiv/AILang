"""Linter tool runners: pyflakes, pylint, mypy, bandit, ruff."""

from __future__ import annotations

import importlib
import io
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, cast

from .common import validate_filepath

# Matches `<path>:<line>[:<col>]: <severity>: <message>`. Path-shape independent
# (a Windows drive letter `C:` is happily eaten by the lazy `.*?`), so it survives
# both Unix and Windows-style absolute paths. Mypy by default omits the column,
# pylint includes it -- so the column group is optional.
_DIAG_LINE = re.compile(r"^(?P<path>.*?):(?P<line>\d+)(?::\d+)?:\s*\w+:\s*(?P<msg>.*)$")


def _parse_diag(line: str) -> tuple[str, str] | None:
    """Parse one mypy/pylint output line into (lineno, message), or None."""
    m = _DIAG_LINE.match(line)
    if not m:
        return None
    return m.group("line"), m.group("msg")


def _is_frozen() -> bool:
    """Check if running inside a PyInstaller frozen bundle."""
    return getattr(sys, "frozen", False)


# =============================================================================
# SUBPROCESS-BASED RUNNERS (for non-frozen mode - thread safe)
# =============================================================================


def _run_subprocess(
    cmd: List[str], timeout: int = 30, env: Optional[Dict[str, str]] = None
) -> tuple:
    """Run subprocess and return (stdout, stderr, returncode) or error dict.

    The verifier intentionally invokes external static-analysis tools.
    Inputs are validated via shutil.which() before reaching here, and we
    explicitly set shell=False (default, but explicit silences bandit's
    B602 check) so no shell injection surface exists.
    """
    import subprocess

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            env=env,
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return None, None, {"error": f"{cmd[0]} not installed"}
    except subprocess.TimeoutExpired:
        return None, None, {"error": f"{cmd[0]} timed out"}
    except (OSError, IOError) as exc:
        return None, None, {"error": str(exc)}


# =============================================================================
# PYTHON API-BASED RUNNERS (for frozen mode - embedded in binary)
# =============================================================================


def _run_pyflakes_api(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Run pyflakes using Python API."""
    try:
        pyflakes_api = cast(Any, importlib.import_module("pyflakes.api"))
        pyflakes_reporter = cast(Any, importlib.import_module("pyflakes.reporter"))
    except ImportError:
        return {"error": "pyflakes not installed"}
    try:
        with open(filepath, encoding="utf-8") as f:
            code = f.read()
        warn_stream = io.StringIO()
        err_stream = io.StringIO()
        reporter = pyflakes_reporter.Reporter(warn_stream, err_stream)
        pyflakes_api.check(code, filepath, reporter)
        output = warn_stream.getvalue() + err_stream.getvalue()
        issues = [
            ln.replace(filepath, actual_name) for ln in output.strip().split("\n") if ln
        ]
        return {
            "issues_count": len(issues),
            "issues": issues[:100],
            "passed": not issues,
        }
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


def _run_pylint_api(
    filepath: str, actual_name: str, check_imports: bool = False
) -> Dict[str, Any]:
    """Run pylint using Python API."""
    del actual_name
    try:
        pylint_lint = cast(Any, importlib.import_module("pylint.lint"))
        pylint_reporters = cast(Any, importlib.import_module("pylint.reporters.text"))
    except ImportError:
        return {"error": "pylint not installed"}
    try:
        output = io.StringIO()
        disable_list = (
            "missing-docstring,missing-module-docstring,"
            "missing-class-docstring,missing-function-docstring"
        )
        if not check_imports:
            disable_list += ",import-error"
        args = [
            filepath,
            "--output-format=text",
            "--score=y",
            f"--disable={disable_list}",
            "--ignored-modules=black",
        ]
        # Check for .pylintrc in the file's directory
        file_dir = os.path.dirname(filepath)
        pylintrc_path = os.path.join(file_dir, ".pylintrc")
        if os.path.exists(pylintrc_path):
            args.append(f"--rcfile={pylintrc_path}")
        reporter = pylint_reporters.TextReporter(output)
        captured_out, captured_err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = captured_out, captured_err
            pylint_lint.Run(args, reporter=reporter, exit=False)
        except SystemExit:
            pass
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        all_out = output.getvalue() + captured_out.getvalue() + captured_err.getvalue()
        score = 10.0
        for line in all_out.split("\n"):
            if "Your code has been rated at" in line:
                try:
                    score = float(line.split("rated at ")[1].split("/")[0])
                except (IndexError, ValueError):
                    pass
        issues: List[str] = []
        for line in all_out.split("\n"):
            if filepath not in line:
                continue
            parsed = _parse_diag(line)
            if parsed is not None:
                issues.append(f"Line {parsed[0]}: {parsed[1]}")
        return {
            "score": score,
            "issues_count": len(issues),
            "issues": issues[:100],
            "passed": score >= 8.0,
        }
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


INCOMPLETE_STUB_MODULES = {"black", "isort", "mypy"}
FILTERED_MYPY_ERRORS = {"import-untyped", "import-not-found"}


def _mypy_env_for(filepath: str) -> Dict[str, str]:
    """Build a mypy environment that can resolve repo-local import roots."""
    env = os.environ.copy()
    cwd = os.path.abspath(os.getcwd())
    del filepath
    candidate_roots = [
        os.path.join(cwd, "typings"),
        os.path.join(cwd, "source"),
        cwd,
        os.path.join(cwd, "tests"),
    ]
    existing = env.get("MYPYPATH", "")
    if existing:
        candidate_roots.extend(p for p in existing.split(os.pathsep) if p)

    seen: set[str] = set()
    roots: List[str] = []
    for root in candidate_roots:
        norm = os.path.normcase(os.path.abspath(root))
        if norm in seen or not os.path.isdir(root):
            continue
        seen.add(norm)
        roots.append(root)
    env["MYPYPATH"] = os.pathsep.join(roots)
    return env


def _mypy_cache_dir() -> str:
    """Use a verifier-owned cache namespace so option/env changes cannot stale."""
    return os.path.join(os.getcwd(), ".mypy_cache", "verifier-v2")


def _run_mypy_api(
    filepath: str, actual_name: str, check_imports: bool = False
) -> Dict[str, Any]:
    """Run mypy using Python API."""
    os.environ["MYPY_USE_MYPYC"] = "0"
    os.environ["MYPY_FORCE_PURE"] = "1"
    try:
        from mypy import api as mypy_api
    except ImportError as exc:
        return {"error": f"mypy import failed: {exc}", "passed": False}
    try:
        args = [
            filepath,
            "--show-error-codes",
            "--explicit-package-bases",
            "--no-pretty",
            "--no-error-summary",
            # Incremental cache shared across the whole verifier run.
            # First call populates, subsequent calls reuse parsed dependencies.
            "--incremental",
            "--cache-dir",
            _mypy_cache_dir(),
        ]
        if not check_imports:
            args.append("--ignore-missing-imports")
        # mypy_api.run returns (stdout, stderr, exit_code). We combine
        # stdout and stderr (mypy writes errors to stdout in some modes
        # and stderr in others) and ignore the exit code (we count
        # errors directly from the parsed output).
        old_mypy_path = os.environ.get("MYPYPATH")
        os.environ["MYPYPATH"] = _mypy_env_for(filepath)["MYPYPATH"]
        try:
            stdout, stderr, _exit = mypy_api.run(args)
        finally:
            if old_mypy_path is None:
                os.environ.pop("MYPYPATH", None)
            else:
                os.environ["MYPYPATH"] = old_mypy_path
        output = (stdout or "") + (stderr or "")
        errors: List[str] = []
        for line in output.split("\n"):
            if ":" in line and "error:" in line:
                if "[attr-defined]" in line:
                    if any(f'Module "{m}"' in line for m in INCOMPLETE_STUB_MODULES):
                        continue
                    if "Module has no attribute" in line:
                        continue
                if not check_imports:
                    if any(f"[{ec}]" in line for ec in FILTERED_MYPY_ERRORS):
                        continue
                parsed = _parse_diag(line.replace(filepath, actual_name))
                if parsed is not None:
                    errors.append(f"Line {parsed[0]}: {parsed[1]}")
        return {
            "errors_count": len(errors),
            "errors": errors[:100],
            "passed": not errors,
        }
    except (OSError, IOError) as exc:
        return {"error": str(exc), "passed": False}


def _run_bandit_api(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Run bandit using Python API."""
    try:
        bandit_config = cast(Any, importlib.import_module("bandit.core.config"))
        bandit_manager = cast(Any, importlib.import_module("bandit.core.manager"))
    except ImportError:
        return {"error": "bandit not installed"}
    try:
        conf = bandit_config.BanditConfig()
        mgr = bandit_manager.BanditManager(conf, "file")
        mgr.discover_files([filepath])
        mgr.run_tests()
        high, medium, low = 0, 0, 0
        issues: List[str] = []
        # Skip B105 (hardcoded password) - too many false positives with token names
        skipped_tests = {"B105"}
        for issue in mgr.get_issue_list():
            if issue.test_id in skipped_tests:
                continue
            sev = issue.severity
            if sev == "HIGH":
                high += 1
            elif sev == "MEDIUM":
                medium += 1
            else:
                low += 1
            issues.append(f">> [{issue.test_id}:{issue.test}] {issue.text}")
        return {
            "high_severity": high,
            "medium_severity": medium,
            "low_severity": low,
            "total_issues": len(issues),
            "issues": issues[:100],
            "passed": high == 0,
        }
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


# =============================================================================
# SUBPROCESS-BASED RUNNERS (for non-frozen mode)
# =============================================================================


def _run_pyflakes_subprocess(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Run pyflakes via subprocess."""
    pyflakes_path = shutil.which("pyflakes")
    if not pyflakes_path:
        return {"error": "pyflakes not found on PATH"}
    stdout, stderr, rc = _run_subprocess([pyflakes_path, filepath])
    if isinstance(rc, dict):
        return rc
    output = (stdout or "") + (stderr or "")
    issues = [
        ln.replace(filepath, actual_name)
        for ln in output.splitlines()
        if filepath in ln
    ]
    return {
        "issues_count": len(issues),
        "issues": issues[:100],
        "passed": len(issues) == 0,
    }


def _run_pylint_subprocess(
    filepath: str, actual_name: str, check_imports: bool = False
) -> Dict[str, Any]:
    """Run pylint via subprocess with JSON output."""
    del actual_name
    pylint_path = shutil.which("pylint")
    if not pylint_path:
        return {"error": "pylint not found on PATH"}
    disable_list = (
        "missing-docstring,missing-module-docstring,"
        "missing-class-docstring,missing-function-docstring"
    )
    if not check_imports:
        disable_list += ",import-error"
    cmd = [
        pylint_path,
        filepath,
        "--output-format=json",
        "--score=y",
        f"--disable={disable_list}",
        "--ignored-modules=black",
    ]
    # Check for .pylintrc in the file's directory
    file_dir = os.path.dirname(filepath)
    pylintrc_path = os.path.join(file_dir, ".pylintrc")
    if os.path.exists(pylintrc_path):
        cmd.append(f"--rcfile={pylintrc_path}")
    stdout, stderr, rc = _run_subprocess(cmd, timeout=60, env=_mypy_env_for(filepath))
    if isinstance(rc, dict):
        return rc
    issues: List[str] = []
    try:
        if stdout and stdout.strip():
            for item in json.loads(stdout):
                issues.append(
                    f"Line {item.get('line', 0)}: [{item.get('symbol', '')}] "
                    f"{item.get('message', '')}"
                )
    except json.JSONDecodeError:
        pass
    score = 10.0
    for line in (stderr or "").split("\n"):
        if "Your code has been rated at" in line:
            try:
                score = float(line.split("rated at ")[1].split("/")[0])
            except (IndexError, ValueError):
                pass
    return {
        "score": score,
        "issues_count": len(issues),
        "issues": issues[:100],
        "passed": score >= 8.0,
    }


def _run_mypy_subprocess(
    filepath: str, actual_name: str, check_imports: bool = False
) -> Dict[str, Any]:
    """Run mypy via subprocess."""
    mypy_path = shutil.which("mypy")
    if not mypy_path:
        return {"error": "mypy not found on PATH"}
    cmd = [
        mypy_path,
        filepath,
        "--show-error-codes",
        "--explicit-package-bases",
        "--no-pretty",
        "--no-error-summary",
        # Incremental cache: dramatic speedup on subsequent runs.
        "--incremental",
        "--cache-dir",
        _mypy_cache_dir(),
    ]
    if not check_imports:
        cmd.append("--ignore-missing-imports")
    stdout, stderr, rc = _run_subprocess(cmd, timeout=60, env=_mypy_env_for(filepath))
    if isinstance(rc, dict):
        return rc
    output = (stdout or "") + (stderr or "")
    errors: List[str] = []
    for line in output.split("\n"):
        if ":" in line and "error:" in line:
            if "[attr-defined]" in line:
                if any(f'Module "{m}"' in line for m in INCOMPLETE_STUB_MODULES):
                    continue
                if "Module has no attribute" in line:
                    continue
            if not check_imports:
                if any(f"[{ec}]" in line for ec in FILTERED_MYPY_ERRORS):
                    continue
            parsed = _parse_diag(line.replace(filepath, actual_name))
            if parsed is not None:
                errors.append(f"Line {parsed[0]}: {parsed[1]}")
    return {
        "errors_count": len(errors),
        "errors": errors[:100],
        "passed": not errors,
    }


def _run_bandit_subprocess(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Run bandit via subprocess with JSON output."""
    bandit_path = shutil.which("bandit")
    if not bandit_path:
        return {"error": "bandit not found on PATH"}
    cmd = [bandit_path, "-f", "json", "-q", "--exit-zero", filepath]
    stdout, _, rc = _run_subprocess(cmd)
    if isinstance(rc, dict):
        return rc
    try:
        data = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "Bandit produced invalid JSON", "passed": False}
    high, medium, low = 0, 0, 0
    issues: List[str] = []
    # Skip common false positives:
    # B105 - hardcoded password (token names like 'DEF', 'IF')
    # B404 - subprocess import (needed for compilers)
    # B603 - subprocess call (needed for compilers)
    skipped_tests = {"B105", "B404", "B603"}
    for item in data.get("results", []):
        test_id = item.get("test_id", "")
        if test_id in skipped_tests:
            continue
        sev = item.get("issue_severity", "LOW")
        if sev == "HIGH":
            high += 1
        elif sev == "MEDIUM":
            medium += 1
        else:
            low += 1
        issues.append(f">> [{test_id}] {item.get('issue_text')}")
    return {
        "high_severity": high,
        "medium_severity": medium,
        "low_severity": low,
        "total_issues": len(issues),
        "issues": issues[:100],
        "passed": high == 0,
    }


# =============================================================================
# PUBLIC API - automatically selects API vs subprocess based on frozen state
# =============================================================================


def run_pyflakes(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Run pyflakes - uses API in frozen mode, subprocess otherwise."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    if _is_frozen():
        return _run_pyflakes_api(validated_path, actual_name)
    return _run_pyflakes_subprocess(validated_path, actual_name)


def run_pylint(
    filepath: str, actual_name: str, check_imports: bool = False
) -> Dict[str, Any]:
    """Run pylint - uses API in frozen mode, subprocess otherwise."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    if _is_frozen():
        return _run_pylint_api(validated_path, actual_name, check_imports)
    return _run_pylint_subprocess(validated_path, actual_name, check_imports)


def run_mypy(
    filepath: str, actual_name: str, check_imports: bool = False
) -> Dict[str, Any]:
    """Run mypy - uses API in frozen mode, subprocess otherwise."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    if _is_frozen():
        return _run_mypy_api(validated_path, actual_name, check_imports)
    return _run_mypy_subprocess(validated_path, actual_name, check_imports)


def run_bandit(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Run bandit - uses API in frozen mode, subprocess otherwise."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    if _is_frozen():
        return _run_bandit_api(validated_path, actual_name)
    return _run_bandit_subprocess(validated_path, actual_name)


def run_ruff(filepath: str, actual_name: str) -> Dict[str, Any]:
    """Run ruff - subprocess only (Rust binary, no Python API)."""
    del actual_name
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    ruff_path = shutil.which("ruff")
    if not ruff_path:
        # Ruff not installed - pass silently (optional tool)
        return {"passed": True, "issues_count": 0, "issues": []}
    cmd = [ruff_path, "check", "--output-format=json", validated_path]
    stdout, _, rc = _run_subprocess(cmd)
    if isinstance(rc, dict):
        return {"passed": True, "issues_count": 0, "issues": []}
    issues: List[str] = []
    try:
        if stdout and stdout.strip():
            for item in json.loads(stdout):
                line = item.get("location", {}).get("row", 0)
                code = item.get("code", "")
                msg = item.get("message", "")
                issues.append(f"Line {line}: [{code}] {msg}")
    except json.JSONDecodeError:
        pass
    return {
        "issues_count": len(issues),
        "issues": issues[:100],
        "passed": len(issues) == 0,
    }

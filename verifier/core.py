"""
Enhanced Python Code Verifier

Coordinates multiple external tools plus custom checks to enforce 10/10 quality.
"""

import importlib.util
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .cache import VerificationCache
from .tools import (
    check_nesting_depth,
    check_syntax,
    detect_suppressions,
    run_bandit,
    run_black,
    run_clone_check,
    run_cohesion,
    run_consistency_check,
    run_detect_secrets,
    run_isort,
    run_magic_index_check,
    run_mypy,
    run_positional_access_audit,
    run_pyflakes,
    run_radon,
    run_ruff,
    run_strict_extras,
    run_todo_check,
    run_vulture,
)
from .verifier_report import generate_summary

COMPILER_DISPATCH_COMPLEXITY_LIMIT = 150


class EnhancedPythonVerifier:
    """
    Comprehensive Python code verification using industry-standard tools.

    Single responsibility: orchestrate tool execution and scoring.
    Reporting extracted to verifier_report, runners to tool_runners.
    """

    def __init__(self):
        self.available_tools = self._check_available_tools()
        self._check_missing_dependencies()

    def get_available_tools(self) -> Dict[str, bool]:
        """Return dictionary of available verification tools."""
        return self.available_tools.copy()

    def _check_available_tools(self) -> Dict[str, bool]:
        # Custom checkers - always available (no external deps)
        tools = {
            "nesting": True,
            "clone": True,
            "magic_index": True,
            "positional_access": True,
            "consistency": True,
            "todo": True,  # Built-in TODO/FIXME checker
            "strict_extras": True,  # Built-in W0201 + W1114 (replaces pylint)
        }
        module_names = {
            "pyflakes": "pyflakes",
            "pylint": "pylint",
            "mypy": "mypy",
            "bandit": "bandit",
            "radon": "radon",
            "black": "black",
            "isort": "isort",
            "ruff": "ruff",
            "vulture": "vulture",
            "cohesion": "cohesion",
            "pip_audit": "pip_audit",
            "detect_secrets": "detect_secrets",
        }
        for tool, module_name in module_names.items():
            spec = importlib.util.find_spec(module_name)
            tools[tool] = spec is not None
        return tools

    def _check_missing_dependencies(self) -> None:
        """Check for missing dependencies and notify user."""
        missing = [
            tool
            for tool, available in self.available_tools.items()
            if not available and tool != "nesting"
        ]
        if missing:
            req_file = Path(__file__).parent / "requirements.txt"
            print(
                f"\n!  Warning: {len(missing)} verification tool(s) not found:",
                file=sys.stderr,
            )
            for tool in missing:
                print(f"  - {tool}", file=sys.stderr)
            if req_file.exists():
                print("\nInstall missing dependencies with:", file=sys.stderr)
                print(f"  python -m pip install --user -r {req_file}", file=sys.stderr)
            else:
                install_cmd = " ".join(missing)
                print(
                    f"\nInstall with: python -m pip install --user {install_cmd}",
                    file=sys.stderr,
                )
            print("", file=sys.stderr)

    def _prepare_tool_jobs(self) -> List[Tuple[str, Callable, bool]]:
        jobs: List[Tuple[str, Callable, bool]] = [
            ("pyflakes", run_pyflakes, True),
            # pylint replaced by ruff + strict_extras (W0201 + W1114).
            # Runtime: pylint ~17.8s -> ruff ~0.05s + strict_extras ~0.1s.
            # Coverage: identical for our codebase.
            ("strict_extras", run_strict_extras, True),
            ("mypy", run_mypy, True),
            ("bandit", run_bandit, True),
            ("radon", run_radon, True),  # Stored as "radon", accessed as "complexity"
            ("black", run_black, True),
            ("isort", run_isort, True),
            ("ruff", run_ruff, True),
            ("vulture", run_vulture, True),
            ("cohesion", run_cohesion, True),
            ("nesting", check_nesting_depth, False),
            ("clone", run_clone_check, True),
            ("magic_index", run_magic_index_check, True),
            ("positional_access", run_positional_access_audit, True),
            ("consistency", run_consistency_check, True),
            ("todo", run_todo_check, True),  # Track incomplete implementations
        ]
        # `pip_audit` scans the entire Python environment and may touch network
        # services. Running it once per source file turns a code verifier into a
        # slow environment scanner, and a single blocked audit can freeze the
        # whole run. Keep per-file verification strictly file-local.
        jobs.append(("detect_secrets", run_detect_secrets, False))
        return jobs

    def _execute_tools_parallel(
        self,
        jobs: List[Tuple[str, Callable, bool]],
        filepath: str,
        display_name: str,
        check_imports: bool = False,
    ) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        tools = self.available_tools
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            task_futures = {}
            for tool_name, runner, needs_filename in jobs:
                if not tools.get(tool_name):
                    results[tool_name] = None
                    continue
                tool_args: tuple = (filepath,)
                if needs_filename:
                    if tool_name in ("pylint", "mypy") and check_imports:
                        tool_args = (filepath, display_name, True)
                    else:
                        tool_args = (filepath, display_name)
                task = pool.submit(runner, *tool_args)
                task_futures[task] = tool_name

            for task in as_completed(task_futures):
                tool_name = task_futures[task]
                try:
                    results[tool_name] = task.result()
                except (OSError, TimeoutError) as exc:
                    results[tool_name] = {"error": f"Tool crashed: {exc}"}
        return results

    def _run_verification(
        self,
        code: str,
        filepath: str,
        display_name: str,
        preset: str,
        check_imports: bool,
        result_cache: Optional[VerificationCache],
    ) -> Dict:
        """Core verification logic shared by verify_code and verify_file."""
        if result_cache:
            cached = result_cache.get(code, preset)
            if cached:
                cached["from_cache"] = True
                return cached

        syntax_result = check_syntax(code)
        if not syntax_result["valid"]:
            return {
                "syntax": syntax_result,
                "overall_score": 0.0,
                "passed": False,
                "fail_reasons": ["syntax invalid"],
                "summary": "[SYNTAX] INVALID - Code has syntax errors",
            }

        # Detect suppression comments (type: ignore, pylint: disable, noqa, etc.)
        suppressions = detect_suppressions(code)

        # Preset is one of: "strict" (default, full formatter deductions) or
        # "fast" (skip formatter deductions). Earlier versions also had
        # "normal" as an alias for "fast" -- accept it for backwards compat.
        base_preset = "strict" if preset == "strict" else "fast"
        jobs = self._prepare_tool_jobs()
        results = self._execute_tools_parallel(
            jobs, filepath, display_name, check_imports=check_imports
        )
        results["pip_audit"] = {
            "passed": True,
            "vulnerabilities_count": 0,
            "issues": [],
            "source": "skipped-per-file",
            "note": "Environment audit is intentionally not run per file.",
        }
        results["syntax"] = syntax_result
        results["suppressions"] = suppressions
        results["overall_score"] = self._calculate_score(results, preset=base_preset)
        results["passed"], results["fail_reasons"] = self._determine_pass(results)
        results["summary"] = generate_summary(results)
        results["from_cache"] = False

        if result_cache:
            result_cache.set(code, preset, results)
        return results

    def verify_code(
        self,
        code: str,
        filename: str = "temp.py",
        preset: str = "strict",
        result_cache: Optional[VerificationCache] = None,
    ) -> Dict:
        """Verify Python code string using all available tools."""
        temp_file: str = ""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            temp_file = f.name
        try:
            return self._run_verification(
                code, temp_file, filename, preset.lower(), False, result_cache
            )
        finally:
            if temp_file:
                Path(temp_file).unlink(missing_ok=True)

    def verify_file(
        self,
        filepath: Path,
        preset: str = "strict",
        result_cache: Optional[VerificationCache] = None,
        check_imports: bool = False,
    ) -> Dict:
        """Verify a Python file in-place (enables import checking when requested)."""
        code = filepath.read_text(encoding="utf-8")
        filename = str(filepath)
        return self._run_verification(
            code, filename, filename, preset.lower(), check_imports, result_cache
        )

    # Tool-specific runners are provided by tool_runners; no per-instance wrappers here.

    def _score_pylint(self, results: Dict) -> float:
        """Score the lint slot (max 15 points).

        Reads from the strict_extras tool (W0201 + W1114 coverage that
        replaces pylint, plus ruff already runs separately for the rest).
        Falls back to pylint's score if strict_extras isn't available, so
        old configs still work.
        """
        extras = results.get("strict_extras")
        if extras is not None:
            issues = extras.get("issues_count", 0)
            if extras.get("error"):
                return 0.0
            # Each issue costs 5 points (caps at 15 = lose all of the slot).
            return max(0.0, 15.0 - min(15.0, issues * 5.0))
        # Fallback to pylint if present.
        pylint_result = results.get("pylint")
        if pylint_result is None:
            return 0.0
        pylint_score = pylint_result.get("score")
        if pylint_score is None:
            return 0.0
        return (pylint_score / 10.0) * 15.0

    def _score_security(self, results: Dict) -> float:
        bandit = results.get("bandit")
        if bandit is None:
            return 0.0  # Tool missing - no credit awarded
        if "high_severity" not in bandit:
            return 0.0

        high = bandit.get("high_severity", 0)
        medium = bandit.get("medium_severity", 0)
        low = bandit.get("low_severity", 0)

        # High=15pts, Medium=5pts, Low=1pt penalty each
        deduction = (high * 15) + (medium * 5) + (low * 1)

        # pip_audit checks system-wide packages, not project code quality.
        # So we don't penalize score for it - it's informational only

        detect = results.get("detect_secrets") or {}
        ds_issues = len(detect.get("issues", [])) if isinstance(detect, dict) else 0
        deduction += ds_issues * 3  # each finding 3pts

        return max(0.0, 25.0 - deduction)

    def _score_pyflakes(self, results: Dict) -> float:
        """Score pyflakes (max 15 points, -2 per issue)."""
        pyflakes_result = results.get("pyflakes")
        if pyflakes_result is None:
            return 0.0
        issues = pyflakes_result.get("issues_count")
        if issues is None:
            return 0.0
        return max(0.0, 15.0 - (issues * 2))

    def _score_mypy(self, results: Dict) -> float:
        """Score mypy (max 30 points - correctness is king, -3 per error)."""
        mypy_result = results.get("mypy")
        if mypy_result is None:
            return 0.0
        errors = mypy_result.get("errors_count")
        if errors is None:
            return 0.0
        return max(0.0, 30.0 - (errors * 3))

    def _score_complexity(self, results: Dict) -> float:
        """Score complexity (max 15 points - algorithm quality matters).

        Uses lenient thresholds to allow complex but maintainable code.
        Max CC 150 allows compilers with large dispatch functions (parsers,
        code generators) which legitimately have high cyclomatic complexity.
        """
        complexity = results.get("radon")
        if complexity is None:
            return 0.0
        if "max_complexity" not in complexity and "avg_complexity" not in complexity:
            return 0.0
        points = 15.0
        max_cc = complexity.get("max_complexity", complexity.get("avg_complexity", 0))
        if max_cc > COMPILER_DISPATCH_COMPLEXITY_LIMIT:
            points -= 5.0
        return max(0.0, points)

    def _strict_deductions(self, results: Dict, preset: str) -> float:
        if preset != "strict":
            return 0.0
        penalties = {"black": 5.0, "isort": 3.0, "ruff": 5.0}
        total = 0.0
        for key, penalty in penalties.items():
            tool_result = results.get(key)
            if tool_result is None:
                total += penalty  # Missing tool = full penalty (no uninstall exploit)
            elif tool_result.get("passed", True) is False:
                total += penalty
        return total

    # Suppression policy: each `type: ignore`, `noqa`, `pylint: disable`,
    # etc. is treated as borrowed correctness that was never paid back. The
    # verifier deducts points AND fails the file. Bypasses (one-off rare
    # cases like third-party tool scaffolding) belong in EXCLUDE_DIRS, not
    # inline -- otherwise the policy has no teeth.
    _SUPPRESSION_PENALTY_PER = 5.0
    _SUPPRESSION_PENALTY_CAP = 25.0

    # Magic-index policy: `node[0]`, `token[1]` etc. on suspicious names
    # is structured data smuggled through the type system as positional
    # access. Same teeth as suppressions -- deduct + fail. The fix is to
    # use named fields (NamedTuple, dataclass) or destructuring.
    _MAGIC_PENALTY_PER = 4.0
    _MAGIC_PENALTY_CAP = 20.0

    def _suppression_deduction(self, results: Dict) -> float:
        """Compute the score deduction from suppression markers."""
        supp = results.get("suppressions") or {}
        count = supp.get("total", 0) if isinstance(supp, dict) else 0
        if count <= 0:
            return 0.0
        return min(self._SUPPRESSION_PENALTY_CAP, count * self._SUPPRESSION_PENALTY_PER)

    def _magic_index_deduction(self, results: Dict) -> float:
        """Compute the score deduction from magic-index patterns."""
        magic = results.get("magic_index") or {}
        count = magic.get("magic_index_count", 0) if isinstance(magic, dict) else 0
        if count <= 0:
            return 0.0
        return min(self._MAGIC_PENALTY_CAP, count * self._MAGIC_PENALTY_PER)

    def _calculate_score(self, results: Dict, preset: str = "strict") -> float:
        """Calculate overall quality score (0-100) with balanced weighting."""
        if not results["syntax"]["valid"]:
            return 0.0

        scores = {
            "pylint": self._score_pylint(results),
            "security": self._score_security(results),
            "pyflakes": self._score_pyflakes(results),
            "mypy": self._score_mypy(results),
            "complexity": self._score_complexity(results),
        }

        deductions = self._strict_deductions(results, preset)
        # Suppression and magic-index deductions apply regardless of
        # preset -- borrowing correctness without paying back, and
        # smuggling structured data through positional access, are both
        # debts in any mode.
        deductions += self._suppression_deduction(results)
        deductions += self._magic_index_deduction(results)
        if deductions:
            scores["deductions"] = deductions

        results["score_details"] = scores
        total = sum(v for k, v in scores.items() if k != "deductions")
        total -= scores.get("deductions", 0.0)
        return round(max(total, 0.0), 2)

    def _determine_pass(self, results: Dict) -> Tuple[bool, List[str]]:
        """Enforce absolute pass criteria - must achieve 100/100 score."""
        reasons: List[str] = []

        # Strict score requirement: must be exactly 100
        score = results.get("overall_score", 0.0)
        if score < 100.0:
            reasons.append(f"score {score:.0f}/100 (must be 100)")

        if not results.get("syntax", {}).get("valid", False):
            reasons.append("syntax invalid")

        # Suppression markers (`type: ignore`, `noqa`, etc.) are
        # treated as borrowed correctness. Any presence fails the file --
        # if the suppression is structurally necessary (e.g. a third-party
        # naming convention), exclude the file via EXCLUDE_DIRS rather
        # than suppressing inline.
        supp = results.get("suppressions") or {}
        supp_count = supp.get("total", 0) if isinstance(supp, dict) else 0
        if supp_count > 0:
            reasons.append(f"{supp_count} suppression(s) present")

        # Magic indexing on structured names (token[0], node[1], etc.)
        # is positional access through the type system. Any occurrence
        # fails -- the fix is named fields (NamedTuple, dataclass) or
        # destructuring.
        magic = results.get("magic_index") or {}
        magic_count = (
            magic.get("magic_index_count", 0) if isinstance(magic, dict) else 0
        )
        if magic_count > 0:
            reasons.append(f"{magic_count} magic index pattern(s)")

        def presence_and_reason(name: str, checker) -> None:
            res = results.get(name)
            if res is None:
                return
            reason = checker(res)
            if reason:
                reasons.append(f"{name} {reason}")

        presence_and_reason(
            "pylint",
            lambda r: (
                None if r.get("passed", False) else f"score {r.get('score', 0.0):.2f}"
            ),
        )

        presence_and_reason(
            "pyflakes",
            lambda r: None if r.get("issues_count", 0) == 0 else "lint issues present",
        )

        presence_and_reason(
            "mypy",
            lambda r: (
                None
                if r.get("errors_count", 0) == 0
                else f"{r.get('errors_count', 0)} type errors"
            ),
        )

        presence_and_reason(
            "bandit",
            lambda r: (
                None
                if r.get("high_severity", 0) == 0 and r.get("medium_severity", 0) == 0
                else (
                    f"{r.get('total_issues', 0)} issues ("
                    f"{r.get('high_severity', 0)} high, "
                    f"{r.get('medium_severity', 0)} medium)"
                )
            ),
        )

        for tool in ("black", "isort", "ruff"):
            presence_and_reason(
                tool,
                lambda r: None if r.get("passed", True) else "did not pass",
            )

        presence_and_reason(
            "vulture",
            lambda r: (
                None
                if r.get("passed", False)
                else f"{r.get('dead_code_count', 0)} dead code items"
            ),
        )

        # pip_audit and detect_secrets check the environment, not the code.
        # They are informational only and should NOT cause pass/fail
        # (A file can be 100/100 perfect code but run in an environment with CVEs)

        presence_and_reason(
            "nesting",
            lambda r: (
                None
                if r.get("passed", False)
                else f"max depth {r.get('max_depth', 0)} (limit: 6)"
            ),
        )

        passed = len(reasons) == 0
        return passed, reasons


if __name__ == "__main__":
    from verifier.cli import main

    main()

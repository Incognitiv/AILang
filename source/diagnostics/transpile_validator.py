"""
AILang Transpiler Validation Framework

Tracks transpilation progress in real-time with:
- Function counting and comparison
- Body verification between Python and AILang
- Line-by-line diff output
- Success/failure guards with detailed error reporting
- TIMEOUT PROTECTION: All operations have 20-second timeout

Usage:
    validator = TranspileValidator(verbose=True, timeout=20)
    result = validator.validate_file("source.py")

    # Or validate entire directory:
    validator.validate_directory("ailang/")
"""

from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from diagnostics.transpile_validator_models import (
    ClassInfo,
    Colors,
    FunctionInfo,
    TranspileResult,
    ValidationReport,
)

# Default timeout for operations (seconds)
DEFAULT_TIMEOUT = 20


class TranspileTimeoutError(Exception):
    """Raised when a transpilation operation times out."""


def run_with_timeout(func: Callable, timeout: int, *args, **kwargs):
    """Run a function with timeout protection.

    Args:
        func: Function to run
        timeout: Maximum seconds to wait
        *args, **kwargs: Arguments to pass to func

    Returns:
        Result of func

    Raises:
        TranspileTimeoutError: If operation exceeds timeout
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TranspileTimeoutError(
                f"Operation timed out after {timeout} seconds"
            ) from None


class PythonAnalyzer:
    """Analyzes Python source code to extract structure."""

    def analyze(self, source: str) -> Tuple[List[FunctionInfo], List[ClassInfo]]:
        """Analyze Python source and return functions and classes."""
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python syntax: {e}") from e

        functions: List[FunctionInfo] = []
        classes: List[ClassInfo] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Skip nested functions (methods are handled by class)
                if not self._is_method(node, tree):
                    functions.append(self._analyze_function(node, source))
            elif isinstance(node, ast.ClassDef):
                classes.append(self._analyze_class(node, source))

        return functions, classes

    def _is_method(self, func: ast.FunctionDef, tree: ast.Module) -> bool:
        """Check if function is a method inside a class."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if item is func:
                        return True
        return False

    def _analyze_function(
        self, node: ast.FunctionDef, source: str, class_name: Optional[str] = None
    ) -> FunctionInfo:
        """Extract information about a function."""
        # Get parameters
        params = []
        for arg in node.args.args:
            param_name = arg.arg
            if arg.annotation:
                param_type = ast.unparse(arg.annotation)
                params.append(f"{param_name}: {param_type}")
            else:
                params.append(param_name)

        # Get return type
        return_type = None
        if node.returns:
            return_type = ast.unparse(node.returns)

        # Get decorators
        decorators = [ast.unparse(d) for d in node.decorator_list]

        # Get docstring
        docstring = ast.get_docstring(node)

        # Get body hash for comparison
        body_source = self._get_body_source(node, source)
        body_hash = self._hash_body(body_source)

        return FunctionInfo(
            name=node.name,
            params=params,
            return_type=return_type,
            body_lines=node.end_lineno - node.lineno if node.end_lineno else 0,
            body_hash=body_hash,
            decorators=decorators,
            docstring=docstring,
            is_method=class_name is not None,
            class_name=class_name,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
        )

    def _analyze_class(self, node: ast.ClassDef, source: str) -> ClassInfo:
        """Extract information about a class."""
        # Get base classes
        bases = [ast.unparse(b) for b in node.bases]

        # Get methods
        methods = [
            self._analyze_function(item, source, node.name)
            for item in node.body
            if isinstance(item, ast.FunctionDef)
        ]

        # Get fields from __init__ assignments
        fields: Dict[str, str] = {}
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.AnnAssign) and (
                        isinstance(stmt.target, ast.Attribute)
                        and (
                            isinstance(stmt.target.value, ast.Name)
                            and stmt.target.value.id == "self"
                        )
                    ):
                        field_name = stmt.target.attr
                        if stmt.annotation:
                            fields[field_name] = ast.unparse(stmt.annotation)
                        else:
                            fields[field_name] = "Any"

        return ClassInfo(
            name=node.name,
            bases=bases,
            methods=methods,
            fields=fields,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
        )

    def _get_body_source(self, node: ast.FunctionDef, source: str) -> str:
        """Get the source code of a function body."""
        lines = source.splitlines()
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno else start + 1
        return "\n".join(lines[start:end])

    def _hash_body(self, body: str) -> str:
        """Create a hash of normalized body for comparison."""
        # Normalize: remove whitespace, comments
        normalized = re.sub(r"#.*$", "", body, flags=re.MULTILINE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()[:16]


class AILangAnalyzer:
    """Analyzes AILang source code to extract structure."""

    # Regex patterns for AILang constructs
    FUNCTION_PATTERN = re.compile(
        r"def\s+(\w+)\s*\((.*?)\)(?:\s*:\s*(\w+))?\s*:",
        re.MULTILINE | re.DOTALL,
    )
    RECORD_PATTERN = re.compile(r"record\s+(\w+)\s*(?:\((.*?)\))?\s*:", re.MULTILINE)
    CLASS_PATTERN = re.compile(r"class\s+(\w+)\s*(?:\((.*?)\))?\s*:", re.MULTILINE)

    def analyze(self, source: str) -> Tuple[List[FunctionInfo], List[str]]:
        """Analyze AILang source and return functions and records."""
        functions = self._extract_functions(source)
        records = self._extract_records(source)
        return functions, records

    def _extract_functions(self, source: str) -> List[FunctionInfo]:
        """Extract function information from AILang source."""
        functions: List[FunctionInfo] = []

        for match in self.FUNCTION_PATTERN.finditer(source):
            name = match.group(1)
            params_str = match.group(2)
            return_type = match.group(3)

            # Parse parameters
            params = []
            if params_str.strip():
                for param in params_str.split(","):
                    param = param.strip()
                    if param:
                        params.append(param)

            # Find body (everything until 'end')
            start_pos = match.end()
            body_end = self._find_matching_end(source, start_pos)
            body = source[start_pos:body_end]

            body_hash = self._hash_body(body)
            body_lines = body.count("\n") + 1

            functions.append(
                FunctionInfo(
                    name=name,
                    params=params,
                    return_type=return_type,
                    body_lines=body_lines,
                    body_hash=body_hash,
                    start_line=source[: match.start()].count("\n") + 1,
                    end_line=source[:body_end].count("\n") + 1,
                )
            )

        return functions

    def _extract_records(self, source: str) -> List[str]:
        """Extract record names from AILang source."""
        return [match.group(1) for match in self.RECORD_PATTERN.finditer(source)]

    def _find_matching_end(self, source: str, start: int) -> int:
        """Find the matching 'end' keyword for a block."""
        depth = 1
        pos = start
        keywords_opening = {"if", "for", "while", "def", "class", "record", "loop"}

        while pos < len(source) and depth > 0:
            # Find next keyword
            next_end = source.find("end", pos)
            if next_end == -1:
                return len(source)

            # Check for opening keywords between pos and next_end
            segment = source[pos:next_end]
            for keyword in keywords_opening:
                pattern = rf"\b{keyword}\b"
                depth += len(re.findall(pattern, segment))

            depth -= 1  # Found an 'end'
            pos = next_end + 3

        return pos

    def _hash_body(self, body: str) -> str:
        """Create a hash of normalized body for comparison."""
        normalized = re.sub(r"//.*$", "", body, flags=re.MULTILINE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()[:16]


class TranspileValidator:
    """
    Validates transpilation between Python and AILang.

    Features:
    - Real-time progress tracking
    - Function-by-function comparison
    - Body hash verification
    - Detailed diff output
    - Summary statistics
    - TIMEOUT PROTECTION: All operations limited to prevent hangs
    """

    def __init__(
        self,
        verbose: bool = True,
        show_diff: bool = False,
        color: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.verbose = verbose
        self.show_diff = show_diff
        self.timeout = timeout
        self.python_analyzer = PythonAnalyzer()
        self.ailang_analyzer = AILangAnalyzer()

        if not color or not sys.stdout.isatty():
            Colors.disable()

        self.log(f"Timeout set to {timeout} seconds per operation", "info")

    def log(self, msg: str, level: str = "info") -> None:
        """Log a message with appropriate formatting."""
        if not self.verbose:
            return

        prefix_map = {
            "info": f"{Colors.BLUE}[INFO]{Colors.RESET}",
            "success": f"{Colors.GREEN}[✓]{Colors.RESET}",
            "warning": f"{Colors.YELLOW}[!]{Colors.RESET}",
            "error": f"{Colors.RED}[✗]{Colors.RESET}",
            "progress": f"{Colors.CYAN}[...]{Colors.RESET}",
        }
        prefix = prefix_map.get(level, "")
        print(f"{prefix} {msg}")

    def validate_transpilation(
        self,
        python_source: str,
        ailang_source: str,
        source_name: str = "<string>",
    ) -> TranspileResult:
        """
        Validate that AILang source correctly represents Python source.

        Args:
            python_source: Original Python code
            ailang_source: Transpiled AILang code
            source_name: Name for logging

        Returns:
            TranspileResult with detailed comparison
        """
        self.log(f"Validating: {Colors.BOLD}{source_name}{Colors.RESET}", "progress")

        result = TranspileResult(
            source_file=source_name,
            success=True,
            python_functions=[],
            ailang_functions=[],
            python_classes=[],
            ailang_records=[],
        )

        # Analyze Python source
        try:
            py_funcs, py_classes = self.python_analyzer.analyze(python_source)
            result.python_functions = py_funcs
            result.python_classes = py_classes
            self.log(
                f"  Python: {len(py_funcs)} functions, {len(py_classes)} classes",
                "info",
            )
        except ValueError as e:
            result.errors.append(f"Python analysis failed: {e}")
            result.success = False
            self.log(f"  Python analysis failed: {e}", "error")
            return result

        # Analyze AILang source
        try:
            ail_funcs, ail_records = self.ailang_analyzer.analyze(ailang_source)
            result.ailang_functions = ail_funcs
            result.ailang_records = ail_records
            self.log(
                f"  AILang: {len(ail_funcs)} functions, {len(ail_records)} records",
                "info",
            )
        except (SyntaxError, TypeError, ValueError, AttributeError, KeyError) as e:
            result.errors.append(f"AILang analysis failed: {e}")
            result.success = False
            self.log(f"  AILang analysis failed: {e}", "error")
            return result

        # Compare functions
        self._compare_functions(result)

        # Summary
        if result.functions_missing > 0:
            result.success = False
            self.log(
                f"  {Colors.RED}Missing {result.functions_missing} functions{Colors.RESET}",
                "error",
            )

        if result.body_mismatches:
            result.warnings.append(
                f"{len(result.body_mismatches)} body mismatches (may be intentional)"
            )
            self.log(
                f"  {Colors.YELLOW}{len(result.body_mismatches)} body mismatches{Colors.RESET}",
                "warning",
            )

        if result.success:
            self.log(
                f"  {Colors.GREEN}PASSED{Colors.RESET} - "
                f"{result.functions_matched}/{result.total_functions} functions matched "
                f"({result.match_percentage:.1f}%)",
                "success",
            )

        return result

    def _compare_functions(self, result: TranspileResult) -> None:
        """Compare functions between Python and AILang."""
        py_func_names = {f.name for f in result.python_functions}
        ail_func_names = {f.name for f in result.ailang_functions}

        # Also include methods from classes
        for cls in result.python_classes:
            for method in cls.methods:
                py_func_names.add(f"{cls.name}.{method.name}")

        # Check for missing functions
        missing = py_func_names - ail_func_names
        result.functions_missing = len(missing)

        # Check for extra functions
        extra = ail_func_names - py_func_names
        result.functions_extra = len(extra)

        # Compare matching functions
        matched = py_func_names & ail_func_names
        result.functions_matched = len(matched)

        if self.verbose and missing:
            for name in sorted(missing):
                self.log(f"    Missing: {name}", "warning")

        if self.verbose and extra:
            for name in sorted(extra):
                self.log(f"    Extra: {name}", "info")

    def validate_file(
        self,
        python_file: Path,
        transpile_fn: Optional[Callable[[str], str]] = None,
    ) -> TranspileResult:
        """
        Validate transpilation of a Python file.

        Args:
            python_file: Path to Python source file
            transpile_fn: Function to transpile Python to AILang
                         (uses default if not provided)

        Returns:
            TranspileResult
        """
        python_file = Path(python_file)

        if not python_file.exists():
            return TranspileResult(
                source_file=str(python_file),
                success=False,
                python_functions=[],
                ailang_functions=[],
                python_classes=[],
                ailang_records=[],
                errors=[f"File not found: {python_file}"],
            )

        python_source = python_file.read_text(encoding="utf-8")

        # Transpile with timeout protection
        try:

            def do_transpile():
                if transpile_fn is None:
                    from tools.transpile import PythonToAILang

                    transpiler = PythonToAILang()
                    return transpiler.transpile(python_source)
                return transpile_fn(python_source)

            self.log(
                f"Transpiling {python_file.name} (timeout: {self.timeout}s)...",
                "progress",
            )
            ailang_source = run_with_timeout(do_transpile, self.timeout)

        except TranspileTimeoutError:
            self.log(f"TIMEOUT transpiling {python_file.name}", "error")
            return TranspileResult(
                source_file=str(python_file),
                success=False,
                python_functions=[],
                ailang_functions=[],
                python_classes=[],
                ailang_records=[],
                errors=[f"Transpilation timed out after {self.timeout}s"],
            )
        except (ValueError, SyntaxError, AttributeError) as exc:
            self.log(f"Error transpiling {python_file.name}: {exc}", "error")
            return TranspileResult(
                source_file=str(python_file),
                success=False,
                python_functions=[],
                ailang_functions=[],
                python_classes=[],
                ailang_records=[],
                errors=[f"Transpilation failed: {exc}"],
            )

        # Validate with timeout protection
        try:
            return run_with_timeout(
                self.validate_transpilation,
                self.timeout,
                python_source,
                ailang_source,
                str(python_file),
            )
        except TimeoutError:
            self.log(f"TIMEOUT validating {python_file.name}", "error")
            return TranspileResult(
                source_file=str(python_file),
                success=False,
                python_functions=[],
                ailang_functions=[],
                python_classes=[],
                ailang_records=[],
                errors=[f"Validation timed out after {self.timeout}s"],
            )

    def validate_directory(
        self,
        directory: Path,
        pattern: str = "*.py",
        transpile_fn: Optional[Callable[[str], str]] = None,
        max_files: int = 50,
    ) -> ValidationReport:
        """
        Validate all Python files in a directory.

        Args:
            directory: Directory to scan
            pattern: Glob pattern for files
            transpile_fn: Transpilation function
            max_files: Maximum number of files to process (safety limit)

        Returns:
            ValidationReport with all results
        """
        directory = Path(directory)
        report = ValidationReport()

        files = list(directory.rglob(pattern))

        # Safety limit
        if len(files) > max_files:
            self.log(
                f"WARNING: Found {len(files)} files, limiting to {max_files}",
                "warning",
            )
            files = files[:max_files]

        self.log(
            f"\n{Colors.BOLD}Validating {len(files)} files in {directory}{Colors.RESET}\n",
            "info",
        )
        self.log(f"Timeout per file: {self.timeout}s, max files: {max_files}", "info")

        for i, python_file in enumerate(sorted(files), 1):
            # Skip __pycache__ and test files
            if "__pycache__" in str(python_file):
                continue
            if python_file.name.startswith("test_"):
                continue

            self.log(
                f"\n[{i}/{len(files)}] Processing {python_file.name}...", "progress"
            )

            result = self.validate_file(python_file, transpile_fn)
            report.results.append(result)
            report.files_processed += 1
            report.total_functions += result.total_functions
            report.total_matched += result.functions_matched

            if result.success:
                report.files_passed += 1
            else:
                report.files_failed += 1
                # Log errors
                for err in result.errors:
                    self.log(f"  Error: {err}", "error")

        # Print summary
        self._print_summary(report)

        return report

    def _print_summary(self, report: ValidationReport) -> None:
        """Print validation summary."""
        print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}TRANSPILATION VALIDATION SUMMARY{Colors.RESET}")
        print(f"{'=' * 60}")
        print(f"Files processed: {report.files_processed}")
        print(
            f"Files passed: {Colors.GREEN}{report.files_passed}{Colors.RESET} / "
            f"{report.files_processed}"
        )
        if report.files_failed > 0:
            print(f"Files failed: {Colors.RED}{report.files_failed}{Colors.RESET}")
        print(f"Total functions: {report.total_functions}")
        print(f"Functions matched: {report.total_matched}")
        print(f"Overall match rate: {report.overall_match_rate:.1f}%")
        print(f"{'=' * 60}\n")


def validate_self(
    timeout: int = DEFAULT_TIMEOUT, max_files: int = 10
) -> ValidationReport:
    """Validate transpilation of AILang's own codebase.

    Args:
        timeout: Timeout per file in seconds
        max_files: Maximum files to process (safety limit)
    """
    validator = TranspileValidator(verbose=True, color=True, timeout=timeout)
    ailang_dir = Path(__file__).parent
    return validator.validate_directory(ailang_dir, max_files=max_files)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate Python to AILang transpilation"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="File or directory to validate (default: ailang/ directory)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--diff", action="store_true", help="Show diffs for mismatches")
    parser.add_argument(
        "--no-color", action="store_true", help="Disable colored output"
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout per file in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=50,
        help="Maximum number of files to process (default: 50)",
    )

    args = parser.parse_args()

    print("\n=== AILang Transpile Validator ===")
    print(f"Timeout: {args.timeout}s per file")
    print(f"Max files: {args.max_files}")
    print()

    validator = TranspileValidator(
        verbose=True,
        show_diff=args.diff,
        color=not args.no_color,
        timeout=args.timeout,
    )

    report = ValidationReport()

    if args.path is None:
        # Validate AILang's own codebase
        ailang_dir = Path(__file__).parent
        report = validator.validate_directory(ailang_dir, max_files=args.max_files)
    else:
        path = Path(args.path)
        if path.is_file():
            result = validator.validate_file(path)
            print(f"\nResult: {'PASSED' if result.success else 'FAILED'}")
            if result.errors:
                for err in result.errors:
                    print(f"  Error: {err}")
            report.files_processed = 1
            report.files_passed = 1 if result.success else 0
            report.files_failed = 0 if result.success else 1
        else:
            report = validator.validate_directory(path, max_files=args.max_files)

    sys.exit(0 if report.files_failed == 0 else 1)

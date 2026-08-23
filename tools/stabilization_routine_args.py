from __future__ import annotations

import argparse

try:
    from .stabilization_routine_common import *
except ImportError:
    from stabilization_routine_common import *

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run standard stabilization routine")
    p.add_argument(
        "--label",
        default=None,
        help="Optional snapshot label (default: routine_<timestamp>).",
    )
    p.add_argument("--runs", type=int, default=3, help="Benchmark measured runs")
    p.add_argument("--warmup", type=int, default=1, help="Benchmark warmup runs")
    p.add_argument(
        "--sample-memory",
        action="store_true",
        default=True,
        help="Collect peak RSS while benchmarking sessions (default: on).",
    )
    p.add_argument(
        "--no-sample-memory",
        action="store_false",
        dest="sample_memory",
        help="Disable peak RSS sampling for this routine run.",
    )
    p.add_argument(
        "--check-leaks",
        action="store_true",
        default=True,
        help="Enable benchmark leak gating (default: on).",
    )
    p.add_argument(
        "--no-check-leaks",
        action="store_false",
        dest="check_leaks",
        help="Disable benchmark leak gating for this routine run.",
    )
    p.add_argument(
        "--leak-threshold",
        type=int,
        default=0,
        help="Allowed leak bytes threshold for benchmark leak gate (default: 0).",
    )
    p.add_argument(
        "--case",
        action="append",
        default=[],
        choices=[
            "loop_hash",
            "fib_mix",
            "file_io",
            "dict_ops",
            "records_bench",
            "format_print",
            "format_str_int",
            "format_hex",
            "format_interp",
            "fixed_array_sum",
            "slice_sum",
            "recursive_traversal",
        ],
        help="Optional benchmark case filter (repeatable).",
    )
    p.add_argument(
        "--impl",
        action="append",
        default=[],
        choices=[
            "ailang_jit",
            "ailang_jit_warm",
            "ailang_aot",
            "c23",
            "rust",
            "python",
        ],
        help="Optional implementation filter (repeatable).",
    )
    p.add_argument(
        "--no-compare-prev",
        action="store_true",
        help="Skip auto-compare against previous routine snapshot.",
    )
    p.add_argument(
        "--compare-old-main",
        action="store_true",
        help="Run tools/compare_with_ailang_main.py after routine capture.",
    )
    p.add_argument(
        "--old-main-root",
        default=str(REPO_ROOT.parent / "AILang-main" / "prototype"),
        help="Path to old AILang prototype root for old-main comparison.",
    )
    p.add_argument(
        "--old-main-fuzz-cases",
        type=int,
        default=0,
        help="Differential fuzz cases for old-main comparison (default: 0).",
    )
    p.add_argument(
        "--old-main-fuzz-seed",
        type=int,
        default=1337,
        help="Differential fuzz seed for old-main comparison.",
    )
    p.add_argument(
        "--old-main-perf-ratio-threshold",
        type=float,
        default=1.10,
        help="Perf ratio gate for old-main comparison.",
    )
    p.add_argument(
        "--old-main-perf-abs-ms-threshold",
        type=float,
        default=15.0,
        help="Perf absolute-ms gate for old-main comparison.",
    )
    p.add_argument(
        "--old-main-fail-on-perf-regression",
        action="store_true",
        help="Fail routine when old-main performance gate fails.",
    )
    p.add_argument(
        "--old-main-fail-on-fuzz-mismatch",
        action="store_true",
        help="Fail routine when old-main fuzz gate fails.",
    )
    p.add_argument(
        "--env-check",
        action="store_true",
        default=True,
        help="Run environment capability check (default: on).",
    )
    p.add_argument(
        "--no-env-check",
        action="store_false",
        dest="env_check",
        help="Disable environment capability check stage.",
    )
    p.add_argument(
        "--phase-profile",
        action="store_true",
        default=True,
        help="Capture compiler phase profile report (default: on).",
    )
    p.add_argument(
        "--no-phase-profile",
        action="store_false",
        dest="phase_profile",
        help="Disable compiler phase profile capture.",
    )
    p.add_argument(
        "--phase-profile-backend",
        choices=["c", "llvm"],
        default="c",
        help="Backend compile path for compiler phase profiling.",
    )
    p.add_argument(
        "--phase-profile-source",
        action="append",
        default=[],
        help="Explicit source file for phase profiling (repeatable).",
    )
    p.add_argument(
        "--language-surface-profile",
        action="store_true",
        default=True,
        help="Capture language-surface profile report (default: on).",
    )
    p.add_argument(
        "--no-language-surface-profile",
        action="store_false",
        dest="language_surface_profile",
        help="Disable language-surface profile capture.",
    )
    p.add_argument(
        "--language-surface-wsl-perf",
        action="store_true",
        help="Enable WSL perf sampling in language-surface profile.",
    )
    p.add_argument(
        "--language-surface-wsl-perf-top",
        type=int,
        default=3,
        help="Number of slowest programs to sample with WSL perf.",
    )
    p.add_argument(
        "--durability-stress",
        action="store_true",
        default=True,
        help="Run generated 5s mega durability stress (default: on).",
    )
    p.add_argument(
        "--no-durability-stress",
        action="store_false",
        dest="durability_stress",
        help="Disable durability stress stage.",
    )
    p.add_argument(
        "--durability-ms",
        type=int,
        default=5000,
        help="Durability stress duration in milliseconds.",
    )
    p.add_argument(
        "--durability-leak-threshold",
        type=int,
        default=0,
        help="Leak threshold for durability stress C backend.",
    )
    p.add_argument(
        "--durability-baseline",
        default=str(
            REPO_ROOT / "benchmarks" / "results" / "durability_stress_baseline.json"
        ),
        help="Baseline JSON path for durability before/after comparisons.",
    )
    p.add_argument(
        "--strict-surface-suite",
        action="store_true",
        default=True,
        help="Run strict per-token/per-builtin surface suite (default: on).",
    )
    p.add_argument(
        "--no-strict-surface-suite",
        action="store_false",
        dest="strict_surface_suite",
        help="Disable strict surface suite stage.",
    )
    p.add_argument(
        "--strict-surface-max-builtins",
        type=int,
        default=None,
        help="Optional builtin cap for strict surface suite (smoke mode).",
    )
    p.add_argument(
        "--adapt-teardown",
        action="store_true",
        default=True,
        help="Run ADAPT teardown leak-classification audit (default: on).",
    )
    p.add_argument(
        "--no-adapt-teardown",
        action="store_false",
        dest="adapt_teardown",
        help="Disable ADAPT teardown leak-classification audit stage.",
    )
    p.add_argument(
        "--adapt-root",
        default=str(REPO_ROOT.parent / "ADAPT"),
        help="Path to ADAPT repository root for teardown audit.",
    )
    p.add_argument(
        "--adapt-contract",
        default=str(REPO_ROOT / "benchmarks" / "adapt_teardown_contract.json"),
        help="Leak classification contract JSON path for ADAPT teardown audit.",
    )
    p.add_argument(
        "--adapt-program",
        action="append",
        default=[],
        help="ADAPT .ail path relative to ADAPT root (repeatable).",
    )
    p.add_argument(
        "--adapt-compile-timeout",
        type=int,
        default=900,
        help="Compile timeout (seconds) per ADAPT teardown program.",
    )
    p.add_argument(
        "--adapt-run-timeout",
        type=int,
        default=300,
        help="Runtime timeout (seconds) per ADAPT teardown program.",
    )
    p.add_argument(
        "--package-smoke",
        action="store_true",
        default=False,
        help="Run packaged-binary smoke test stage.",
    )
    p.add_argument(
        "--package-smoke-timeout",
        type=int,
        default=180,
        help="Per-step timeout (seconds) passed to package smoke tool.",
    )
    p.add_argument(
        "--package-smoke-run-jit-json",
        action="store_true",
        default=False,
        help="Require --jit-json status=ok in package smoke stage.",
    )
    p.add_argument(
        "--no-package-smoke-run-jit-json",
        action="store_false",
        dest="package_smoke_run_jit_json",
        help="Skip --jit-json check in package smoke stage.",
    )
    p.add_argument(
        "--variant-recommendation",
        action="store_true",
        default=False,
        help="Generate variant recommendation report from compare/package signals.",
    )
    p.add_argument(
        "--package-extract-smoke",
        action="store_true",
        default=True,
        help="Run packaged-layout extraction smoke stage (default: on).",
    )
    p.add_argument(
        "--no-package-extract-smoke",
        action="store_false",
        dest="package_extract_smoke",
        help="Disable packaged-layout extraction smoke stage.",
    )
    p.add_argument(
        "--release-manifest",
        action="store_true",
        default=True,
        help="Generate artifact hash/toolchain release manifest (default: on).",
    )
    p.add_argument(
        "--no-release-manifest",
        action="store_false",
        dest="release_manifest",
        help="Disable release manifest stage.",
    )
    p.add_argument(
        "--release-checklist",
        action="store_true",
        default=True,
        help="Run release checklist gate stage (default: on).",
    )
    p.add_argument(
        "--no-release-checklist",
        action="store_false",
        dest="release_checklist",
        help="Disable release checklist gate stage.",
    )
    p.add_argument(
        "--full-routine",
        action="store_true",
        help=(
            "Run one-command full routine: benchmark+leaks, old-main compare, "
            "package smoke, and variant recommendation."
        ),
    )
    return p.parse_args()

__all__ = [name for name in globals() if not name.startswith("__")]

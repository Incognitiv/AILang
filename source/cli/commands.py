"""Public CLI command facade."""

from __future__ import annotations

from cli.builtins import _print_builtins
from cli.compilation import (
    _resolve_tool,
    compile_to_native,
    compile_via_c,
    default_emit_c_output_path,
    default_emit_llvm_output_path,
    default_pgo_output_dir,
)
from cli.diagnostics import (
    DIAGNOSTICS_AVAILABLE,
    run_diagnostics,
    run_diagnostics_json,
    run_diagnostics_on_error,
    run_effect_policy_gate,
    run_prepass,
    run_static_analysis,
)
from cli.optimizer_report import report_optimizer
from cli.reports import (
    report_checks,
    report_effect_policy,
    report_ffi,
    report_format,
    report_runtime_needs,
)

__all__ = [
    "DIAGNOSTICS_AVAILABLE",
    "_resolve_tool",
    "run_prepass",
    "run_diagnostics",
    "run_diagnostics_on_error",
    "run_diagnostics_json",
    "compile_via_c",
    "compile_to_native",
    "default_emit_c_output_path",
    "default_emit_llvm_output_path",
    "default_pgo_output_dir",
    "report_checks",
    "report_format",
    "report_optimizer",
    "report_ffi",
    "report_effect_policy",
    "report_runtime_needs",
    "run_effect_policy_gate",
    "run_static_analysis",
    "_print_builtins",
]

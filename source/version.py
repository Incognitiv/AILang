"""AILang version information."""

__version__ = "1.8.0"
__version_info__ = (1, 8, 0)

# Release metadata
RELEASE_NAME = "RVS Serious App Proof"
RELEASE_DATE = "2026-05-15"
CODENAME = "FreeBSD Homecoming"

# Feature flags for this version
FEATURES = {
    "llvm_backend": True,
    "c_transpiler": True,
    "jit_execution": True,
    "aot_compilation": True,
    "safety_checks": True,
    "simd_intrinsics": True,
    "freestanding_support": True,
    "split_ints": True,
    "parse_int": True,
    "chained_field_access": True,
    "sqlite_fast_text_bind": True,
    "jit_opt_levels": True,
    "jit_ir_dump": True,
    "adapt_hot_shape_helpers": True,
    "aot_pgo_hooks": True,
    "llvm_ir_pgo_probe": True,
    "llvm_ir_pgo_hosted": True,
    "link_directives": True,
    "c23_compat_track": True,
    "ffi_object_linking": True,
    "ffi_report": True,
    "ffi_layout_probe": True,
    "target_introspection": True,
    "target_conditional_directives": True,
    "defined_function_callconv": True,
    "c_abi_layout_tests": True,
    "typed_callback_direct_call": True,
    "extern_record_layout_metadata": True,
    "cbind_layout_bindings": True,
    "drop_plan": True,
    "optimizer_report": True,
    "llvm_optimizer_report": True,
    "performance_thresholds": True,
    "erased_gauntlet_baselines": True,
    "complexity_guard": True,
    "virtual_string_fields": True,
    "virtual_array_field_scalarization": True,
    "stack_class_scalarization": True,
    "rvs_serious_app_proof": True,
    "rvs_freebsd_command_bridge": True,
    "rvs_profile_lifecycle_basics": True,
}


def get_version_string() -> str:
    """Return full version string."""
    return f"AILang {__version__} ({RELEASE_NAME})"


def get_feature_status() -> str:
    """Return feature status summary."""
    enabled = sum(1 for v in FEATURES.values() if v)
    return f"{enabled}/{len(FEATURES)} features enabled"

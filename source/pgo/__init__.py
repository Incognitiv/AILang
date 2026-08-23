"""Profile-guided optimization helpers for AILang."""

from __future__ import annotations

from pgo.c_backend import c_pgo_compile_flags
from pgo.llvm_ir import (
    LLVM_PGO_PROFDATA_NAME,
    LLVMProfileMergeError,
    default_ailang_clang_target,
    llvm_pgo_generate_flags,
    llvm_pgo_probe,
    llvm_pgo_use_flags,
    llvm_pgo_use_flags_with_tool,
    merge_llvm_profraw,
)
from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool
from pgo.paths import default_pgo_output_dir, source_identity_tag

__all__ = [
    "LLVM_PGO_PROFDATA_NAME",
    "LLVMProfileMergeError",
    "c_pgo_compile_flags",
    "default_ailang_clang_target",
    "default_pgo_output_dir",
    "llvm_pgo_generate_flags",
    "llvm_pgo_probe",
    "llvm_pgo_use_flags",
    "llvm_pgo_use_flags_with_tool",
    "merge_llvm_profraw",
    "resolve_llvm_tool",
    "same_llvm_root_tool",
    "source_identity_tag",
]

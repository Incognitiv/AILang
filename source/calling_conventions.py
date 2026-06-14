"""Shared lowering helpers for C/LLVM calling convention decorators."""

from __future__ import annotations


def normalized_decorators(raw: object) -> list[str]:
    """Return normalized decorator names without leading @."""
    if not isinstance(raw, list):
        return []
    return [str(item).lstrip("@").lower() for item in raw]


def c_callconv_macro(decorators: list[str]) -> str:
    """Return the C macro prefix for a decorated function signature."""
    if "stdcall" in decorators:
        return "AILANG_STDCALL "
    if "fastcall" in decorators:
        return "AILANG_FASTCALL "
    return ""


def llvm_calling_convention(decorators: list[str]) -> str:
    """Return llvmlite's calling-convention name for function decorators."""
    if "stdcall" in decorators:
        return "x86_stdcallcc"
    if "fastcall" in decorators:
        return "x86_fastcallcc"
    return ""

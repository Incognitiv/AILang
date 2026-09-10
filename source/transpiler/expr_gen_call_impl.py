"""Orchestration for C function-call expression lowering."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from .expr_gen_call_common import build_call_args
from .expr_gen_call_dispatch import (
    dispatch_builtin_and_simd,
    dispatch_callable_io,
    dispatch_scalar_formatting,
)
from .expr_gen_call_early import (
    dispatch_array_call,
    dispatch_sql_call,
    dispatch_string_fastpath,
    dispatch_surface_call,
)
from .expr_gen_call_finalize import finalize_call


def _generate_call(self: Any, node: A.Call) -> str:
    """Generate a function call while preserving the canonical dispatch order."""
    result = dispatch_surface_call(self, node)
    if result is not None:
        return result
    result = dispatch_sql_call(self, node)
    if result is not None:
        return result
    result = dispatch_array_call(self, node)
    if result is not None:
        return result
    result = dispatch_string_fastpath(self, node)
    if result is not None:
        return result

    call_arg_nodes, call_args = build_call_args(self, node)

    result = dispatch_callable_io(self, node, call_args)
    if result is not None:
        return result
    result = dispatch_scalar_formatting(self, node, call_args)
    if result is not None:
        return result
    result = dispatch_builtin_and_simd(self, node, call_args)
    if result is not None:
        return result
    return finalize_call(self, node, call_arg_nodes, call_args)

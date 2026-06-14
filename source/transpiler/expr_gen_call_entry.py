"""Entry point for C call-expression lowering."""

from __future__ import annotations

from parser import ast as A

from transpiler.expr_gen_call_impl import _generate_call as _generate_regular_call
from transpiler.pure_call_hoist import hoisted_pure_call_replacement


def _generate_call(self, node: A.Call) -> str:
    hoisted = hoisted_pure_call_replacement(self, node)
    if hoisted is not None:
        return hoisted
    return _generate_regular_call(self, node)

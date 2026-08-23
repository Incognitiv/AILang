"""Entry point for C call-expression lowering."""

from __future__ import annotations

from parser import ast as A

from transpiler.expr_gen_call_impl import _generate_call as _generate_regular_call


def _generate_call(self, node: A.Call) -> str:
    # Correctness phase: user-function pure-call replacement is disabled until
    # the evaluator models the same parameter/return type contracts as native
    # execution.  The old path could replace a checked ``(): u8`` call with an
    # untyped int64 constant and silently bypass an out-of-range return trap.
    return _generate_regular_call(self, node)

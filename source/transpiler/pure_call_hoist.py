"""Optional C call-expression hoist hooks.

This pass precomputes calls that are provably pure and fully literal at the
call site, for example ``scan_packet(packet)`` when ``packet`` is assigned once
to a string literal.  Unsupported shapes return ``None`` and use normal call
lowering.
"""

from __future__ import annotations

from parser import ast as A
from typing import Any

from transpiler.pure_eval import stable_literal_bindings, try_eval_call


def hoisted_pure_call_replacement(emitter: Any, node: A.Call) -> str | None:
    function_nodes = getattr(emitter, "_function_nodes", {})
    if not function_nodes:
        return None

    body = getattr(emitter, "_current_function_body", []) or []
    bindings = stable_literal_bindings(body)
    value = try_eval_call(function_nodes, node, bindings)
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return f"((int64_t){value}LL)"
    return None

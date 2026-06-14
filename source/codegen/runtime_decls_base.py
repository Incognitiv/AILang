"""Base runtime declaration service helpers for LLVM codegen."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class RuntimeDeclsBase:
    """Common back-reference plumbing and external declaration helper."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        # Legacy code on the codegen side may read cache fields like
        # ``runtime_decls.exp_func`` (which currently lives on
        # ``CodeGen`` itself). Fall through so the service looks
        # transparent during migration. ``_cg`` itself bypasses this
        # via ``object.__getattribute__``.
        return getattr(self._cg, name)

    def _declare_external(self, name: str, func_ty: ir.FunctionType) -> ir.Function:
        """Idempotently declare an external libc/runtime function.

        Multiple codepaths (``get_*`` accessors here, ``_ensure_libc_functions``
        in expr_generator.py, etc.) each used to declare the same external
        function with ``ir.Function(self.module, ..., name)`` -- and llvmlite
        rejects the second registration with DuplicatedNameError. Routing
        every declaration through this helper makes each accessor fall
        back to the existing global if some other path already declared
        it.
        """
        existing = self._cg.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        return ir.Function(self._cg.module, func_ty, name)

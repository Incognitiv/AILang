"""
BigIntRuntime - LLVM-side BigInt representation and implementation lookup.

The backend owns only the representation adapter. BigInt arithmetic must be
provided by AILang code (normally stdlib/core/bigint.ail) and declared during
module import processing. Missing implementation symbols are a compile-time
error; the LLVM backend must never manufacture unresolved ``ailang_bigint_*``
extern declarations as a hidden language runtime.
"""

from __future__ import annotations

from typing import Any

from codegen.codegen_errors import CodeGenError
from llvmlite import ir


class BigIntRuntime:
    """BigInt representation adapter and fail-closed implementation lookup."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def _get_bigint_type(self) -> ir.PointerType:
        """Return the backend ABI representation of an AILang BigInt value."""
        if self._cg._bigint_type is None:
            self._cg._bigint_type = ir.LiteralStructType(
                [
                    ir.IntType(64),
                    ir.IntType(64),
                    ir.IntType(64),
                    ir.IntType(64).as_pointer(),
                ]
            )
        return self._cg._bigint_type.as_pointer()

    def _require_bigint_function(
        self,
        cache_attr: str,
        symbol: str,
        expected_type: ir.FunctionType,
    ) -> ir.Function:
        """Resolve a BigInt operation that was declared from AILang source.

        Import processing forward-declares imported AILang functions before
        function bodies are generated. Therefore a valid BigInt implementation
        is already present in ``CodeGen.functions`` when an ``unbounded``
        operation is lowered. Creating an ``ir.Function`` here would silently
        re-introduce a native runtime dependency, so absence is fatal.
        """
        cached = getattr(self._cg, cache_attr)
        if cached is not None:
            return cached

        candidate = self._cg.functions.get(symbol)
        if candidate is None:
            module_value = self._cg.module.globals.get(symbol)
            if isinstance(module_value, ir.Function):
                candidate = module_value

        if candidate is None:
            raise CodeGenError(
                "unbounded BigInt implementation is unavailable: required "
                f"AILang function '{symbol}' was not imported/compiled. "
                "The LLVM backend will not synthesize a hidden native BigInt "
                "runtime; import the pure-AIL BigInt implementation first."
            )
        if not isinstance(candidate, ir.Function):
            raise CodeGenError(
                f"BigInt symbol '{symbol}' exists but is not an LLVM function"
            )
        if candidate.function_type != expected_type:
            raise CodeGenError(
                f"BigInt function '{symbol}' has incompatible ABI: expected "
                f"{expected_type}, got {candidate.function_type}"
            )

        setattr(self._cg, cache_attr, candidate)
        return candidate

    def _get_bigint_new(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_new_func",
            "ailang_bigint_new",
            ir.FunctionType(bigint_ptr, [ir.IntType(64)]),
        )

    def _get_bigint_from_int(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_from_int_func",
            "ailang_bigint_from_int",
            ir.FunctionType(bigint_ptr, [ir.IntType(64)]),
        )

    def _get_bigint_add(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_add_func",
            "ailang_bigint_add",
            ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr]),
        )

    def _get_bigint_sub(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_sub_func",
            "ailang_bigint_sub",
            ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr]),
        )

    def _get_bigint_mul(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_mul_func",
            "ailang_bigint_mul",
            ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr]),
        )

    def _get_bigint_div(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_div_func",
            "ailang_bigint_div",
            ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr]),
        )

    def _get_bigint_pow(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_pow_func",
            "ailang_bigint_pow",
            ir.FunctionType(bigint_ptr, [bigint_ptr, ir.IntType(64)]),
        )

    def _get_bigint_cmp(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_cmp_func",
            "ailang_bigint_cmp",
            ir.FunctionType(ir.IntType(32), [bigint_ptr, bigint_ptr]),
        )

    def _get_bigint_print(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_print_func",
            "ailang_bigint_print",
            ir.FunctionType(ir.VoidType(), [bigint_ptr]),
        )

    def _get_bigint_digits(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_digits_func",
            "ailang_bigint_digits",
            ir.FunctionType(ir.IntType(64), [bigint_ptr]),
        )

    def _get_bigint_free(self) -> ir.Function:
        bigint_ptr = self._get_bigint_type()
        return self._require_bigint_function(
            "_bigint_free_func",
            "ailang_bigint_free",
            ir.FunctionType(ir.VoidType(), [bigint_ptr]),
        )

    def is_bigint_type(self, llvm_type: ir.Type) -> bool:
        if self._cg._bigint_type is None:
            return False
        return llvm_type == self._cg._bigint_type.as_pointer()

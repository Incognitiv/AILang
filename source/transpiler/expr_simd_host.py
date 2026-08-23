"""Shared host contract for SIMD builtin mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from llvmlite import ir


class _SimdBuiltinsHostMixin:
    """Type contract for mixins that emit SIMD builtins."""

    codegen: Any
    if TYPE_CHECKING:

        @property
        def builder(self) -> ir.IRBuilder: ...

        @property
        def function(self) -> ir.Function: ...

        def cast_value(
            self, _value: Any, _target_type: Any, _signed: bool = True
        ) -> Any: ...

        def ensure_int64(self, _value: Any) -> Any: ...

        def generate_expr(self, _node: Any) -> Any: ...

        def _type_name_to_llvm(self, _name: str) -> Any: ...

        def _get_type_size_bits(self, _llvm_type: Any) -> int: ...

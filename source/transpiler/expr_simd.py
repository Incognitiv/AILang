"""SIMD builtin compatibility shell.

Keeps the public `_SimdBuiltinsMixin` symbol stable while method bodies
live in focused implementation mixins.
"""

from __future__ import annotations

from transpiler.expr_simd_advanced_impl import _SimdBuiltinsAdvancedMixin
from transpiler.expr_simd_core_impl import _SimdBuiltinsCoreMixin


class _SimdBuiltinsMixin(_SimdBuiltinsCoreMixin, _SimdBuiltinsAdvancedMixin):
    """Compatibility mixin composed from split SIMD implementations."""

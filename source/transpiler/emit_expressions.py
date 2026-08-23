"""Expression generation orchestration for LLVM expressions.

This module keeps ``ExprGenerator`` as a thin coordinator and delegates
all node-specific behavior to extracted service objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from llvmlite import ir
from runtime.arena import ArenaGenerator
from transpiler.expr_builtin_channel import ExprBuiltinChannelEmitter
from transpiler.expr_builtin_file import ExprBuiltinFileEmitter
from transpiler.expr_builtin_memory import ExprBuiltinMemoryEmitter
from transpiler.expr_builtin_meta import ExprBuiltinMetaEmitter
from transpiler.expr_builtin_sql import ExprBuiltinSqlEmitter
from transpiler.expr_call_dispatch import ExprBuiltinCallDispatcher
from transpiler.expr_calls import ExprCallEmitter
from transpiler.expr_collections import ExprCollectionEmitter
from transpiler.expr_common import ExprGenError
from transpiler.expr_literals import ExprLiteralEmitter
from transpiler.expr_ops import ExprOpsEmitter
from transpiler.expr_simd import _SimdBuiltinsMixin
from transpiler.expr_system import ExprBuiltinSystemEmitter
from transpiler.expr_threading import ExprBuiltinThreadingEmitter
from transpiler.expr_type_helpers import ExprTypeHelperEmitter

if TYPE_CHECKING:
    from codegen.codegen import CodeGen


class ExprGenerator(_SimdBuiltinsMixin):
    """Coordinator for LLVM expression generation.

    The class keeps one short-lived cache for dispatch and creates dedicated
    service objects for all major expression categories.
    """

    def __init__(self, codegen: CodeGen) -> None:
        self.codegen = codegen
        self._pow_intrinsic: Optional[ir.Function] = None
        self._call_dispatch: Optional[dict] = None
        self.arena_gen = ArenaGenerator(codegen)

        # Cache of {NodeClass: bound visitor method}. Expressions are visited
        # frequently, so this matters for runtime generation throughput.
        self._visit_cache: dict[type, Callable[[Any], Any]] = {}

        self.type_emitter: ExprTypeHelperEmitter = ExprTypeHelperEmitter(self)
        self.literal_emitter: ExprLiteralEmitter = ExprLiteralEmitter(self)
        self.ops_emitter: ExprOpsEmitter = ExprOpsEmitter(self)
        self.meta_emitter: ExprBuiltinMetaEmitter = ExprBuiltinMetaEmitter(self)
        self.call_emitter: ExprCallEmitter = ExprCallEmitter(self)
        self.collection_emitter: ExprCollectionEmitter = ExprCollectionEmitter(self)
        self.file_emitter: ExprBuiltinFileEmitter = ExprBuiltinFileEmitter(self)
        self.call_dispatcher: ExprBuiltinCallDispatcher = ExprBuiltinCallDispatcher(
            self
        )
        self.sql_emitter: ExprBuiltinSqlEmitter = ExprBuiltinSqlEmitter(self)
        self.system_emitter: ExprBuiltinSystemEmitter = ExprBuiltinSystemEmitter(self)
        self.threading_emitter: ExprBuiltinThreadingEmitter = (
            ExprBuiltinThreadingEmitter(self)
        )
        self.memory_emitter: ExprBuiltinMemoryEmitter = ExprBuiltinMemoryEmitter(self)
        self.channel_emitter: ExprBuiltinChannelEmitter = ExprBuiltinChannelEmitter(
            self
        )

    # Convenience accessors -------------------------------------------------

    @property
    def builder(self) -> ir.IRBuilder:
        return self.codegen.current_builder

    @property
    def function(self) -> ir.Function:
        return self.codegen.current_function

    def __getattr__(self, name: str) -> Any:
        """Delegate missing attributes to expression services."""
        services = (
            self.__dict__.get("type_emitter"),
            self.__dict__.get("literal_emitter"),
            self.__dict__.get("ops_emitter"),
            self.__dict__.get("meta_emitter"),
            self.__dict__.get("call_emitter"),
            self.__dict__.get("collection_emitter"),
            self.__dict__.get("file_emitter"),
            self.__dict__.get("sql_emitter"),
            self.__dict__.get("call_dispatcher"),
            self.__dict__.get("system_emitter"),
            self.__dict__.get("threading_emitter"),
            self.__dict__.get("memory_emitter"),
            self.__dict__.get("channel_emitter"),
        )
        for svc in services:
            if svc is None:
                continue
            if any(name in cls.__dict__ for cls in type(svc).mro()):
                return getattr(svc, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    # Dispatch --------------------------------------------------------------

    def generate_expr(self, node):
        cls = type(node)
        visitor = self._visit_cache.get(cls)
        if visitor is None:
            visitor = getattr(self, f"visit_{cls.__name__}", self.generic_visit)
            self._visit_cache[cls] = visitor
        return visitor(node)

    def generic_visit(self, node):
        raise ExprGenError(f"No visit_{type(node).__name__} method for ExprGenerator")

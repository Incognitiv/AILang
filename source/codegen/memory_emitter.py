"""
MemoryEmitter - service for LLVM-side memory helpers.

Phase A5 of the LLVM-side architectural pivot. Lifts memory helper
methods off ``CodeGen`` while preserving legacy call sites via
``CodeGen.__getattr__``.
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class MemoryEmitter:
    """Memory helper methods used by LLVM code generation."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def checked_malloc(self, size: ir.Value, name: str = "ptr") -> ir.Value:
        """Malloc with null check. Exits with error if allocation fails."""
        return self._checked_malloc_with_builder(
            self._cg.current_builder,
            self._cg.current_function,
            size,
            name,
        )

    def _checked_malloc_with_builder(
        self,
        builder: ir.IRBuilder,
        func: ir.Function,
        size: ir.Value,
        name: str = "ptr",
    ) -> ir.Value:
        """Malloc with null check using specified builder/function."""
        malloc = self._cg.get_malloc()
        ptr = builder.call(malloc, [size], name=name)

        null_ptr = ir.Constant(ptr.type, None)
        is_null = builder.icmp_unsigned("==", ptr, null_ptr, name="is_null")

        oom_block = func.append_basic_block("malloc_oom")
        ok_block = func.append_basic_block("malloc_ok")
        builder.cbranch(is_null, oom_block, ok_block)

        builder.position_at_end(oom_block)
        error_msg = self._cg.create_string_constant("Error: Out of memory!\n")
        printf = self._cg.get_printf()
        builder.call(printf, [error_msg])
        self._cg._emit_safety_trap("Out of memory", builder=builder)

        builder.position_at_end(ok_block)
        return ptr

    def string_alloc(self, size: ir.Value, name: str = "str_buf") -> ir.Value:
        """Allocate memory for a string, using arena routing when active."""
        builder = self._cg.current_builder
        func = self._cg.current_function
        if (
            builder is not None
            and func is not None
            and self._cg._request_arena_slot is not None
        ):
            i8_ptr = ir.IntType(8).as_pointer()
            req_arena = builder.load(
                self._cg._request_arena_slot, name="req_arena_load"
            )
            has_req_arena = builder.icmp_unsigned(
                "!=", req_arena, ir.Constant(i8_ptr, None), name="has_req_arena"
            )
            req_block = func.append_basic_block("str_alloc_req_arena")
            fallback_block = func.append_basic_block("str_alloc_fallback")
            merge_block = func.append_basic_block("str_alloc_merge")
            builder.cbranch(has_req_arena, req_block, fallback_block)

            builder.position_at_end(req_block)
            req_ptr = self._cg._arena_gen.arena_alloc(req_arena, size)
            req_end_block = builder.block
            builder.branch(merge_block)

            builder.position_at_end(fallback_block)
            if self._cg._string_arena is not None:
                fallback_ptr = self._cg._arena_gen.arena_alloc(
                    self._cg._string_arena, size
                )
            else:
                fallback_ptr = self.checked_malloc(size, name)
            fallback_end_block = builder.block
            builder.branch(merge_block)

            builder.position_at_end(merge_block)
            out_ptr = builder.phi(i8_ptr, name=name)
            out_ptr.add_incoming(req_ptr, req_end_block)
            out_ptr.add_incoming(fallback_ptr, fallback_end_block)
            return out_ptr

        if self._cg._string_arena is not None:
            return self._cg._arena_gen.arena_alloc(self._cg._string_arena, size)
        return self.checked_malloc(size, name)

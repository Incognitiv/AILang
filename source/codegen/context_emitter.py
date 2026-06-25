"""Context and scope lifecycle helpers for LLVM code generation."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class ContextEmitter:
    """Helpers for active builder/function access and destructor-aware scope tracking."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        # Keep service transparent to legacy ``CodeGen`` callers.
        return getattr(self._cg, name)

    @property
    def current_builder(self) -> ir.IRBuilder:
        """Get current builder, raising error if None."""
        if self._cg.builder is None:
            raise RuntimeError("Builder not initialized")
        return self._cg.builder

    @property
    def current_function(self) -> ir.Function:
        """Get current function, raising error if None."""
        if self._cg.func is None:
            raise RuntimeError("Function not initialized")
        return self._cg.func

    def alloca_in_entry_block(self, llvm_type: ir.Type, name: str) -> ir.Value:
        """
        Create an alloca instruction in the function's entry block.

        This is preferred over mid-function allocas because:
        1. LLVM can compute stack frame size at compile time
        2. SROA/mem2reg works more effectively on entry-block allocas
        3. Better register allocation
        4. No dynamic stack adjustment needed

        LLVM's mem2reg pass prefers allocas in entry block for promotion.
        """
        entry_block = self.current_function.entry_basic_block

        # If we're already in the entry block, allocate directly.
        if self.current_builder.block == entry_block:
            return self.current_builder.alloca(llvm_type, name=name)

        # Insert after existing entry-block allocas/stores.
        insert_before = None
        for instr in entry_block.instructions:
            instr_type = type(instr).__name__
            if instr_type in {"AllocaInstr", "StoreInstr"}:
                continue
            insert_before = instr
            break

        entry_builder = ir.IRBuilder(entry_block)
        if insert_before is not None:
            entry_builder.position_before(insert_before)
        elif entry_block.is_terminated and entry_block.terminator is not None:
            entry_builder.position_before(entry_block.terminator)

        return entry_builder.alloca(llvm_type, name=name)

    # ------------------------------------------------------------------
    # RAII / Destructor support
    # ------------------------------------------------------------------

    def push_scope(self) -> None:
        """Enter a new scope. Track objects for cleanup."""
        self._cg.scope_cleanup_stack.append([])

    def pop_scope(self, skip_names: set[str] | None = None) -> None:
        """Exit scope. Call destructors for all objects in reverse order."""
        if not self._cg.scope_cleanup_stack:
            return

        skip_names = skip_names or set()
        scope_objects = self._cg.scope_cleanup_stack.pop()
        for var_name, class_name, obj_ptr in reversed(scope_objects):
            if var_name in skip_names:
                continue
            destructor = self._cg.class_destructors.get(class_name)
            if destructor is not None:
                self.current_builder.call(destructor, [obj_ptr])

    def register_for_cleanup(
        self, var_name: str, class_name: str, obj_ptr: Any
    ) -> None:
        """Register an object for cleanup when scope exits."""
        if class_name in self._cg.class_destructors and self._cg.scope_cleanup_stack:
            self._cg.scope_cleanup_stack[-1].append((var_name, class_name, obj_ptr))

    def cleanup_all_scopes(self, skip_names: set[str] | None = None) -> None:
        """Cleanup all remaining scopes (for example at function return)."""
        while self._cg.scope_cleanup_stack:
            self.pop_scope(skip_names=skip_names)

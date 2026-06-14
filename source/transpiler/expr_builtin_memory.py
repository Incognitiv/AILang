"""
Memory/arena/dictionary/raw-memory builtins for ``ExprGenerator``.
Extracted from ``emit_expressions.py`` as part of the LLVM expression
refactor.
"""

from __future__ import annotations

from parser.ast import Variable
from typing import Any

from llvmlite import ir
from transpiler.expr_builtin_memory_stack import (
    _builtin_ptr_array,
    _builtin_stack_alloc,
)
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ARG_THIRD, ExprGenError


class ExprBuiltinMemoryEmitter:
    """Memory-related builtins and low-level data helpers."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _coerce_to_i8_ptr(self, value: ir.Value, opname: str) -> ir.Value:
        """Coerce integer/pointer value to raw ``i8*`` pointer."""
        i8_ptr = ir.IntType(8).as_pointer()
        if isinstance(value.type, ir.PointerType):
            if value.type == i8_ptr:
                return value
            return self.builder.bitcast(value, i8_ptr, name=f"{opname}_ptr")
        if isinstance(value.type, ir.IntType):
            as_i64 = value
            if value.type.width < 64:
                as_i64 = self.builder.zext(value, ir.IntType(64), name=f"{opname}_i64")
            elif value.type.width > 64:
                as_i64 = self.builder.trunc(value, ir.IntType(64), name=f"{opname}_i64")
            return self.builder.inttoptr(as_i64, i8_ptr, name=f"{opname}_ptr")
        raise ExprGenError(f"{opname} expects integer or pointer arena handle")

    def _raw_pointer_value(
        self, ptr: ir.Value, name_prefix: str, label: str
    ) -> ir.Value:
        if isinstance(ptr.type, ir.PointerType):
            return self.builder.ptrtoint(
                ptr, ir.IntType(64), name=f"{name_prefix}_{label}"
            )
        return ptr

    def _offset_to_i64(self, offset: ir.Value, name_prefix: str) -> ir.Value:
        if offset.type == ir.IntType(64):
            return offset
        return self.builder.zext(offset, ir.IntType(64), name=f"{name_prefix}_off")

    def _guard_non_null_pointer(self, ptr: ir.Value, name_prefix: str) -> None:
        raw_ptr = self._raw_pointer_value(ptr, name_prefix, "raw")
        is_null = self.builder.icmp_unsigned(
            "==",
            raw_ptr,
            ir.Constant(ir.IntType(64), 0),
            name=f"{name_prefix}_null",
        )
        func = self.builder.block.parent
        null_blk = func.append_basic_block(f"{name_prefix}_null_err")
        ok_blk = func.append_basic_block(f"{name_prefix}_ok")
        self.builder.cbranch(is_null, null_blk, ok_blk)
        self.builder.position_at_end(null_blk)
        fmt = self.codegen.create_string_constant(
            f"Error: null pointer dereference in {name_prefix}\n"
        )
        self.builder.call(self.codegen.get_printf(), [fmt])
        self.codegen._emit_safety_trap(
            f"Null pointer dereference in {name_prefix}",
            builder=self.builder,
        )
        self.builder.position_at_end(ok_blk)

    def _offset_pointer(
        self,
        ptr: ir.Value,
        offset: ir.Value,
        bit_width: int,
        name_prefix: str,
    ) -> ir.Value:
        stride = bit_width // 8
        byte_off = self.builder.mul(
            self._offset_to_i64(offset, name_prefix),
            ir.Constant(ir.IntType(64), stride),
            name=f"{name_prefix}_byteoff",
        )
        raw_ptr = self._raw_pointer_value(ptr, name_prefix, "base")
        addr = self.builder.add(raw_ptr, byte_off, name=f"{name_prefix}_addr")
        return self.builder.inttoptr(
            addr,
            ir.IntType(bit_width).as_pointer(),
            name=f"{name_prefix}_ptr",
        )

    def _builtin_alloc(self, args) -> ir.Value:
        """Allocate bytes of memory: alloc(bytes) -> int (ptr as integer)
        Returns a pointer to allocated memory as an integer.
        Memory should be freed with dealloc() or via RAII destructor.
        Example: ptr = alloc(1024)  // Allocate 1KB
        """
        if len(args) != 1:
            raise ExprGenError("alloc() expects 1 argument (bytes)")
        size = self.generate_expr(args[ARG_FIRST])
        if size.type != ir.IntType(64):
            size = self.builder.zext(size, ir.IntType(64), name="alloc_size")
        # Use checked malloc that exits on OOM
        ptr = self.codegen.checked_malloc(size, "alloc_ptr")
        # Convert pointer to integer for storage in int fields
        ptr_int = self.builder.ptrtoint(ptr, ir.IntType(64), name="alloc_int")
        return ptr_int

    def _builtin_dealloc(self, args) -> ir.Value:
        """Free allocated memory: dealloc(ptr)
        Frees memory previously allocated with alloc().
        Accepts pointer as integer (from alloc()) or raw pointer.
        Example: dealloc(ptr)
        """
        if len(args) != 1:
            raise ExprGenError("dealloc() expects 1 argument (ptr)")
        ptr_val = self.generate_expr(args[ARG_FIRST])
        # Null pointer guard (Active Armor Item 3)
        raw_val = ptr_val
        if isinstance(raw_val.type, ir.PointerType):
            raw_val = self.builder.ptrtoint(raw_val, ir.IntType(64), name="dealloc_raw")
        is_null = self.builder.icmp_unsigned(
            "==",
            raw_val,
            ir.Constant(ir.IntType(64), 0),
            name="dealloc_null",
        )
        func = self.builder.block.parent
        null_blk = func.append_basic_block("dealloc_null_skip")
        ok_blk = func.append_basic_block("dealloc_ok")
        done_blk = func.append_basic_block("dealloc_done")
        self.builder.cbranch(is_null, null_blk, ok_blk)
        # Null case: skip free, jump to done
        self.builder.position_at_end(null_blk)
        self.builder.branch(done_blk)
        # OK case: free the memory
        self.builder.position_at_end(ok_blk)
        # Get or declare free()
        if not hasattr(self.codegen, "free_func") or self.codegen.free_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            free_ty = ir.FunctionType(ir.VoidType(), [void_ptr])
            self.codegen.free_func = ir.Function(self.codegen.module, free_ty, "free")
        ptr = ptr_val
        # Convert integer to pointer if needed
        if isinstance(ptr.type, ir.IntType):
            ptr = self.builder.inttoptr(
                ptr, ir.IntType(8).as_pointer(), name="free_ptr"
            )
        elif ptr.type != ir.IntType(8).as_pointer():
            ptr = self.builder.bitcast(ptr, ir.IntType(8).as_pointer(), name="free_ptr")
        self.builder.call(self.codegen.free_func, [ptr])
        self.builder.branch(done_blk)
        # Merge at done block
        self.builder.position_at_end(done_blk)
        # Null the source variable to prevent use-after-free (Ada-style)
        # If the argument was a variable, store 0 back into its alloca
        if isinstance(args[ARG_FIRST], Variable):
            var_name = args[ARG_FIRST].name
            if var_name in self.codegen.locals:
                var_ptr = self.codegen.locals[var_name]
                if hasattr(var_ptr, "type") and hasattr(var_ptr.type, "pointee"):
                    pointee = var_ptr.type.pointee
                    if isinstance(pointee, ir.PointerType):
                        zero_val = ir.Constant(pointee, None)
                    else:
                        zero_val = ir.Constant(pointee, 0)
                    self.builder.store(zero_val, var_ptr)
        return ir.Constant(ir.IntType(64), 0)

    # ------------------------------------------------------------------
    # Arena Allocator Builtins (zero-overhead bump allocation)
    # ------------------------------------------------------------------
    def _builtin_arena_create(self, args) -> ir.Value:
        """Create arena: arena_create(size) -> arena_ptr
        Creates a new arena with the given size in bytes.
        Use for scoped allocations that can be bulk-freed.
        Example:
            arena = arena_create(1024 * 1024)  # 1MB arena
        """
        if len(args) != 1:
            raise ExprGenError("arena_create() expects 1 argument (size)")
        size = self.generate_expr(args[ARG_FIRST])
        if not isinstance(size.type, ir.IntType):
            raise ExprGenError("arena_create() size must be an integer")
        # Ensure i64 for size
        if size.type.width != 64:
            size = self.builder.zext(size, ir.IntType(64), name="arena_size")
        return self.arena_gen.create_arena(size)

    def _builtin_arena_alloc(self, args) -> ir.Value:
        """Bump allocate from arena: arena_alloc(arena, size) -> ptr
        Ultra-fast allocation: just bumps a pointer (2 instructions).
        No individual free possible - use arena_reset() or arena_destroy().
        Example:
            ptr = arena_alloc(arena, 64)  # Allocate 64 bytes
        """
        if len(args) != 2:
            raise ExprGenError("arena_alloc() expects 2 arguments (arena, size)")
        arena = self.generate_expr(args[ARG_FIRST])
        arena = self._coerce_to_i8_ptr(arena, "arena_alloc")
        size = self.generate_expr(args[ARG_SECOND])
        # Ensure i64 for size
        if isinstance(size.type, ir.IntType) and size.type.width != 64:
            size = self.builder.zext(size, ir.IntType(64), name="alloc_size")
        return self.arena_gen.arena_alloc(arena, size)

    def _builtin_arena_reset(self, args) -> ir.Value:
        """Reset arena to empty: arena_reset(arena)
        O(1) operation - just resets the bump pointer.
        All previous allocations are invalidated but memory is not freed.
        Perfect for frame allocators in games.
        Example:
            arena_reset(arena)  # All ptrs from this arena now invalid
        """
        if len(args) != 1:
            raise ExprGenError("arena_reset() expects 1 argument (arena)")
        arena = self.generate_expr(args[ARG_FIRST])
        arena = self._coerce_to_i8_ptr(arena, "arena_reset")
        self.arena_gen.arena_reset(arena)
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_arena_destroy(self, args) -> ir.Value:
        """Destroy arena and free all memory: arena_destroy(arena)
        Frees the entire arena block. Use when done with the arena.
        Example:
            arena_destroy(arena)
        """
        if len(args) != 1:
            raise ExprGenError("arena_destroy() expects 1 argument (arena)")
        arena = self.generate_expr(args[ARG_FIRST])
        arena_ptr = self._coerce_to_i8_ptr(arena, "arena_destroy")
        self.arena_gen.arena_destroy(arena_ptr)
        if self.codegen._request_arena_slot is not None:
            i8_ptr = ir.IntType(8).as_pointer()
            active_ptr = self.builder.load(
                self.codegen._request_arena_slot, name="active_req_arena"
            )
            is_active = self.builder.icmp_unsigned(
                "==", active_ptr, arena_ptr, name="destroy_active_req_arena"
            )
            with self.builder.if_then(is_active):
                self.builder.store(
                    ir.Constant(i8_ptr, None), self.codegen._request_arena_slot
                )
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_arena_used(self, args) -> ir.Value:
        """Get bytes used in arena: arena_used(arena) -> int
        Returns how many bytes have been allocated from the arena.
        Example:
            used = arena_used(arena)
        """
        if len(args) != 1:
            raise ExprGenError("arena_used() expects 1 argument (arena)")
        arena = self.generate_expr(args[ARG_FIRST])
        arena = self._coerce_to_i8_ptr(arena, "arena_used")
        return self.arena_gen.arena_used(arena)

    def _builtin_arena_remaining(self, args) -> ir.Value:
        """Get bytes remaining in arena: arena_remaining(arena) -> int
        Returns how many bytes are left before the arena is full.
        Example:
            remaining = arena_remaining(arena)
        """
        if len(args) != 1:
            raise ExprGenError("arena_remaining() expects 1 argument (arena)")
        arena = self.generate_expr(args[ARG_FIRST])
        arena = self._coerce_to_i8_ptr(arena, "arena_remaining")
        return self.arena_gen.arena_remaining(arena)

    def _builtin_arena_use(self, args) -> ir.Value:
        """Select active request arena: arena_use(arena_handle) -> int.

        Stores the selected arena handle into the function-local routing slot.
        Subsequent string_alloc() calls route through this arena until changed.
        Pass 0 / null pointer to clear routing.
        """
        if len(args) != 1:
            raise ExprGenError("arena_use() expects 1 argument (arena_handle)")
        if self.codegen._request_arena_slot is None:
            raise ExprGenError("arena_use() is not available in this context")
        arena_val = self.generate_expr(args[ARG_FIRST])
        arena_ptr = self._coerce_to_i8_ptr(arena_val, "arena_use")
        self.builder.store(arena_ptr, self.codegen._request_arena_slot)
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_dict_has_key(self, args) -> ir.Value:
        """Check if dict has key: dict_has_key(dict, key) -> bool
        Returns 1 if key exists in dict, 0 otherwise.
        Fixes bug M4: dict[key] returns 0 for both "key not found" and "key has value 0".
        Example:
            if dict_has_key(mydict, "name") then
                name = mydict["name"]
            end
        """
        if len(args) != 2:
            raise ExprGenError("dict_has_key() expects 2 arguments (dict, key)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        key_val = self.generate_expr(args[ARG_SECOND])
        has_key_fn = self.codegen.get_dict_has_key_func()
        result = self.builder.call(has_key_fn, [dict_ptr, key_val], name="has_key")
        return result

    def _builtin_dict_new(self, args) -> ir.Value:
        """Create an empty dictionary."""
        if args:
            raise ExprGenError("dict_new() expects no arguments")
        create_fn = self.codegen.get_dict_create_func()
        return self.builder.call(
            create_fn, [ir.Constant(ir.IntType(64), 16)], name="dict_new"
        )

    def _builtin_dict_size(self, args) -> ir.Value:
        """Get number of entries in dict: dict_size(d) -> int
        Reads the size field from the dict struct.
        Example:
            d = {"name": "AILang", "version": "1"}
            print(dict_size(d))  // 2
        """
        if len(args) != 1:
            raise ExprGenError("dict_size() expects 1 argument (dict)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        size_fn = self.codegen.get_dict_size_func()
        return self.builder.call(size_fn, [dict_ptr], name="dict_size")

    def _builtin_dict_key_at(self, args) -> ir.Value:
        """Get key at index: dict_key_at(d, i) -> string
        Returns the key string at position i (0-indexed).
        Used for iterating dict entries:
            i = 0
            while i < dict_size(d) then
                key = dict_key_at(d, i)
                val = d[key]
                i = i + 1
            end
        """
        if len(args) != 2:
            raise ExprGenError("dict_key_at() expects 2 arguments (dict, index)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        index = self.generate_expr(args[ARG_SECOND])
        key_at_fn = self.codegen.get_dict_key_at_func()
        return self.builder.call(key_at_fn, [dict_ptr, index], name="dict_key")

    def _builtin_dict_value_at(self, args) -> ir.Value:
        """Get value at index: dict_value_at(d, i) -> int
        Returns the raw i64 value at position i.
        """
        if len(args) != 2:
            raise ExprGenError("dict_value_at() expects 2 arguments (dict, index)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        index = self.generate_expr(args[ARG_SECOND])
        val_at_fn = self.codegen.get_dict_value_at_func()
        return self.builder.call(val_at_fn, [dict_ptr, index], name="dict_val")

    def _builtin_dict_remove(self, args) -> ir.Value:
        """Remove key from dict: dict_remove(d, key) -> int
        Returns 1 if key was removed, 0 if not found.
        Shifts remaining entries to fill the gap.
        """
        if len(args) != 2:
            raise ExprGenError("dict_remove() expects 2 arguments (dict, key)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        key_val = self.generate_expr(args[ARG_SECOND])
        remove_fn = self.codegen.get_dict_remove_func()
        return self.builder.call(remove_fn, [dict_ptr, key_val], name="dict_removed")

    def _builtin_dict_get_type(self, args) -> ir.Value:
        """Get type tag for dict entry: dict_get_type(d, key) -> int
        Returns the type tag for the value associated with key.
        Type tags: 0=int, 1=float, 2=string, 3=bool, 4=pointer, 255=not found
        Example:
            d = {"name": "Alice", "age": 30}
            t = dict_get_type(d, "name")  // 2 (string)
            t = dict_get_type(d, "age")   // 0 (int)
        """
        if len(args) != 2:
            raise ExprGenError("dict_get_type() expects 2 arguments (dict, key)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        key_val = self.generate_expr(args[ARG_SECOND])
        type_fn = self.codegen.get_dict_get_type_func()
        tag = self.builder.call(type_fn, [dict_ptr, key_val], name="dict_type_tag")
        return self.builder.zext(tag, ir.IntType(64), name="dict_type")

    def _builtin_dict_get_string(self, args) -> ir.Value:
        """Get string value from dict: dict_get_string(d, key) -> string
        Returns the string associated with key. The dict stores string
        pointers as i64 via ptrtoint; this converts back to i8* (char*).
        Example:
            d = {"name": "Alice"}
            s = dict_get_string(d, "name")  // "Alice"
        """
        if len(args) != 2:
            raise ExprGenError("dict_get_string() expects 2 arguments (dict, key)")
        dict_ptr = self.generate_expr(args[ARG_FIRST])
        key_val = self.generate_expr(args[ARG_SECOND])
        get_fn = self.codegen.get_dict_get_func()
        val_i64 = self.builder.call(get_fn, [dict_ptr, key_val], name="dict_str_i64")
        char_ptr = ir.IntType(8).as_pointer()
        return self.builder.inttoptr(val_i64, char_ptr, name="dict_str_ptr")

    def _builtin_peek64(self, args) -> ir.Value:
        """Read i64 from ptr+offset: peek64(ptr, offset) -> i64
        Reads a 64-bit integer from memory at ptr + offset*8.
        Accepts pointer as integer (from alloc()) or raw pointer.
        Includes null pointer guard (Active Armor Item 3).
        Example: val = peek64(buffer, 5)  // Read 6th element
        """
        if len(args) != 2:
            raise ExprGenError("peek64() expects 2 arguments (ptr, offset)")
        ptr = self.generate_expr(args[ARG_FIRST])
        offset = self.generate_expr(args[ARG_SECOND])
        if offset.type != ir.IntType(64):
            offset = self.builder.zext(offset, ir.IntType(64), name="peek_off")
        # Null pointer guard
        raw_ptr = ptr
        if isinstance(raw_ptr.type, ir.PointerType):
            raw_ptr = self.builder.ptrtoint(raw_ptr, ir.IntType(64), name="peek_raw")
        is_null = self.builder.icmp_unsigned(
            "==", raw_ptr, ir.Constant(ir.IntType(64), 0), name="peek_null"
        )
        func = self.builder.block.parent
        null_blk = func.append_basic_block("peek_null_err")
        ok_blk = func.append_basic_block("peek_ok")
        self.builder.cbranch(is_null, null_blk, ok_blk)
        self.builder.position_at_end(null_blk)
        fmt = self.codegen.create_string_constant(
            "Error: null pointer dereference in peek64\n"
        )
        self.builder.call(self.codegen.get_printf(), [fmt])
        self.codegen._emit_safety_trap(
            "Null pointer dereference in peek64",
            builder=self.builder,
        )
        self.builder.position_at_end(ok_blk)
        # Convert integer to pointer if needed
        i64_ptr_type = ir.IntType(64).as_pointer()
        if isinstance(ptr.type, ir.IntType):
            ptr = self.builder.inttoptr(ptr, i64_ptr_type, name="peek_ptr")
        elif ptr.type != i64_ptr_type:
            ptr = self.builder.bitcast(ptr, i64_ptr_type, name="peek_ptr")
        # GEP to offset
        elem_ptr = self.builder.gep(ptr, [offset], name="peek_elem_ptr")
        # Load value
        val = self.builder.load(elem_ptr, name="peek64_val")
        return val

    def _builtin_poke64(self, args) -> ir.Value:
        """Write i64 to ptr+offset: poke64(ptr, offset, value)
        Writes a 64-bit integer to memory at ptr + offset*8.
        Accepts pointer as integer (from alloc()) or raw pointer.
        Includes null pointer guard (Active Armor Item 3).
        Example: poke64(buffer, 5, 42)  // Write 42 to 6th element
        """
        if len(args) != 3:
            raise ExprGenError("poke64() expects 3 arguments (ptr, offset, value)")
        ptr = self.generate_expr(args[ARG_FIRST])
        offset = self.generate_expr(args[ARG_SECOND])
        value = self.generate_expr(args[ARG_THIRD])
        if offset.type != ir.IntType(64):
            offset = self.builder.zext(offset, ir.IntType(64), name="poke_off")
        if value.type != ir.IntType(64) and isinstance(value.type, ir.IntType):
            value = self.builder.sext(value, ir.IntType(64), name="poke_val")
        # Null pointer guard
        raw_ptr = ptr
        if isinstance(raw_ptr.type, ir.PointerType):
            raw_ptr = self.builder.ptrtoint(raw_ptr, ir.IntType(64), name="poke_raw")
        is_null = self.builder.icmp_unsigned(
            "==",
            raw_ptr,
            ir.Constant(ir.IntType(64), 0),
            name="poke_null",
        )
        func = self.builder.block.parent
        null_blk = func.append_basic_block("poke_null_err")
        ok_blk = func.append_basic_block("poke_ok")
        self.builder.cbranch(is_null, null_blk, ok_blk)
        self.builder.position_at_end(null_blk)
        fmt = self.codegen.create_string_constant(
            "Error: null pointer dereference in poke64\n"
        )
        self.builder.call(self.codegen.get_printf(), [fmt])
        self.codegen._emit_safety_trap(
            "Null pointer dereference in poke64",
            builder=self.builder,
        )
        self.builder.position_at_end(ok_blk)
        # Convert integer to pointer if needed
        i64_ptr_type = ir.IntType(64).as_pointer()
        if isinstance(ptr.type, ir.IntType):
            ptr = self.builder.inttoptr(ptr, i64_ptr_type, name="poke_ptr")
        elif ptr.type != i64_ptr_type:
            ptr = self.builder.bitcast(ptr, i64_ptr_type, name="poke_ptr")
        # GEP to offset
        elem_ptr = self.builder.gep(ptr, [offset], name="poke_elem_ptr")
        # Store value
        self.builder.store(value, elem_ptr)
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_peek_generic(
        self, args: list, bit_width: int, name_prefix: str
    ) -> ir.Value:
        """Generic peek: read N-bit value from ptr+offset.
        bit_width: 8, 32, or 64
        Stride is bit_width/8 bytes per element.
        """
        if len(args) != 2:
            raise ExprGenError(f"{name_prefix}() expects 2 arguments (ptr, offset)")
        ptr = self.generate_expr(args[ARG_FIRST])
        offset = self.generate_expr(args[ARG_SECOND])
        self._guard_non_null_pointer(ptr, name_prefix)
        typed_ptr = self._offset_pointer(ptr, offset, bit_width, name_prefix)
        val = self.builder.load(typed_ptr, name=f"{name_prefix}_val")
        # Zero-extend to i64 for AILang's int type
        if bit_width < 64:
            val = self.builder.zext(val, ir.IntType(64), name=f"{name_prefix}_ext")
        return val

    def _builtin_poke_generic(
        self, args: list, bit_width: int, name_prefix: str
    ) -> ir.Value:
        """Generic poke: write N-bit value to ptr+offset.
        bit_width: 8, 32, or 64
        Stride is bit_width/8 bytes per element.
        """
        if len(args) != 3:
            raise ExprGenError(
                f"{name_prefix}() expects 3 arguments (ptr, offset, value)"
            )
        ptr = self.generate_expr(args[ARG_FIRST])
        offset = self.generate_expr(args[ARG_SECOND])
        value = self.generate_expr(args[ARG_THIRD])
        self._guard_non_null_pointer(ptr, name_prefix)
        typed_ptr = self._offset_pointer(ptr, offset, bit_width, name_prefix)
        # Truncate value to target bit width
        if isinstance(value.type, ir.IntType) and value.type.width > bit_width:
            value = self.builder.trunc(
                value, ir.IntType(bit_width), name=f"{name_prefix}_trunc"
            )
        elif isinstance(value.type, ir.IntType) and value.type.width < bit_width:
            value = self.builder.zext(
                value, ir.IntType(bit_width), name=f"{name_prefix}_ext"
            )
        self.builder.store(value, typed_ptr)
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_peek32(self, args: list) -> ir.Value:
        """Read uint32 from ptr+offset*4: peek32(ptr, offset) -> int"""
        return self._builtin_peek_generic(args, 32, "peek32")

    def _builtin_poke32(self, args: list) -> ir.Value:
        """Write uint32 to ptr+offset*4: poke32(ptr, offset, value)"""
        return self._builtin_poke_generic(args, 32, "poke32")

    def _builtin_peek8(self, args: list) -> ir.Value:
        """Read uint8 from ptr+offset: peek8(ptr, offset) -> int"""
        return self._builtin_peek_generic(args, 8, "peek8")

    def _builtin_poke8(self, args: list) -> ir.Value:
        """Write uint8 to ptr+offset: poke8(ptr, offset, value)"""
        return self._builtin_poke_generic(args, 8, "poke8")

    def _builtin_addressof(self, args: list) -> ir.Value:
        """Get address of a variable as int: addressof(var) -> int"""
        if len(args) != 1:
            raise ExprGenError("addressof() expects 1 argument")
        # The argument should be a Variable node          get its alloca
        arg = args[ARG_FIRST]
        if hasattr(arg, "name") and arg.name in self.codegen.locals:
            alloca = self.codegen.locals[arg.name]
            if isinstance(alloca.type, ir.PointerType):
                return self.builder.ptrtoint(alloca, ir.IntType(64), name="addrof")
        # Fallback: evaluate and return as-is
        val = self.generate_expr(arg)
        return val

    def _builtin_memcpy(self, args: list) -> ir.Value:
        """Raw memory copy: memcpy(dst, src, nbytes)"""
        if len(args) != 3:
            raise ExprGenError("memcpy() expects 3 arguments (dst, src, nbytes)")
        dst = self.generate_expr(args[ARG_FIRST])
        src = self.generate_expr(args[ARG_SECOND])
        nbytes = self.generate_expr(args[ARG_THIRD])
        # Convert int to pointer
        i8_ptr = ir.IntType(8).as_pointer()
        if isinstance(dst.type, ir.IntType):
            dst = self.builder.inttoptr(dst, i8_ptr, name="mcpy_dst")
        if isinstance(src.type, ir.IntType):
            src = self.builder.inttoptr(src, i8_ptr, name="mcpy_src")
        if nbytes.type != ir.IntType(64):
            nbytes = self.builder.zext(nbytes, ir.IntType(64), name="mcpy_n")
        # Declare memcpy if not already
        memcpy_name = "llvm.memcpy.p0i8.p0i8.i64"
        if memcpy_name not in self.codegen.functions:
            memcpy_ty = ir.FunctionType(
                ir.VoidType(),
                [i8_ptr, i8_ptr, ir.IntType(64), ir.IntType(1)],
            )
            memcpy_fn = ir.Function(self.codegen.module, memcpy_ty, name=memcpy_name)
            self.codegen.functions[memcpy_name] = memcpy_fn
        memcpy_fn = self.codegen.functions[memcpy_name]
        self.builder.call(
            memcpy_fn,
            [dst, src, nbytes, ir.Constant(ir.IntType(1), 0)],
        )
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_memset(self, args: list) -> ir.Value:
        """Raw memory set: memset(dest, value, nbytes)"""
        if len(args) != 3:
            raise ExprGenError("memset() expects 3 arguments (dest, value, nbytes)")
        dest = self.generate_expr(args[ARG_FIRST])
        val = self.generate_expr(args[ARG_SECOND])
        nbytes = self.generate_expr(args[ARG_THIRD])
        i8_ptr = ir.IntType(8).as_pointer()
        if isinstance(dest.type, ir.IntType) and dest.type.width != 8:
            dest = self.builder.inttoptr(dest, i8_ptr, name="mset_dst")
        if val.type != ir.IntType(8):
            val = self.builder.trunc(val, ir.IntType(8), name="mset_val")
        if nbytes.type != ir.IntType(64):
            nbytes = self.builder.zext(nbytes, ir.IntType(64), name="mset_n")
        memset_name = "llvm.memset.p0i8.i64"
        if memset_name not in self.codegen.functions:
            memset_ty = ir.FunctionType(
                ir.VoidType(),
                [i8_ptr, ir.IntType(8), ir.IntType(64), ir.IntType(1)],
            )
            memset_fn = ir.Function(self.codegen.module, memset_ty, name=memset_name)
            self.codegen.functions[memset_name] = memset_fn
        memset_fn = self.codegen.functions[memset_name]
        self.builder.call(
            memset_fn,
            [dest, val, nbytes, ir.Constant(ir.IntType(1), 0)],
        )
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_memmove(self, args: list) -> ir.Value:
        """Raw memory move: memmove(dest, src, nbytes)          handles overlapping."""
        if len(args) != 3:
            raise ExprGenError("memmove() expects 3 arguments (dest, src, nbytes)")
        dest = self.generate_expr(args[ARG_FIRST])
        src = self.generate_expr(args[ARG_SECOND])
        nbytes = self.generate_expr(args[ARG_THIRD])
        i8_ptr = ir.IntType(8).as_pointer()
        if isinstance(dest.type, ir.IntType):
            dest = self.builder.inttoptr(dest, i8_ptr, name="mmov_dst")
        if isinstance(src.type, ir.IntType):
            src = self.builder.inttoptr(src, i8_ptr, name="mmov_src")
        if nbytes.type != ir.IntType(64):
            nbytes = self.builder.zext(nbytes, ir.IntType(64), name="mmov_n")
        memmove_name = "llvm.memmove.p0i8.p0i8.i64"
        if memmove_name not in self.codegen.functions:
            memmove_ty = ir.FunctionType(
                ir.VoidType(),
                [i8_ptr, i8_ptr, ir.IntType(64), ir.IntType(1)],
            )
            memmove_fn = ir.Function(self.codegen.module, memmove_ty, name=memmove_name)
            self.codegen.functions[memmove_name] = memmove_fn
        memmove_fn = self.codegen.functions[memmove_name]
        self.builder.call(
            memmove_fn,
            [dest, src, nbytes, ir.Constant(ir.IntType(1), 0)],
        )
        return ir.Constant(ir.IntType(64), 0)

    # Stack allocation helpers live in expr_builtin_memory_stack.py.
    _builtin_ptr_array = _builtin_ptr_array
    _builtin_stack_alloc = _builtin_stack_alloc

    def _builtin_realloc(self, args: list) -> ir.Value:
        """Reallocate memory: realloc(ptr, new_size) -> new_ptr as i64."""
        if len(args) != 2:
            raise ExprGenError("realloc() expects 2 arguments (ptr, new_size)")
        ptr_val = self.generate_expr(args[ARG_FIRST])
        new_size = self.generate_expr(args[ARG_SECOND])
        i8_ptr = ir.IntType(8).as_pointer()
        if isinstance(ptr_val.type, ir.IntType):
            ptr_val = self.builder.inttoptr(ptr_val, i8_ptr, name="realloc_p")
        if new_size.type != ir.IntType(64):
            new_size = self.builder.zext(new_size, ir.IntType(64), name="realloc_sz")
        realloc_name = "realloc"
        if realloc_name not in self.codegen.functions:
            realloc_ty = ir.FunctionType(i8_ptr, [i8_ptr, ir.IntType(64)])
            realloc_fn = ir.Function(self.codegen.module, realloc_ty, name=realloc_name)
            realloc_fn.linkage = "external"
            self.codegen.functions[realloc_name] = realloc_fn
        realloc_fn = self.codegen.functions[realloc_name]
        result = self.builder.call(realloc_fn, [ptr_val, new_size], name="realloc_r")
        return self.builder.ptrtoint(result, ir.IntType(64), name="realloc_i")

    def _builtin_calloc(self, args: list) -> ir.Value:
        """Allocate zero-filled memory: calloc(count, size) -> i64 pointer."""
        if len(args) != 2:
            raise ExprGenError("calloc() expects 2 arguments (count, size)")
        count = self.generate_expr(args[ARG_FIRST])
        size = self.generate_expr(args[ARG_SECOND])
        if count.type != ir.IntType(64):
            count = self.builder.zext(count, ir.IntType(64), name="calloc_count")
        if size.type != ir.IntType(64):
            size = self.builder.zext(size, ir.IntType(64), name="calloc_size")
        # Zero-sized allocation is legal and returns nullptr in libc.
        i8_ptr = ir.IntType(8).as_pointer()
        calloc_name = "calloc"
        if calloc_name not in self.codegen.functions:
            calloc_ty = ir.FunctionType(i8_ptr, [ir.IntType(64), ir.IntType(64)])
            calloc_fn = ir.Function(self.codegen.module, calloc_ty, name=calloc_name)
            calloc_fn.linkage = "external"
            self.codegen.functions[calloc_name] = calloc_fn
        calloc_fn = self.codegen.functions[calloc_name]
        # Preserve legacy behavior if count*size overflows by truncating at i64.
        result = self.builder.call(calloc_fn, [count, size], name="calloc_r")
        return self.builder.ptrtoint(result, ir.IntType(64), name="calloc_i")

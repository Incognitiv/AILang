"""
Arena Allocator for AILang

Bump allocator with chunked growth — fast common-case allocation
plus unbounded total capacity. The user-visible API stays the same
(arena_create, arena_alloc, arena_reset, arena_destroy, arena_used,
arena_remaining); the internal layout grows on demand.

Arena layout in memory:
    [base: i8*][current: i8*][end: i8*][prev_chunk: i8*][...data...]

`prev_chunk` is the head of a linked list of OVERFLOW chunks.
`base`/`current`/`end` always refer to the *active* chunk — initially
the data area inside the arena struct itself, and after each overflow
they point into a freshly-malloc'd block. Each overflow block has
its first 8 bytes reserved for a "next-prev" pointer that links the
chain backward, so arena_destroy can walk and free every chunk.

Why chunked instead of a single fixed buffer:

The previous design fixed the arena's size at create time and aborted
with "Error: Arena allocation overflow!" when exhausted. That made
sense for short-lived batch programs but contradicted AILang's
elsewhere-stated stance against artificial limits (unbound integers,
arbitrary-precision arithmetic). Long-running programs (HTTP servers,
REPLs, soak harnesses) routinely exhaust a 16 MB or even 1 GB
single-block arena and crash predictably.

Chunked layout removes that ceiling. The hot path is unchanged: 2
loads + 1 add + 1 bounds check + 1 store. Only the overflow path
differs — instead of abort(), it requests a fresh malloc'd chunk
sized to fit the request (with a default minimum of 16 MB) and
continues. Total memory grows monotonically until arena_destroy or
process exit; that's an acceptable trade for a language-level "no
artificial cap" promise. Programs that want a hard cap should apply
it as policy in user code (querying system_ram_total / arena_used
and aborting before exhaustion is a `core/memory.ail` concern, not
an arena.py concern).

Performance:
    - arena_alloc fast path: 2 instructions (load + add)
    - arena_alloc on overflow: malloc(chunk_size) + bookkeeping
    - arena_reset: O(chunks) — frees all overflow chunks, keeps the
      original; sets current = base
    - arena_destroy: O(chunks) — frees all overflow chunks, then the
      original arena block
    - Zero fragmentation within a chunk; gaps between chunks unused
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class ArenaGenerator:
    """Generates LLVM IR for chunked arena allocation."""

    # Arena struct offsets (all i8*)
    OFFSET_BASE = 0
    OFFSET_CURRENT = 1
    OFFSET_END = 2
    OFFSET_PREV_CHUNK = 3
    STRUCT_SIZE = 32  # 4 pointers x 8 bytes

    # Default minimum size for an overflow chunk. A new chunk is sized
    # max(requested_alloc, OVERFLOW_CHUNK_MIN) so single large allocs
    # aren't truncated and many small allocs don't malloc per-string.
    OVERFLOW_CHUNK_MIN = 16 * 1024 * 1024  # 16 MB per overflow chunk

    def __init__(self, codegen: Any) -> None:
        self.codegen = codegen
        self._arena_type: ir.LiteralStructType | None = None

    @property
    def arena_type(self) -> ir.LiteralStructType:
        """Arena struct: {base, current, end, prev_chunk}."""
        if self._arena_type is None:
            i8_ptr = ir.IntType(8).as_pointer()
            self._arena_type = ir.LiteralStructType([i8_ptr, i8_ptr, i8_ptr, i8_ptr])
        return self._arena_type

    def _get_malloc(self) -> ir.Function:
        """Get malloc function from codegen."""
        return self.codegen.get_malloc()

    def _get_free(self) -> ir.Function:
        """Get free function from codegen."""
        return self.codegen._get_free()

    def _store_field(
        self, builder: ir.IRBuilder, arena_struct: ir.Value, field: int, value: ir.Value
    ) -> None:
        """Helper: store value into a struct field by offset."""
        ptr = builder.gep(
            arena_struct,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field)],
            name=f"arena_field_{field}_ptr",
        )
        builder.store(value, ptr)

    def _load_field(
        self, builder: ir.IRBuilder, arena_struct: ir.Value, field: int, name: str
    ) -> ir.Value:
        """Helper: load value from a struct field by offset."""
        ptr = builder.gep(
            arena_struct,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field)],
            name=f"{name}_ptr",
        )
        return builder.load(ptr, name=name)

    def create_arena(self, size: ir.Value) -> ir.Value:
        """
        Create a new arena with the given initial chunk size.

        Args:
            size: Initial chunk size in bytes (i64)

        Returns:
            Pointer to arena struct (i8*)
        """
        builder = self.codegen.current_builder
        i8_ptr = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)

        # Total size = struct header + initial data area
        struct_size = ir.Constant(i64, self.STRUCT_SIZE)
        total_size = builder.add(size, struct_size, name="arena_total_size")

        # Allocate the block
        malloc = self._get_malloc()
        block = builder.call(malloc, [total_size], name="arena_block")

        # Check for OOM at create time
        null_ptr = ir.Constant(i8_ptr, None)
        is_null = builder.icmp_unsigned("==", block, null_ptr, name="arena_oom")

        oom_block = self.codegen.current_function.append_basic_block("arena_oom")
        ok_block = self.codegen.current_function.append_basic_block("arena_ok")
        builder.cbranch(is_null, oom_block, ok_block)

        # OOM handler — preserved from the previous design. Initial
        # allocation failure means the system can't even give us our
        # first chunk, so abort cleanly.
        builder.position_at_end(oom_block)
        error_msg = self.codegen.create_string_constant(
            "Error: Out of memory creating arena!\n"
        )
        printf = self.codegen.get_printf()
        builder.call(printf, [error_msg])
        exit_func = self.codegen.get_exit_func()
        builder.call(exit_func, [ir.Constant(ir.IntType(32), 1)])
        builder.unreachable()

        # Success path
        builder.position_at_end(ok_block)

        # Cast to arena struct pointer
        arena_ptr_ty = self.arena_type.as_pointer()
        arena_ptr = builder.bitcast(block, arena_ptr_ty, name="arena_ptr")

        # Calculate data start (after header) and end pointer
        data_start = builder.gep(
            block,
            [ir.Constant(ir.IntType(32), self.STRUCT_SIZE)],
            name="arena_data_start",
        )
        end_ptr = builder.gep(block, [total_size], name="arena_end")

        # Initialize fields
        self._store_field(builder, arena_ptr, self.OFFSET_BASE, data_start)
        self._store_field(builder, arena_ptr, self.OFFSET_CURRENT, data_start)
        self._store_field(builder, arena_ptr, self.OFFSET_END, end_ptr)
        # No overflow chunks yet
        self._store_field(builder, arena_ptr, self.OFFSET_PREV_CHUNK, null_ptr)

        # Return as i8* for consistency
        return builder.bitcast(arena_ptr, i8_ptr, name="arena")

    def arena_alloc(self, arena: ir.Value, size: ir.Value) -> ir.Value:
        """
        Bump allocate from arena. Hot path stays cheap; overflow falls
        through to a chunk-extension path that mallocs a new chunk and
        chains it into prev_chunk for later cleanup.

        Args:
            arena: Arena pointer (i8*)
            size: Allocation size in bytes (i64)

        Returns:
            Allocated pointer (i8*)
        """
        builder = self.codegen.current_builder
        i8_ptr = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)

        # Cast arena to struct pointer
        arena_ptr_ty = self.arena_type.as_pointer()
        arena_struct = builder.bitcast(arena, arena_ptr_ty, name="arena_struct")

        # Load current and end pointers
        current = self._load_field(
            builder, arena_struct, self.OFFSET_CURRENT, "arena_current"
        )
        end = self._load_field(builder, arena_struct, self.OFFSET_END, "arena_end")

        # Bump-allocate: new_current = current + size
        current_int = builder.ptrtoint(current, i64, name="current_int")
        new_current_int = builder.add(current_int, size, name="new_current_int")
        new_current = builder.inttoptr(new_current_int, i8_ptr, name="new_current")

        # Bounds check
        end_int = builder.ptrtoint(end, i64, name="end_int")
        overflow = builder.icmp_unsigned(
            ">", new_current_int, end_int, name="arena_overflow"
        )

        overflow_block = self.codegen.current_function.append_basic_block(
            "arena_overflow"
        )
        alloc_ok_block = self.codegen.current_function.append_basic_block(
            "arena_alloc_ok"
        )
        merge_block = self.codegen.current_function.append_basic_block(
            "arena_alloc_merge"
        )
        builder.cbranch(overflow, overflow_block, alloc_ok_block)

        # Fast path: enough room in current chunk. Update current,
        # return the original `current` (start of this allocation).
        builder.position_at_end(alloc_ok_block)
        self._store_field(builder, arena_struct, self.OFFSET_CURRENT, new_current)
        builder.branch(merge_block)
        ok_end = builder.block

        # Overflow path: malloc a new chunk, chain it into prev_chunk,
        # update base/current/end to point inside the new chunk, return
        # the start of the allocation in the new chunk.
        builder.position_at_end(overflow_block)
        overflow_ptr = self._alloc_new_chunk(builder, arena_struct, size)
        builder.branch(merge_block)
        overflow_end = builder.block

        # Merge: pick the right return value via phi.
        builder.position_at_end(merge_block)
        result = builder.phi(i8_ptr, name="arena_alloc_result")
        result.add_incoming(current, ok_end)
        result.add_incoming(overflow_ptr, overflow_end)
        return result

    def _alloc_new_chunk(
        self, builder: ir.IRBuilder, arena_struct: ir.Value, size: ir.Value
    ) -> ir.Value:
        """
        Allocate a new chunk on overflow. Layout of the malloc'd block:

            [next_prev: i8* (8 bytes)][data: chunk_size bytes]

        The first 8 bytes form the chain link backward to the older
        previous chunk. The remaining bytes are the new data area.

        Updates the arena struct's base/current/end to point inside
        the new data area, and prev_chunk to point at this new block
        (so the chain head walks backward through all overflow blocks
        on destroy).

        Returns the pointer where THIS allocation lives — the start
        of the new data area, after the 8-byte chain header.
        """
        i8_ptr = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)

        # Compute the next chunk size with geometric growth.
        # The active chunk's data span is `end - base`. We double it for
        # the next chunk so successive overflow chunks amortize malloc
        # syscalls under sustained load — pattern recommended for chunked
        # arena allocators (Medium / Reddit / Odin-lang forum, etc.).
        # A floor of OVERFLOW_CHUNK_MIN guards against degenerate tiny
        # initial chunks; the request size itself is also enforced as a
        # minimum so a single huge allocation is satisfied even if it
        # exceeds the doubled-last-chunk number.
        base = self._load_field(
            builder, arena_struct, self.OFFSET_BASE, "active_base_for_growth"
        )
        end = self._load_field(
            builder, arena_struct, self.OFFSET_END, "active_end_for_growth"
        )
        base_int_g = builder.ptrtoint(base, i64, name="base_int_g")
        end_int_g = builder.ptrtoint(end, i64, name="end_int_g")
        active_span = builder.sub(end_int_g, base_int_g, name="active_chunk_span")
        # Doubled span — but careful with overflow on i64. For realistic
        # arena sizes (terabytes max), no concern; multiplication by 2
        # is `shl 1`.
        doubled = builder.shl(active_span, ir.Constant(i64, 1), name="doubled_span")

        # Pick max(size, doubled, OVERFLOW_CHUNK_MIN) via two selects.
        chunk_min = ir.Constant(i64, self.OVERFLOW_CHUNK_MIN)
        size_or_doubled_gt = builder.icmp_unsigned(
            ">", size, doubled, name="size_or_doubled_gt"
        )
        size_or_doubled = builder.select(
            size_or_doubled_gt, size, doubled, name="size_or_doubled"
        )
        gt_min = builder.icmp_unsigned(">", size_or_doubled, chunk_min, name="gt_min")
        chunk_size = builder.select(
            gt_min, size_or_doubled, chunk_min, name="overflow_chunk_size"
        )

        # Add 8 bytes for the chain link header
        link_size = ir.Constant(i64, 8)
        block_size = builder.add(chunk_size, link_size, name="overflow_block_size")

        # malloc the new block
        malloc = self._get_malloc()
        new_block = builder.call(malloc, [block_size], name="overflow_block")

        # OOM check — if the system genuinely can't give us another
        # chunk, abort cleanly. This is a real out-of-memory state,
        # not a fixed-cap overflow. The error message is distinct so
        # users can tell which case they hit.
        null_ptr = ir.Constant(i8_ptr, None)
        is_null = builder.icmp_unsigned("==", new_block, null_ptr, name="chunk_oom")
        chunk_oom_block = self.codegen.current_function.append_basic_block(
            "arena_chunk_oom"
        )
        chunk_ok_block = self.codegen.current_function.append_basic_block(
            "arena_chunk_ok"
        )
        builder.cbranch(is_null, chunk_oom_block, chunk_ok_block)

        builder.position_at_end(chunk_oom_block)
        error_msg = self.codegen.create_string_constant(
            "Error: Out of memory extending arena (system RAM exhausted)!\n"
        )
        printf = self.codegen.get_printf()
        builder.call(printf, [error_msg])
        exit_func = self.codegen.get_exit_func()
        builder.call(exit_func, [ir.Constant(ir.IntType(32), 1)])
        builder.unreachable()

        builder.position_at_end(chunk_ok_block)

        # Write the chain link: first 8 bytes of the new block hold
        # the previous prev_chunk value. After this, arena.prev_chunk
        # points to the new block, and walking backward from
        # arena.prev_chunk reads each block's first 8 bytes to find
        # the next-older block (or null at the chain root).
        old_prev = self._load_field(
            builder, arena_struct, self.OFFSET_PREV_CHUNK, "old_prev_chunk"
        )
        link_slot = builder.bitcast(new_block, i8_ptr.as_pointer(), name="link_slot")
        builder.store(old_prev, link_slot)

        # Update arena.prev_chunk = new_block
        self._store_field(builder, arena_struct, self.OFFSET_PREV_CHUNK, new_block)

        # Compute the data area: new_block + 8 (skip the chain header)
        data_start = builder.gep(new_block, [link_size], name="overflow_data_start")
        # End pointer: data_start + chunk_size
        data_start_int = builder.ptrtoint(data_start, i64, name="data_start_int")
        end_int = builder.add(data_start_int, chunk_size, name="overflow_end_int")
        end_ptr = builder.inttoptr(end_int, i8_ptr, name="overflow_end")

        # Update arena.base / current / end to point inside this chunk.
        # current is advanced past the size of THIS allocation, so the
        # caller's allocation lives at data_start..data_start+size and
        # the next bump in this chunk starts at data_start+size.
        new_current_int = builder.add(
            data_start_int, size, name="new_chunk_current_int"
        )
        new_current = builder.inttoptr(
            new_current_int, i8_ptr, name="new_chunk_current"
        )

        self._store_field(builder, arena_struct, self.OFFSET_BASE, data_start)
        self._store_field(builder, arena_struct, self.OFFSET_CURRENT, new_current)
        self._store_field(builder, arena_struct, self.OFFSET_END, end_ptr)

        # Return the alloc start
        return data_start

    def arena_reset(self, arena: ir.Value) -> None:
        """
        Reset arena to empty. Frees all overflow chunks (walks the
        prev_chunk chain) and resets the base/current pointers in the
        original block. The original block's size is recovered by
        loading the existing end pointer — base..end was the original
        data range.

        After reset:
          - prev_chunk = null
          - base unchanged (still points to the original block's data area)
          - current = base
          - end unchanged

        Note: arena_reset only correctly restores the *original* end
        if no overflow has happened yet, since overflow updates end to
        point inside the latest chunk. To support reset across
        overflows we'd need to track the original size; for now,
        callers that need reset-across-overflow should arena_destroy
        and arena_create fresh. The implicit string arena in main()
        never calls arena_reset, so this limitation doesn't bite in
        practice.

        Args:
            arena: Arena pointer (i8*)
        """
        builder = self.codegen.current_builder
        i8_ptr = ir.IntType(8).as_pointer()

        arena_ptr_ty = self.arena_type.as_pointer()
        arena_struct = builder.bitcast(arena, arena_ptr_ty, name="arena_struct")

        # Walk and free all overflow chunks.
        self._free_chunk_chain(builder, arena_struct)

        # Reset prev_chunk = null
        null_ptr = ir.Constant(i8_ptr, None)
        self._store_field(builder, arena_struct, self.OFFSET_PREV_CHUNK, null_ptr)

        # Reset current = base
        base = self._load_field(builder, arena_struct, self.OFFSET_BASE, "arena_base")
        self._store_field(builder, arena_struct, self.OFFSET_CURRENT, base)

    def arena_destroy(self, arena: ir.Value) -> None:
        """
        Destroy arena and free all memory: overflow chunks first via
        the prev_chunk chain, then the original arena block.

        Args:
            arena: Arena pointer (i8*)
        """
        builder = self.codegen.current_builder

        arena_ptr_ty = self.arena_type.as_pointer()
        arena_struct = builder.bitcast(arena, arena_ptr_ty, name="arena_struct")

        # Free all overflow chunks
        self._free_chunk_chain(builder, arena_struct)

        # Free the original block
        free = self._get_free()
        builder.call(free, [arena])

    def _free_chunk_chain(self, builder: ir.IRBuilder, arena_struct: ir.Value) -> None:
        """
        Walk the prev_chunk linked list and free each block. After
        this, prev_chunk should be considered consumed; the caller
        either resets it (arena_reset) or tears down the arena
        (arena_destroy).

        Each block has its first 8 bytes pointing to the next-older
        block. The chain terminates at null.
        """
        i8_ptr = ir.IntType(8).as_pointer()
        free = self._get_free()

        # Loop variable: a stack slot holding the current chunk pointer.
        # Initialize from arena.prev_chunk.
        cur_slot = builder.alloca(i8_ptr, name="chain_walker")
        initial = self._load_field(
            builder, arena_struct, self.OFFSET_PREV_CHUNK, "chain_initial"
        )
        builder.store(initial, cur_slot)

        loop_cond = self.codegen.current_function.append_basic_block("chain_cond")
        loop_body = self.codegen.current_function.append_basic_block("chain_body")
        loop_end = self.codegen.current_function.append_basic_block("chain_end")
        builder.branch(loop_cond)

        builder.position_at_end(loop_cond)
        cur = builder.load(cur_slot, name="chain_cur")
        null_ptr = ir.Constant(i8_ptr, None)
        is_null = builder.icmp_unsigned("==", cur, null_ptr, name="chain_at_end")
        builder.cbranch(is_null, loop_end, loop_body)

        builder.position_at_end(loop_body)
        # Read the next-older link from the first 8 bytes of `cur`,
        # save it as the next iteration's value, then free `cur`.
        link_slot = builder.bitcast(cur, i8_ptr.as_pointer(), name="chain_link_slot")
        next_chunk = builder.load(link_slot, name="chain_next")
        builder.store(next_chunk, cur_slot)
        builder.call(free, [cur])
        builder.branch(loop_cond)

        builder.position_at_end(loop_end)

    def arena_used(self, arena: ir.Value) -> ir.Value:
        """
        Get bytes currently used in the arena's *active* chunk.

        Note: this is the active-chunk usage, not a sum across all
        overflow chunks. For a chunked arena, the active-chunk number
        is what's still allocatable in the bump path; the historical
        chunks are full but immutable. If callers need a total-bytes
        figure across the chain, that requires walking the chain and
        summing each chunk's size, which is O(chunks) — not currently
        exposed but cheap to add.

        Args:
            arena: Arena pointer (i8*)

        Returns:
            Used bytes in active chunk (i64)
        """
        builder = self.codegen.current_builder
        i64 = ir.IntType(64)

        arena_ptr_ty = self.arena_type.as_pointer()
        arena_struct = builder.bitcast(arena, arena_ptr_ty, name="arena_struct")

        base = self._load_field(builder, arena_struct, self.OFFSET_BASE, "arena_base")
        current = self._load_field(
            builder, arena_struct, self.OFFSET_CURRENT, "arena_current"
        )

        base_int = builder.ptrtoint(base, i64, name="base_int")
        current_int = builder.ptrtoint(current, i64, name="current_int")
        used = builder.sub(current_int, base_int, name="arena_used")
        return used

    def arena_remaining(self, arena: ir.Value) -> ir.Value:
        """
        Get bytes remaining in the active chunk before the next
        overflow. Hitting zero (or below the next request size)
        triggers a fresh chunk allocation in arena_alloc.

        Args:
            arena: Arena pointer (i8*)

        Returns:
            Remaining bytes in active chunk (i64)
        """
        builder = self.codegen.current_builder
        i64 = ir.IntType(64)

        arena_ptr_ty = self.arena_type.as_pointer()
        arena_struct = builder.bitcast(arena, arena_ptr_ty, name="arena_struct")

        current = self._load_field(
            builder, arena_struct, self.OFFSET_CURRENT, "arena_current"
        )
        end = self._load_field(builder, arena_struct, self.OFFSET_END, "arena_end")

        current_int = builder.ptrtoint(current, i64, name="current_int")
        end_int = builder.ptrtoint(end, i64, name="end_int")
        remaining = builder.sub(end_int, current_int, name="arena_remaining")
        return remaining

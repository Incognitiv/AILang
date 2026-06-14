"""
Channel communication builtins for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression
refactor.
"""

from __future__ import annotations

from parser.ast import (
    ChannelClose,
    ChannelCreate,
    ChannelRecv,
    ChannelSend,
    ChannelTryRecv,
    ChannelTrySend,
)
from typing import Any

from llvmlite import ir

# Channel struct field indices (for GEP operations)
# Channel struct layout: { capacity, head, tail, closed, lock, buffer_ptr }
CHAN_FIELD_CAPACITY = 0
CHAN_FIELD_HEAD = 1
CHAN_FIELD_TAIL = 2
CHAN_FIELD_CLOSED = 3
CHAN_FIELD_LOCK = 4
CHAN_FIELD_BUFFER = 5


class ExprBuiltinChannelEmitter:
    """Go-style channel runtime builtins."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _chan_pointer_type(self) -> ir.PointerType:
        return self.codegen.get_channel_type().as_pointer()

    def _chan_ptr_from_handle(self, ch_handle: ir.Value) -> ir.Value:
        return self.builder.inttoptr(
            ch_handle, self._chan_pointer_type(), name="chan_ptr"
        )

    def _chan_field_ptr(
        self, chan_ptr: ir.Value, zero: ir.Constant, field_index: int, name: str
    ) -> ir.Value:
        return self.builder.gep(
            chan_ptr,
            [zero, ir.Constant(ir.IntType(32), field_index)],
            name=name,
        )

    def _chan_field_ptrs(
        self, chan_ptr: ir.Value, zero: ir.Constant, fields: list[tuple[str, int]]
    ) -> dict[str, ir.Value]:
        return {
            name: self._chan_field_ptr(chan_ptr, zero, field_index, name)
            for name, field_index in fields
        }

    def _chan_write_tail(
        self,
        buf_ptr: ir.Value,
        tail: ir.Value,
        capacity: ir.Value,
        value: ir.Value,
        tail_ptr: ir.Value,
        one: ir.Constant,
    ) -> None:
        idx = self.builder.srem(tail, capacity, name="buf_idx")
        elem_ptr = self.builder.gep(buf_ptr, [idx], name="elem_ptr")
        self.builder.store(value, elem_ptr)
        new_tail = self.builder.add(tail, one, name="new_tail")
        self.builder.store(new_tail, tail_ptr)

    def _chan_read_head(
        self,
        buf_ptr: ir.Value,
        head: ir.Value,
        capacity: ir.Value,
        result_ptr: ir.Value,
        head_ptr: ir.Value,
        one: ir.Constant,
    ) -> None:
        idx = self.builder.srem(head, capacity, name="buf_idx")
        elem_ptr = self.builder.gep(buf_ptr, [idx], name="elem_ptr")
        value = self.builder.load(elem_ptr, name="recv_value")
        self.builder.store(value, result_ptr)
        new_head = self.builder.add(head, one, name="new_head")
        self.builder.store(new_head, head_ptr)

    def _chan_try_lock(
        self,
        lock_ptr: ir.Value,
        one: ir.Constant,
        zero: ir.Constant,
        success_block: ir.Block,
        busy_block: ir.Block,
    ) -> None:
        old_lock = self.builder.atomic_rmw(
            "xchg", lock_ptr, one, "seq_cst", name="old_lock"
        )
        lock_was_free = self.builder.icmp_signed("==", old_lock, zero, name="lock_free")
        self.builder.cbranch(lock_was_free, success_block, busy_block)

    def _chan_release_to(
        self, lock_ptr: ir.Value, zero: ir.Constant, target_block: ir.Block
    ) -> None:
        self.builder.atomic_rmw("xchg", lock_ptr, zero, "release")
        self.builder.branch(target_block)

    def _chan_enter_locked_path(
        self,
        lock_ptr: ir.Value,
        one: ir.Constant,
        zero: ir.Constant,
        spin_block: ir.Block,
        got_lock_block: ir.Block,
        busy_block: ir.Block,
    ) -> None:
        self.builder.branch(spin_block)
        self.builder.position_at_end(spin_block)
        self._chan_try_lock(lock_ptr, one, zero, got_lock_block, busy_block)
        self.builder.position_at_end(got_lock_block)

    def _chan_branch_closed(
        self,
        closed_ptr: ir.Value,
        zero: ir.Constant,
        closed_block: ir.Block,
        open_block: ir.Block,
    ) -> None:
        closed_val = self.builder.load(closed_ptr, name="closed")
        is_closed = self.builder.icmp_signed("!=", closed_val, zero, name="is_closed")
        self.builder.cbranch(is_closed, closed_block, open_block)

    def _chan_load_head_tail(
        self, head_ptr: ir.Value, tail_ptr: ir.Value
    ) -> tuple[ir.Value, ir.Value]:
        head = self.builder.load(head_ptr, name="head")
        tail = self.builder.load(tail_ptr, name="tail")
        return head, tail

    def _chan_branch_has_data(
        self,
        head_ptr: ir.Value,
        tail_ptr: ir.Value,
        yes_block: ir.Block,
        no_block: ir.Block,
    ) -> tuple[ir.Value, ir.Value]:
        head, tail = self._chan_load_head_tail(head_ptr, tail_ptr)
        has_data = self.builder.icmp_signed("<", head, tail, name="has_data")
        self.builder.cbranch(has_data, yes_block, no_block)
        return head, tail

    def _chan_branch_has_space(
        self,
        cap_ptr: ir.Value,
        head_ptr: ir.Value,
        tail_ptr: ir.Value,
        yes_block: ir.Block,
        no_block: ir.Block,
    ) -> tuple[ir.Value, ir.Value]:
        capacity = self.builder.load(cap_ptr, name="capacity")
        head, tail = self._chan_load_head_tail(head_ptr, tail_ptr)
        size = self.builder.sub(tail, head, name="size")
        has_space = self.builder.icmp_signed("<", size, capacity, name="has_space")
        self.builder.cbranch(has_space, yes_block, no_block)
        return capacity, tail

    def visit_ChannelCreate(self, node: ChannelCreate) -> ir.Value:
        """Create a new channel with given capacity.

        channel(type, capacity)

        Returns a pointer to the channel struct.
        """
        # Get capacity value
        capacity = self.generate_expr(node.capacity)
        if capacity.type != ir.IntType(64):
            capacity = self.builder.zext(capacity, ir.IntType(64), name="cap64")

        # Allocate channel struct with null check
        chan_size = ir.Constant(ir.IntType(64), 48)  # 6 * 8 bytes
        chan_mem = self.codegen.checked_malloc(chan_size, "chan_mem")
        chan_ptr = self.builder.bitcast(
            chan_mem, self._chan_pointer_type(), name="chan_ptr"
        )

        # Allocate buffer (capacity * 8 bytes for i64 elements)
        eight = ir.Constant(ir.IntType(64), 8)
        buf_size = self.builder.mul(capacity, eight, name="buf_size")
        # Ensure minimum buffer size for unbuffered channels
        buf_size_min = self.builder.add(buf_size, eight, name="buf_size_min")
        buf_mem = self.codegen.checked_malloc(buf_size_min, "buf_mem")
        buf_ptr = self.builder.bitcast(
            buf_mem, ir.IntType(64).as_pointer(), name="buf_ptr"
        )

        # Initialize channel fields
        zero = ir.Constant(ir.IntType(64), 0)

        # capacity
        cap_ptr = self._chan_field_ptr(chan_ptr, zero, CHAN_FIELD_CAPACITY, "cap_ptr")
        self.builder.store(capacity, cap_ptr)

        # head = 0
        head_ptr = self._chan_field_ptr(chan_ptr, zero, CHAN_FIELD_HEAD, "head_ptr")
        self.builder.store(zero, head_ptr)

        # tail = 0
        tail_ptr = self._chan_field_ptr(chan_ptr, zero, CHAN_FIELD_TAIL, "tail_ptr")
        self.builder.store(zero, tail_ptr)

        # closed = 0
        closed_ptr = self._chan_field_ptr(
            chan_ptr, zero, CHAN_FIELD_CLOSED, "closed_ptr"
        )
        self.builder.store(zero, closed_ptr)

        # lock = 0
        lock_ptr = self._chan_field_ptr(chan_ptr, zero, CHAN_FIELD_LOCK, "lock_ptr")
        self.builder.store(zero, lock_ptr)

        # buffer pointer
        buf_field_ptr = self._chan_field_ptr(
            chan_ptr, zero, CHAN_FIELD_BUFFER, "buf_field_ptr"
        )
        self.builder.store(buf_ptr, buf_field_ptr)

        # Return channel pointer as i64 (handle)
        return self.builder.ptrtoint(chan_ptr, ir.IntType(64), name="chan_handle")

    def visit_ChannelSend(self, node: ChannelSend) -> ir.Value:
        """Send a value to a channel. Blocks if full.
        Returns -1 if the channel has been closed.

        chan_send(ch, value)
        """
        ch_handle = self.generate_expr(node.channel)
        value = self.generate_expr(node.value)

        # Ensure value is i64
        if value.type != ir.IntType(64):
            if isinstance(value.type, ir.IntType):
                value = self.builder.zext(value, ir.IntType(64), name="val64")
            else:
                value = self.builder.bitcast(value, ir.IntType(64), name="val64")

        # Convert handle to pointer
        chan_ptr = self._chan_ptr_from_handle(ch_handle)

        zero = ir.Constant(ir.IntType(64), 0)
        one = ir.Constant(ir.IntType(64), 1)
        neg_one = ir.Constant(ir.IntType(64), -1)

        # Get field pointers
        ptrs = self._chan_field_ptrs(
            chan_ptr,
            zero,
            [
                ("cap_ptr", CHAN_FIELD_CAPACITY),
                ("head_ptr", CHAN_FIELD_HEAD),
                ("tail_ptr", CHAN_FIELD_TAIL),
                ("closed_ptr", CHAN_FIELD_CLOSED),
                ("lock_ptr", CHAN_FIELD_LOCK),
                ("buf_ptr_ptr", CHAN_FIELD_BUFFER),
            ],
        )
        cap_ptr = ptrs["cap_ptr"]
        head_ptr = ptrs["head_ptr"]
        tail_ptr = ptrs["tail_ptr"]
        closed_ptr = ptrs["closed_ptr"]
        lock_ptr = ptrs["lock_ptr"]
        buf_ptr_ptr = ptrs["buf_ptr_ptr"]

        # Allocate result storage
        result_ptr = self.builder.alloca(ir.IntType(64), name="send_result")
        self.builder.store(zero, result_ptr)

        # Acquire spinlock
        spin_block = self.codegen.current_function.append_basic_block("chan_spin")
        got_lock = self.codegen.current_function.append_basic_block("chan_got_lock")
        chan_closed_blk = self.codegen.current_function.append_basic_block(
            "chan_closed"
        )
        check_space = self.codegen.current_function.append_basic_block("check_space")
        chan_full = self.codegen.current_function.append_basic_block("chan_full")
        send_block = self.codegen.current_function.append_basic_block("chan_send")
        done_block = self.codegen.current_function.append_basic_block("chan_send_done")

        self._chan_enter_locked_path(
            lock_ptr, one, zero, spin_block, got_lock, spin_block
        )

        # Check closed flag under lock
        self._chan_branch_closed(closed_ptr, zero, chan_closed_blk, check_space)

        # Channel is closed          release lock and return -1
        self.builder.position_at_end(chan_closed_blk)
        self.builder.store(neg_one, result_ptr)
        self._chan_release_to(lock_ptr, zero, done_block)

        # Check if channel has space
        self.builder.position_at_end(check_space)
        capacity, tail = self._chan_branch_has_space(
            cap_ptr, head_ptr, tail_ptr, send_block, chan_full
        )

        # Channel is full          release lock and retry
        self.builder.position_at_end(chan_full)
        self._chan_release_to(lock_ptr, zero, spin_block)

        # Normal send path
        self.builder.position_at_end(send_block)

        # Load buffer pointer
        buf_ptr = self.builder.load(buf_ptr_ptr, name="buf_ptr")
        self._chan_write_tail(buf_ptr, tail, capacity, value, tail_ptr, one)

        # Release lock
        self._chan_release_to(lock_ptr, zero, done_block)

        self.builder.position_at_end(done_block)
        return self.builder.load(result_ptr, name="send_final")

    def visit_ChannelRecv(self, node: ChannelRecv) -> ir.Value:
        """Receive a value from a channel. Blocks if empty.
        Returns 0 if channel is closed and no data remains.

        chan_recv(ch)
        """
        ch_handle = self.generate_expr(node.channel)

        # Convert handle to pointer
        chan_ptr = self._chan_ptr_from_handle(ch_handle)

        zero = ir.Constant(ir.IntType(64), 0)
        one = ir.Constant(ir.IntType(64), 1)

        # Get field pointers
        ptrs = self._chan_field_ptrs(
            chan_ptr,
            zero,
            [
                ("cap_ptr", CHAN_FIELD_CAPACITY),
                ("head_ptr", CHAN_FIELD_HEAD),
                ("tail_ptr", CHAN_FIELD_TAIL),
                ("closed_ptr", CHAN_FIELD_CLOSED),
                ("lock_ptr", CHAN_FIELD_LOCK),
                ("buf_ptr_ptr", CHAN_FIELD_BUFFER),
            ],
        )
        cap_ptr = ptrs["cap_ptr"]
        head_ptr = ptrs["head_ptr"]
        tail_ptr = ptrs["tail_ptr"]
        closed_ptr = ptrs["closed_ptr"]
        lock_ptr = ptrs["lock_ptr"]
        buf_ptr_ptr = ptrs["buf_ptr_ptr"]

        # Spin until we have data (or channel is closed and drained)
        wait_block = self.codegen.current_function.append_basic_block("chan_wait")
        spin_block = self.codegen.current_function.append_basic_block("chan_recv_spin")
        got_lock = self.codegen.current_function.append_basic_block(
            "chan_recv_got_lock"
        )
        has_data = self.codegen.current_function.append_basic_block("chan_has_data")
        no_data = self.codegen.current_function.append_basic_block("chan_no_data")
        closed_empty = self.codegen.current_function.append_basic_block(
            "chan_closed_empty"
        )
        done_block = self.codegen.current_function.append_basic_block("chan_recv_done")

        # Allocate result storage
        result_ptr = self.builder.alloca(ir.IntType(64), name="recv_result")
        self.builder.store(zero, result_ptr)

        self.builder.branch(wait_block)

        # Wait loop - check if data available
        self.builder.position_at_end(wait_block)
        head_check = self.builder.load(head_ptr, name="head_check")
        tail_check = self.builder.load(tail_ptr, name="tail_check")
        data_avail = self.builder.icmp_signed(
            "<", head_check, tail_check, name="data_avail"
        )
        self.builder.cbranch(
            data_avail,
            spin_block,
            self._chan_recv_check_closed(closed_ptr, zero, closed_empty, wait_block),
        )

        # Acquire spinlock
        self.builder.position_at_end(spin_block)
        self._chan_try_lock(lock_ptr, one, zero, got_lock, spin_block)

        self.builder.position_at_end(got_lock)

        # Check again under lock
        head, _ = self._chan_branch_has_data(head_ptr, tail_ptr, has_data, no_data)

        # No data - release lock and retry
        self.builder.position_at_end(no_data)
        self._chan_release_to(lock_ptr, zero, wait_block)

        # Has data - read it
        self.builder.position_at_end(has_data)
        capacity = self.builder.load(cap_ptr, name="capacity")
        buf_ptr = self.builder.load(buf_ptr_ptr, name="buf_ptr")
        self._chan_read_head(buf_ptr, head, capacity, result_ptr, head_ptr, one)

        # Release lock
        self._chan_release_to(lock_ptr, zero, done_block)

        # Closed and empty          return 0
        self.builder.position_at_end(closed_empty)
        self.builder.branch(done_block)

        self.builder.position_at_end(done_block)
        return self.builder.load(result_ptr, name="final_result")

    def _chan_recv_check_closed(
        self,
        closed_ptr: ir.Value,
        zero: ir.Constant,
        closed_block: ir.Block,
        wait_block: ir.Block,
    ) -> ir.Block:
        """Create a block that checks if a channel is closed and empty.

        If closed, branches to closed_block; otherwise retries via wait_block.
        Returns the newly created check block (caller should cbranch to it).
        """
        check_block = self.codegen.current_function.append_basic_block(
            "chan_check_closed"
        )
        saved = self.builder.block
        self.builder.position_at_end(check_block)
        closed_val = self.builder.load(closed_ptr, name="closed_flag")
        is_closed = self.builder.icmp_signed("!=", closed_val, zero, name="is_closed")
        self.builder.cbranch(is_closed, closed_block, wait_block)
        self.builder.position_at_end(saved)
        return check_block

    def visit_ChannelTrySend(self, node: ChannelTrySend) -> ir.Value:
        """Try to send without blocking. Returns 1 if sent, 0 if full.

        chan_try_send(ch, value) -> bool
        """
        ch_handle = self.generate_expr(node.channel)
        value = self.generate_expr(node.value)

        # Ensure value is i64
        if value.type != ir.IntType(64):
            if isinstance(value.type, ir.IntType):
                value = self.builder.zext(value, ir.IntType(64), name="val64")
            else:
                value = self.builder.bitcast(value, ir.IntType(64), name="val64")

        # Convert handle to pointer
        chan_ptr = self._chan_ptr_from_handle(ch_handle)

        zero = ir.Constant(ir.IntType(64), 0)
        one = ir.Constant(ir.IntType(64), 1)

        # Get field pointers
        ptrs = self._chan_field_ptrs(
            chan_ptr,
            zero,
            [
                ("cap_ptr", CHAN_FIELD_CAPACITY),
                ("head_ptr", CHAN_FIELD_HEAD),
                ("tail_ptr", CHAN_FIELD_TAIL),
                ("lock_ptr", CHAN_FIELD_LOCK),
                ("closed_ptr", CHAN_FIELD_CLOSED),
                ("buf_ptr_ptr", CHAN_FIELD_BUFFER),
            ],
        )
        cap_ptr = ptrs["cap_ptr"]
        head_ptr = ptrs["head_ptr"]
        tail_ptr = ptrs["tail_ptr"]
        lock_ptr = ptrs["lock_ptr"]
        closed_ptr = ptrs["closed_ptr"]
        buf_ptr_ptr = ptrs["buf_ptr_ptr"]

        # Allocate result storage
        result_ptr = self.builder.alloca(ir.IntType(64), name="try_send_result")
        self.builder.store(zero, result_ptr)  # Default to failure

        # Create basic blocks
        spin_block = self.codegen.current_function.append_basic_block("try_send_spin")
        got_lock = self.codegen.current_function.append_basic_block("try_send_got_lock")
        chan_closed_blk = self.codegen.current_function.append_basic_block(
            "try_send_closed"
        )
        check_space = self.codegen.current_function.append_basic_block(
            "try_send_check_space"
        )
        has_space = self.codegen.current_function.append_basic_block(
            "try_send_has_space"
        )
        no_space = self.codegen.current_function.append_basic_block("try_send_no_space")
        done_block = self.codegen.current_function.append_basic_block("try_send_done")

        self._chan_enter_locked_path(
            lock_ptr, one, zero, spin_block, got_lock, done_block
        )

        # Check closed flag under lock
        self._chan_branch_closed(closed_ptr, zero, chan_closed_blk, check_space)

        # Channel is closed          release lock and return 0 (failure)
        self.builder.position_at_end(chan_closed_blk)
        self._chan_release_to(lock_ptr, zero, done_block)

        # Check if there's space
        self.builder.position_at_end(check_space)
        capacity, tail = self._chan_branch_has_space(
            cap_ptr, head_ptr, tail_ptr, has_space, no_space
        )

        # Has space - store value
        self.builder.position_at_end(has_space)
        buf_ptr = self.builder.load(buf_ptr_ptr, name="buf_ptr")
        self._chan_write_tail(buf_ptr, tail, capacity, value, tail_ptr, one)

        # Mark success
        self.builder.store(one, result_ptr)

        # Release lock
        self._chan_release_to(lock_ptr, zero, done_block)

        # No space - release lock
        self.builder.position_at_end(no_space)
        self._chan_release_to(lock_ptr, zero, done_block)

        self.builder.position_at_end(done_block)
        return self.builder.load(result_ptr, name="try_send_final")

    def visit_ChannelTryRecv(self, node: ChannelTryRecv) -> ir.Value:
        """Try to receive without blocking. Returns value or 0 if empty.

        Design note: Currently returns just the value, with 0 indicating empty.
        To distinguish "received 0" from "empty channel", use a sentinel value
        or check channel length before/after. A future API could return a tuple
        (value, success) but that would require breaking changes to existing code.

        Example usage:
            val = chan_try_recv(ch)
            if val != 0:  // Assumes 0 is not a valid data value
                process(val)
        """
        ch_handle = self.generate_expr(node.channel)

        # Convert handle to pointer
        chan_ptr = self._chan_ptr_from_handle(ch_handle)

        zero = ir.Constant(ir.IntType(64), 0)
        one = ir.Constant(ir.IntType(64), 1)

        # Get field pointers
        ptrs = self._chan_field_ptrs(
            chan_ptr,
            zero,
            [
                ("cap_ptr", CHAN_FIELD_CAPACITY),
                ("head_ptr", CHAN_FIELD_HEAD),
                ("tail_ptr", CHAN_FIELD_TAIL),
                ("lock_ptr", CHAN_FIELD_LOCK),
                ("buf_ptr_ptr", CHAN_FIELD_BUFFER),
            ],
        )
        cap_ptr = ptrs["cap_ptr"]
        head_ptr = ptrs["head_ptr"]
        tail_ptr = ptrs["tail_ptr"]
        lock_ptr = ptrs["lock_ptr"]
        buf_ptr_ptr = ptrs["buf_ptr_ptr"]

        # Allocate result storage
        result_ptr = self.builder.alloca(ir.IntType(64), name="try_recv_result")
        self.builder.store(zero, result_ptr)  # Default to 0

        # Try to acquire spinlock
        spin_block = self.codegen.current_function.append_basic_block("try_recv_spin")
        got_lock = self.codegen.current_function.append_basic_block("try_recv_got_lock")
        has_data = self.codegen.current_function.append_basic_block("try_recv_has_data")
        no_data = self.codegen.current_function.append_basic_block("try_recv_no_data")
        done_block = self.codegen.current_function.append_basic_block("try_recv_done")

        self.builder.branch(spin_block)

        # Try lock once
        self.builder.position_at_end(spin_block)
        self._chan_try_lock(lock_ptr, one, zero, got_lock, done_block)

        self.builder.position_at_end(got_lock)

        # Check if there's data
        head, _ = self._chan_branch_has_data(head_ptr, tail_ptr, has_data, no_data)

        # Has data - read value
        self.builder.position_at_end(has_data)
        capacity = self.builder.load(cap_ptr, name="capacity")
        buf_ptr = self.builder.load(buf_ptr_ptr, name="buf_ptr")
        self._chan_read_head(buf_ptr, head, capacity, result_ptr, head_ptr, one)

        # Release lock
        self._chan_release_to(lock_ptr, zero, done_block)

        # No data - release lock
        self.builder.position_at_end(no_data)
        self._chan_release_to(lock_ptr, zero, done_block)

        self.builder.position_at_end(done_block)
        return self.builder.load(result_ptr, name="try_recv_final")

    def visit_ChannelClose(self, node: ChannelClose) -> ir.Value:
        """Close a channel (no more sends allowed).

        Acquires the spinlock before setting the closed flag to avoid
        a TOCTOU race with send/recv operations.
        """
        ch_handle = self.generate_expr(node.channel)

        # Convert handle to pointer
        chan_ptr = self._chan_ptr_from_handle(ch_handle)

        zero = ir.Constant(ir.IntType(64), 0)
        one = ir.Constant(ir.IntType(64), 1)

        # Get field pointers
        ptrs = self._chan_field_ptrs(
            chan_ptr,
            zero,
            [
                ("closed_ptr", CHAN_FIELD_CLOSED),
                ("lock_ptr", CHAN_FIELD_LOCK),
            ],
        )
        closed_ptr = ptrs["closed_ptr"]
        lock_ptr = ptrs["lock_ptr"]

        # Acquire spinlock before setting closed flag
        spin_block = self.codegen.current_function.append_basic_block("close_spin")
        got_lock = self.codegen.current_function.append_basic_block("close_got_lock")

        self.builder.branch(spin_block)
        self.builder.position_at_end(spin_block)

        old_lock = self.builder.atomic_rmw(
            "xchg", lock_ptr, one, "seq_cst", name="old_lock"
        )
        lock_was_free = self.builder.icmp_signed("==", old_lock, zero, name="lock_free")
        self.builder.cbranch(lock_was_free, got_lock, spin_block)

        self.builder.position_at_end(got_lock)

        # Set closed flag under lock
        self.builder.store(one, closed_ptr)

        # Release lock
        self.builder.atomic_rmw("xchg", lock_ptr, zero, "release")

        return zero

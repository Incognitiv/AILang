"""Type-level helpers for LLVM collection runtime structs."""

from __future__ import annotations

from llvmlite import ir


def get_channel_type(self) -> ir.LiteralStructType:
    """Get the channel struct type for message passing.

    Channel structure:
    {
        i64 capacity,    // Max items (0 = unbuffered)
        i64 head,        // Read position (atomic)
        i64 tail,        // Write position (atomic)
        i64 closed,      // 0 = open, 1 = closed (atomic)
        i64 lock,        // Spinlock for thread safety
        i64* buffer      // Ring buffer for items
    }
    """
    if self._cg.channel_type is None:
        self._cg.channel_type = ir.LiteralStructType(
            [
                ir.IntType(64),  # capacity
                ir.IntType(64),  # head (read position)
                ir.IntType(64),  # tail (write position)
                ir.IntType(64),  # closed flag
                ir.IntType(64),  # spinlock
                ir.IntType(64).as_pointer(),  # buffer pointer
            ]
        )
    return self._cg.channel_type

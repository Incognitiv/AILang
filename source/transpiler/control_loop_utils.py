"""Small loop utility helpers shared by LLVM statement visitors."""

from __future__ import annotations

from llvmlite import ir


def close_streams_if_outer_loop(self) -> None:
    """Call stream close function when exiting outermost loop."""
    if self.codegen.loop_depth == 0 and (self.codegen._stream_write_func is not None):
        self.builder.call(self.codegen.get_stream_close_func(), [])


def setup_loop_bound(self, max_iterations, name: str):
    if max_iterations is None:
        return None, None
    counter = self.builder.alloca(ir.IntType(64), name=name)
    self.builder.store(ir.Constant(ir.IntType(64), 0), counter)
    return counter, self.codegen.generate_expr(max_iterations)


def and_loop_bound(self, cond: ir.Value, counter: ir.Value | None, max_val) -> ir.Value:
    if counter is None or max_val is None:
        return cond
    current_iter = self.builder.load(counter, name="current_iter")
    within_bound = self.builder.icmp_signed(
        "<", current_iter, max_val, name="within_bound"
    )
    return self.builder.and_(cond, within_bound, name="bounded_cond")


def increment_loop_bound(self, counter: ir.Value | None) -> None:
    if counter is None:
        return
    current = self.builder.load(counter)
    incremented = self.builder.add(
        current, ir.Constant(ir.IntType(64), 1), name="iter_inc"
    )
    self.builder.store(incremented, counter)

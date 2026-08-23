"""Timing and clock builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from transpiler.expr_common import ExprGenError


def _builtin_time_ms(self, _args):
    """Get current time in milliseconds: time_ms() -> int

    Cross-platform:
    - Windows: QueryPerformanceCounter
    - Linux/Unix: clock_gettime(CLOCK_MONOTONIC)

    Returns milliseconds since some arbitrary epoch.
    """
    # Detect target platform from module triple
    triple = str(self.codegen.module.triple)
    is_windows = "windows" in triple or "win32" in triple or triple == ""

    if is_windows:
        return self._time_ms_windows()
    return self._time_ms_linux()


def _time_ms_windows(self):
    """Windows implementation using QueryPerformanceCounter"""
    i64_ptr = ir.IntType(64).as_pointer()

    # Get or declare QueryPerformanceCounter
    qpc_name = "QueryPerformanceCounter"
    if qpc_name not in self.codegen.functions:
        qpc_ty = ir.FunctionType(ir.IntType(32), [i64_ptr])
        qpc_func = ir.Function(self.codegen.module, qpc_ty, qpc_name)
        self.codegen.functions[qpc_name] = qpc_func
    qpc_func = self.codegen.functions[qpc_name]

    qpf_name = "QueryPerformanceFrequency"
    if qpf_name not in self.codegen.functions:
        qpf_ty = ir.FunctionType(ir.IntType(32), [i64_ptr])
        qpf_func = ir.Function(self.codegen.module, qpf_ty, qpf_name)
        self.codegen.functions[qpf_name] = qpf_func
    qpf_func = self.codegen.functions[qpf_name]

    # Allocate storage for counter and frequency
    counter_ptr = self.builder.alloca(ir.IntType(64), name="perf_counter")
    freq_ptr = self.builder.alloca(ir.IntType(64), name="perf_freq")

    # Call QueryPerformanceFrequency
    self.builder.call(qpf_func, [freq_ptr])
    freq = self.builder.load(freq_ptr, name="freq")

    # Call QueryPerformanceCounter
    self.builder.call(qpc_func, [counter_ptr])
    counter = self.builder.load(counter_ptr, name="counter")

    # Convert to milliseconds: (counter * 1000) / freq
    thousand = ir.Constant(ir.IntType(64), 1000)
    counter_ms = self.builder.mul(counter, thousand, name="counter_x1000")
    result = self.builder.sdiv(counter_ms, freq, name="time_ms")

    return result


def _time_ms_linux(self):
    """Linux implementation using clock_gettime(CLOCK_MONOTONIC)"""
    # struct timespec { time_t tv_sec; long tv_nsec; }
    # On 64-bit Linux: both are i64

    # Declare clock_gettime: int clock_gettime(clockid_t, struct timespec*)
    cgt_name = "clock_gettime"
    if cgt_name not in self.codegen.functions:
        # timespec is {i64, i64} - we'll use a pointer to [2 x i64]
        timespec_ty = ir.ArrayType(ir.IntType(64), 2)
        cgt_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(32), timespec_ty.as_pointer()]
        )
        cgt_func = ir.Function(self.codegen.module, cgt_ty, cgt_name)
        self.codegen.functions[cgt_name] = cgt_func
    cgt_func = self.codegen.functions[cgt_name]

    # Allocate timespec
    timespec_ty = ir.ArrayType(ir.IntType(64), 2)
    ts_ptr = self.builder.alloca(timespec_ty, name="timespec")

    # CLOCK_MONOTONIC = 1 on Linux
    clock_monotonic = ir.Constant(ir.IntType(32), 1)

    # Call clock_gettime
    self.builder.call(cgt_func, [clock_monotonic, ts_ptr])

    # Extract seconds and nanoseconds
    zero = ir.Constant(ir.IntType(32), 0)
    one = ir.Constant(ir.IntType(32), 1)

    sec_ptr = self.builder.gep(ts_ptr, [zero, zero], name="sec_ptr")
    nsec_ptr = self.builder.gep(ts_ptr, [zero, one], name="nsec_ptr")

    sec = self.builder.load(sec_ptr, name="tv_sec")
    nsec = self.builder.load(nsec_ptr, name="tv_nsec")

    # Convert to milliseconds: sec * 1000 + nsec / 1000000
    thousand = ir.Constant(ir.IntType(64), 1000)
    million = ir.Constant(ir.IntType(64), 1000000)

    sec_ms = self.builder.mul(sec, thousand, name="sec_ms")
    nsec_ms = self.builder.sdiv(nsec, million, name="nsec_ms")
    result = self.builder.add(sec_ms, nsec_ms, name="time_ms")

    return result


# ------------------------------------------------------------------
# Math functions (libm - hardware accelerated)
# ------------------------------------------------------------------
def _builtin_time_ns(self, _args):
    """Get current time in nanoseconds: time_ns() -> int

    Cross-platform high-precision timing.
    """
    triple = str(self.codegen.module.triple)
    is_windows = "windows" in triple or "win32" in triple or triple == ""

    if is_windows:
        return self._time_ns_windows()
    return self._time_ns_linux()


def _time_ns_windows(self):
    """Windows implementation using QueryPerformanceCounter"""
    i64_ptr = ir.IntType(64).as_pointer()

    qpc_name = "QueryPerformanceCounter"
    if qpc_name not in self.codegen.functions:
        qpc_ty = ir.FunctionType(ir.IntType(32), [i64_ptr])
        qpc_func = ir.Function(self.codegen.module, qpc_ty, qpc_name)
        self.codegen.functions[qpc_name] = qpc_func
    qpc_func = self.codegen.functions[qpc_name]

    qpf_name = "QueryPerformanceFrequency"
    if qpf_name not in self.codegen.functions:
        qpf_ty = ir.FunctionType(ir.IntType(32), [i64_ptr])
        qpf_func = ir.Function(self.codegen.module, qpf_ty, qpf_name)
        self.codegen.functions[qpf_name] = qpf_func
    qpf_func = self.codegen.functions[qpf_name]

    counter_ptr = self.builder.alloca(ir.IntType(64), name="perf_counter_ns")
    freq_ptr = self.builder.alloca(ir.IntType(64), name="perf_freq_ns")

    self.builder.call(qpf_func, [freq_ptr])
    freq = self.builder.load(freq_ptr, name="freq_ns")

    self.builder.call(qpc_func, [counter_ptr])
    counter = self.builder.load(counter_ptr, name="counter_ns")

    # Convert to nanoseconds: (counter * 1000000000) / freq
    billion = ir.Constant(ir.IntType(64), 1000000000)
    counter_ns = self.builder.mul(counter, billion, name="counter_x1B")
    result = self.builder.sdiv(counter_ns, freq, name="time_ns")

    return result


def _time_ns_linux(self):
    """Linux implementation using clock_gettime(CLOCK_MONOTONIC)"""
    cgt_name = "clock_gettime"
    if cgt_name not in self.codegen.functions:
        timespec_ty = ir.ArrayType(ir.IntType(64), 2)
        cgt_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(32), timespec_ty.as_pointer()]
        )
        cgt_func = ir.Function(self.codegen.module, cgt_ty, cgt_name)
        self.codegen.functions[cgt_name] = cgt_func
    cgt_func = self.codegen.functions[cgt_name]

    timespec_ty = ir.ArrayType(ir.IntType(64), 2)
    ts_ptr = self.builder.alloca(timespec_ty, name="timespec_ns")

    clock_monotonic = ir.Constant(ir.IntType(32), 1)
    self.builder.call(cgt_func, [clock_monotonic, ts_ptr])

    zero = ir.Constant(ir.IntType(32), 0)
    one = ir.Constant(ir.IntType(32), 1)

    sec_ptr = self.builder.gep(ts_ptr, [zero, zero], name="sec_ptr_ns")
    nsec_ptr = self.builder.gep(ts_ptr, [zero, one], name="nsec_ptr_ns")

    sec = self.builder.load(sec_ptr, name="tv_sec_ns")
    nsec = self.builder.load(nsec_ptr, name="tv_nsec_ns")

    # Convert to nanoseconds: sec * 1000000000 + nsec
    billion = ir.Constant(ir.IntType(64), 1000000000)
    sec_ns = self.builder.mul(sec, billion, name="sec_to_ns")
    result = self.builder.add(sec_ns, nsec, name="time_ns")

    return result


# ------------------------------------------------------------------
# Fast Bit Manipulation Intrinsics
# ------------------------------------------------------------------
def _builtin_clock_ns(self, args):
    """Get wall-clock time in nanoseconds: clock_ns() -> int

    Uses QueryPerformanceCounter on Windows, clock_gettime on Unix.
    Good for timing longer operations (microseconds to seconds).

    Example:
        start = clock_ns()
        // ... code to benchmark ...
        end = clock_ns()
        elapsed_ns = end - start
        elapsed_ms = elapsed_ns / 1000000
    """

    if len(args) != 0:
        raise ExprGenError("clock_ns() takes no arguments")

    # Detect platform from target triple
    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        return self._clock_ns_windows()
    return self._clock_ns_posix()


def _clock_ns_windows(self) -> ir.Value:
    """Windows implementation using QueryPerformanceCounter"""
    # Use QueryPerformanceCounter on Windows
    # It returns ticks, we need to convert to nanoseconds
    if self.codegen.qpc_func is None:
        # BOOL QueryPerformanceCounter(LARGE_INTEGER *lpPerformanceCount);
        # BOOL QueryPerformanceFrequency(LARGE_INTEGER *lpFrequency);
        i64_ptr = ir.IntType(64).as_pointer()

        qpc_ty = ir.FunctionType(ir.IntType(32), [i64_ptr])
        qpc_func = ir.Function(self.codegen.module, qpc_ty, "QueryPerformanceCounter")
        self.codegen.qpc_func = qpc_func

        qpf_ty = ir.FunctionType(ir.IntType(32), [i64_ptr])
        qpf_func = ir.Function(self.codegen.module, qpf_ty, "QueryPerformanceFrequency")
        self.codegen.qpf_func = qpf_func
    else:
        # Use cached functions (type narrowing for mypy)
        if self.codegen.qpc_func is None or self.codegen.qpf_func is None:
            raise ExprGenError("Performance counter functions not initialized")
        qpc_func = self.codegen.qpc_func
        qpf_func = self.codegen.qpf_func

    # Allocate storage for counter and frequency
    counter_ptr = self.builder.alloca(ir.IntType(64), name="perf_counter")
    freq_ptr = self.builder.alloca(ir.IntType(64), name="perf_freq")

    # Get frequency and counter
    self.builder.call(qpf_func, [freq_ptr])
    self.builder.call(qpc_func, [counter_ptr])

    counter = self.builder.load(counter_ptr, name="counter")
    freq = self.builder.load(freq_ptr, name="freq")

    # Convert to nanoseconds without i64 overflow.
    # Naive (counter * 1e9) overflows i64 once counter > 2^63 / 1e9 ~= 9.22e9.
    # On Windows QPF is locked to 10 MHz, so naive math wraps after ~15 min uptime
    # and clock_ns() then returns negative values. Decompose:
    #   counter = q*freq + r,   r < freq
    #   ns      = q*1e9 + (r*1e9)/freq
    # q*1e9 is safe up to ~292 years uptime; r*1e9 is bounded by freq*1e9
    # which fits i64 for any realistic QPF (<= ~9.22 GHz).
    billion = ir.Constant(ir.IntType(64), 1000000000)
    q = self.builder.sdiv(counter, freq, name="counter_q")
    r = self.builder.srem(counter, freq, name="counter_r")
    q_ns = self.builder.mul(q, billion, name="q_ns")
    r_scaled = self.builder.mul(r, billion, name="r_scaled")
    r_ns = self.builder.sdiv(r_scaled, freq, name="r_ns")
    result_ns = self.builder.add(q_ns, r_ns, name="result_ns")

    return result_ns


def _clock_ns_posix(self) -> ir.Value:
    """POSIX implementation using clock_gettime"""
    # struct timespec { time_t tv_sec; long tv_nsec; }
    # int clock_gettime(clockid_t clk_id, struct timespec *tp);
    # CLOCK_MONOTONIC = 1

    # Define timespec struct type: { i64, i64 }
    timespec_ty = ir.LiteralStructType([ir.IntType(64), ir.IntType(64)])
    timespec_ptr_ty = timespec_ty.as_pointer()

    # Declare clock_gettime if not already
    if self.codegen.clock_gettime_func is None:
        clock_gettime_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(32), timespec_ptr_ty]
        )
        self.codegen.clock_gettime_func = ir.Function(
            self.codegen.module, clock_gettime_ty, "clock_gettime"
        )

    clock_gettime = self.codegen.clock_gettime_func

    # Allocate timespec struct
    ts_ptr = self.builder.alloca(timespec_ty, name="timespec")

    # Call clock_gettime(CLOCK_MONOTONIC, &ts)
    # CLOCK_MONOTONIC = 1
    clock_monotonic = ir.Constant(ir.IntType(32), 1)
    self.builder.call(clock_gettime, [clock_monotonic, ts_ptr])

    # Load seconds and nanoseconds
    zero = ir.Constant(ir.IntType(32), 0)
    one = ir.Constant(ir.IntType(32), 1)

    sec_ptr = self.builder.gep(
        ts_ptr, [ir.Constant(ir.IntType(64), 0), zero], name="sec_ptr"
    )
    nsec_ptr = self.builder.gep(
        ts_ptr, [ir.Constant(ir.IntType(64), 0), one], name="nsec_ptr"
    )

    sec = self.builder.load(sec_ptr, name="seconds")
    nsec = self.builder.load(nsec_ptr, name="nanoseconds")

    # Convert to total nanoseconds: sec * 1_000_000_000 + nsec
    billion = ir.Constant(ir.IntType(64), 1000000000)
    sec_ns = self.builder.mul(sec, billion, name="sec_ns")
    total_ns = self.builder.add(sec_ns, nsec, name="total_ns")

    return total_ns


# ------------------------------------------------------------------
# Command-line arguments: argc/argv
# ------------------------------------------------------------------

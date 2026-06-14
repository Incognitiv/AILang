"""
AILang profile instrumentation.

When `--profile` is passed to the AILang CLI, codegen.py injects calls to
`__ailang_prof_enter(name)` and `__ailang_prof_exit(name)` at every AILang
function's entry and at every return path. This module provides those
callbacks (as ctypes thunks), accumulates per-function call counts and
timings, and renders a `systemd-analyze blame`-style report when the
program finishes.

The same accumulator doubles as crash diagnostics: any frames still on the
call stack when the program dies are exactly the AILang functions that were
mid-execution at the time of the fault. fast_jit.py prints those frames
alongside the crash banner.

Two design choices worth stating:
  * Callbacks live in Python, not in JIT-emitted IR. That keeps codegen.py
    small (declare two extern functions, emit two calls) and means all the
    bookkeeping logic — self-time math, formatting, sorting — is plain
    Python that can be unit-tested without involving LLVM at all.
  * The callback addresses are exposed via jit_symbols() and wired into
    the ORC JIT by fast_jit.py through JITLibraryBuilder.import_symbol —
    NOT via binding.add_symbol, which is MCJIT-flavored and silently does
    not propagate to LLJIT/ORC. We hold a Python-side reference to the
    ctypes thunks for the lifetime of the process; if those refs were
    garbage-collected, calls into them would segfault.
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Any, Optional, cast

# --------------------------------------------------------------------------
# State (held on a single instance to keep mutation explicit and avoid the
# `global` statement at every call site)
# --------------------------------------------------------------------------


class _State:
    """Mutable profiler state — one instance, mutated in place by callbacks."""

    def __init__(self) -> None:
        # Linear event log: (kind, function_name, ns_since_reset).
        # kind is "e" (enter) or "x" (exit). Self-time computation walks this.
        self.events: list[tuple[str, str, int]] = []
        # Live call stack — names of functions entered but not yet exited.
        # Non-empty at crash time means execution was inside those frames.
        self.call_stack: list[str] = []
        # Wall-clock anchor (perf_counter_ns); event timestamps are relative.
        self.t0: int = 0
        # Kept-alive refs to the ctypes thunks. Without these, Python may GC
        # the closures and the JIT will call into freed memory.
        self.callback_refs: list[Any] = []
        # Has install() been called?
        self.enabled: bool = False
        # Debug side-table: function_name -> (source_path, line). Populated
        # by codegen.py and handed in via set_source_map(). Empty dict is
        # fine — output just falls back to bare function names.
        self.source_map: dict[str, tuple[str, int]] = {}
        # Sampling thread: started by start_sampling() when --profile-sample
        # is passed. Reads call_stack at fixed intervals and bumps a
        # histogram of stacks-as-seen — gives a sample-based view that
        # complements the deterministic blame report (the blame says where
        # time was *attributed*; samples say where it was *caught*).
        self.sampling_thread: Optional[threading.Thread] = None
        self.sampling_stop: Optional[threading.Event] = None
        self.sampling_interval_s: float = 0.001  # 1 ms = 1 kHz default
        self.sample_hist: dict[str, int] = {}


_S = _State()


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def reset() -> None:
    """Clear accumulated profile state. Call before each program run."""
    _S.events.clear()
    _S.call_stack.clear()
    _S.sample_hist.clear()
    _S.t0 = time.perf_counter_ns()


def install() -> None:
    """Allocate the ctypes thunks and mark the profiler enabled.

    Idempotent: the actual JIT symbol registration happens in fast_jit.py via
    JITLibraryBuilder.import_symbol(name, addr) — see jit_symbols() below.
    """
    if _S.enabled:
        return

    # void(*)(const char *) signature on both — the AILang side passes a
    # pointer to a NUL-terminated function name constant.
    sig = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    enter_cb = sig(_on_enter)
    exit_cb = sig(_on_exit)
    _S.callback_refs.extend([enter_cb, exit_cb])
    _S.enabled = True


def jit_symbols() -> dict[str, int]:
    """Return {symbol_name: address} for the ORC linker to import.

    install() must have been called first. fast_jit.py iterates this dict
    and calls JITLibraryBuilder.import_symbol(name, addr) on each entry,
    which is the supported way to make Python callables visible to LLJIT-
    compiled IR.
    """
    if not _S.enabled or len(_S.callback_refs) < 2:
        return {}
    enter_cb, exit_cb = _S.callback_refs[0], _S.callback_refs[1]
    enter_addr = ctypes.cast(enter_cb, ctypes.c_void_p).value
    exit_addr = ctypes.cast(exit_cb, ctypes.c_void_p).value
    if enter_addr is None or exit_addr is None:
        raise RuntimeError("profiler: failed to obtain ctypes callback addresses")
    return {
        "__ailang_prof_enter": enter_addr,
        "__ailang_prof_exit": exit_addr,
    }


def is_enabled() -> bool:
    """True once install() has run."""
    return _S.enabled


def set_source_map(mapping: dict[str, tuple[str, int]]) -> None:
    """Register a func_name -> (source_path, line) map for prettier output.

    fast_jit.py wires this from codegen.source_map after generate() runs.
    Safe to call with an empty dict; output simply falls back to bare names.
    """
    _S.source_map = dict(mapping)


# --------------------------------------------------------------------------
# Callbacks (called from JIT'd code via ctypes)
# --------------------------------------------------------------------------


def _on_enter(name_ptr: int) -> None:
    """Record a function-entry event. Runs on every instrumented call."""
    if name_ptr is None or name_ptr == 0:
        return
    name = ctypes.string_at(name_ptr).decode("utf-8", errors="replace")
    now = time.perf_counter_ns() - _S.t0
    _S.events.append(("e", name, now))
    _S.call_stack.append(name)


def _on_exit(name_ptr: int) -> None:
    """Record a function-exit event. Runs on every return."""
    if name_ptr is None or name_ptr == 0:
        return
    name = ctypes.string_at(name_ptr).decode("utf-8", errors="replace")
    now = time.perf_counter_ns() - _S.t0
    _S.events.append(("x", name, now))
    if _S.call_stack:
        # Tolerate stack mismatches — if codegen missed an exit hook somewhere,
        # we still want subsequent frames to balance. Pop blindly rather than
        # asserting equality.
        _S.call_stack.pop()


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


def compute_blame() -> list[dict[str, Any]]:
    """Walk the event log and compute per-function statistics.

    Returns a list of dicts sorted by self_ns (descending). Each dict has:
        name      — function name
        calls     — number of times entered
        total_ns  — wall time spent in this function (including children)
        self_ns   — wall time spent in this function (excluding children)

    Self-time is computed by maintaining a stack of (entry_ns, child_ns)
    pairs: when a function exits, its self_ns is (exit - entry) - child_ns,
    and that elapsed is added to the parent's child_ns.
    """
    stats: dict[str, dict[str, int]] = {}
    # Stack frames: [name, entry_ns, accumulated_child_ns]
    stack: list[list[Any]] = []

    for kind, name, ns in _S.events:
        if kind == "e":
            stack.append([name, ns, 0])
            continue
        # kind == "x"
        if not stack:
            # Spurious exit — ignore.
            continue
        frame = stack.pop()
        entered_name, entered_ns, child_ns = frame
        elapsed = max(ns - entered_ns, 0)
        slot = stats.setdefault(entered_name, {"calls": 0, "total_ns": 0, "self_ns": 0})
        slot["calls"] += 1
        slot["total_ns"] += elapsed
        self_ns = max(elapsed - child_ns, 0)
        slot["self_ns"] += self_ns
        if stack:
            stack[-1][2] += elapsed

    rows: list[dict[str, Any]] = [{"name": n, **s} for n, s in stats.items()]
    rows.sort(key=lambda r: cast(int, r["self_ns"]), reverse=True)
    return rows


def crash_frames() -> list[str]:
    """Names still on the call stack — the live frames when execution stopped.

    Order is outermost first, innermost last (the function that was actually
    executing is at the end). Empty list means the program completed cleanly.
    """
    return list(_S.call_stack)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def _fmt_ns(ns: int) -> str:
    """Pretty-print a nanosecond duration in the largest fitting unit."""
    if ns >= 1_000_000_000:
        return f"{ns / 1_000_000_000:7.3f}s"
    if ns >= 1_000_000:
        return f"{ns / 1_000_000:7.2f}ms"
    if ns >= 1_000:
        return f"{ns / 1_000:7.1f}µs"
    return f"{ns:7d}ns"


def _fmt_location(name: str) -> str:
    """Render `name @ basename:line` if we have a source mapping, else `name`.

    The basename keeps lines short — the full path is rarely useful in a
    terminal report and just makes the columns wrap. Tools that want the
    full path can call source_map() directly.
    """
    loc = _S.source_map.get(name)
    if not loc:
        return name
    path, line = loc
    if not path:
        return name
    # os.path.basename without importing os: split on either separator.
    base = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if line > 0:
        return f"{name} @ {base}:{line}"
    return f"{name} @ {base}"


def source_map() -> dict[str, tuple[str, int]]:
    """Read-only view of the func_name -> (path, line) mapping."""
    return dict(_S.source_map)


def format_blame(top_n: int = 25) -> str:
    """systemd-analyze blame-style report. Empty string if no events."""
    rows = compute_blame()
    if not rows:
        return ""

    total_self = sum(r["self_ns"] for r in rows)
    total_calls = sum(r["calls"] for r in rows)

    lines = [
        "",
        "=== profile (systemd-analyze blame style) ===",
        f"  {len(rows)} functions, {total_calls} calls, "
        f"{_fmt_ns(total_self)} total self-time",
        "",
        f"  {'self':>9}  {'total':>9}  {'calls':>7}  {'per-call':>9}  function",
        f"  {'----':>9}  {'-----':>9}  {'-----':>7}  {'--------':>9}  --------",
    ]
    for row in rows[:top_n]:
        per_call = row["self_ns"] // row["calls"] if row["calls"] else 0
        lines.append(
            f"  {_fmt_ns(row['self_ns'])}  {_fmt_ns(row['total_ns'])}  "
            f"{row['calls']:>7}  {_fmt_ns(per_call)}  {_fmt_location(row['name'])}"
        )
    if len(rows) > top_n:
        lines.append(f"  ... ({len(rows) - top_n} more functions truncated)")
    lines.append("")
    return "\n".join(lines)


def format_crash_frames() -> str:
    """Render live call stack for crash banner. Empty string if stack is empty."""
    frames = crash_frames()
    if not frames:
        return ""
    lines = ["    AILang call stack at crash (outermost first):"]
    for depth, name in enumerate(frames):
        prefix = "      " + ("  " * depth)
        lines.append(f"{prefix}{_fmt_location(name)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Flame-graph output (Brendan Gregg's "folded" format)
# --------------------------------------------------------------------------


def format_folded_stacks() -> str:
    """Walk the event log into folded-stack lines for flamegraph.pl.

    The format is:
        outer;middle;inner self_ns_count\n
    Each stack frame's self-time becomes one line; stacks with the same
    spelling collapse into a single line with summed counts. Pipe the
    result into Brendan Gregg's flamegraph.pl:

        ailang --profile --profile-flame=out.folded foo.ail
        flamegraph.pl out.folded > flame.svg

    Self-time vs total-time matters here: flame graphs visualize where
    the program *was actually executing*, not where it was waiting on a
    callee. We emit one count per `self_ns` nanosecond at the leaf, so
    the column widths in the rendered SVG line up with self-time.
    """
    # As we walk, maintain (name, last_t) per stack frame so we can attribute
    # the elapsed since the last sub-event to "this frame as leaf" — that's
    # the slice of time the frame was actually on top of the stack.
    folded_counts: dict[str, int] = {}
    stack_names: list[str] = []
    last_t = 0
    for kind, name, ns in _S.events:
        if stack_names:
            leaf_self = ns - last_t
            if leaf_self > 0:
                key = ";".join(stack_names)
                folded_counts[key] = folded_counts.get(key, 0) + leaf_self
        if kind == "e":
            stack_names.append(name)
        else:
            if stack_names:
                stack_names.pop()
        last_t = ns
    # Sort lexicographically so the output is stable across runs (helps
    # when diffing two profiles); flamegraph.pl doesn't care about order.
    return "\n".join(f"{stk} {cnt}" for stk, cnt in sorted(folded_counts.items()))


def write_folded_stacks(path: str) -> int:
    """Write folded-stack output to `path`. Returns lines written."""
    text = format_folded_stacks()
    if not text:
        return 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.write("\n")
    return text.count("\n") + 1


# --------------------------------------------------------------------------
# Sampling thread (--profile-sample) — complements the deterministic blame.
#
# The blame report tells you where time was *attributed* (every entry/exit
# logged, exact). Sampling tells you where the program was *caught*: a thread
# wakes every N ms, peeks at the live call_stack, and bumps a histogram.
#
# Why have both? The deterministic profile slows the program by 1.5-3x, which
# can shift the workload (e.g. stretch tight loops so a previously-rare branch
# starts dominating). Sampling at 1 kHz adds essentially zero overhead and
# captures the same workload the user ships. When the two views agree, you
# trust both. When they diverge, the sampling view is the one to listen to.
# --------------------------------------------------------------------------


def _sampler_loop() -> None:
    """Background thread body — peek and bump until told to stop."""
    stop = _S.sampling_stop
    if stop is None:
        return
    interval = _S.sampling_interval_s
    while not stop.is_set():
        # Snapshot the call stack. Python list reads are not atomic w.r.t.
        # concurrent appends/pops from the ctypes thunk thread, but a torn
        # read just produces a momentarily-wrong key — the histogram is
        # statistical, so a few stale samples don't matter.
        frames = list(_S.call_stack)
        if frames:
            key = ";".join(frames)
            _S.sample_hist[key] = _S.sample_hist.get(key, 0) + 1
        stop.wait(interval)


def start_sampling(interval_s: float = 0.001) -> None:
    """Spawn the sampling thread. Idempotent."""
    if _S.sampling_thread is not None:
        return
    _S.sampling_interval_s = max(interval_s, 0.0001)  # cap at 10 kHz
    _S.sampling_stop = threading.Event()
    _S.sampling_thread = threading.Thread(
        target=_sampler_loop, name="ailang-prof-sampler", daemon=True
    )
    _S.sampling_thread.start()


def stop_sampling() -> None:
    """Signal the sampler to stop and join. Safe to call when not started."""
    if _S.sampling_thread is None:
        return
    if _S.sampling_stop is not None:
        _S.sampling_stop.set()
    _S.sampling_thread.join(timeout=0.5)
    _S.sampling_thread = None
    _S.sampling_stop = None


def is_sampling() -> bool:
    """True between start_sampling() and stop_sampling()."""
    return _S.sampling_thread is not None


def format_sample_hotspots(top_n: int = 10) -> str:
    """Render top-N sampled stacks as a compact hotspot list.

    Each line shows: percentage, sample count, leaf-frame location, and
    the truncated call chain leading to it. Empty string when nothing
    was sampled (e.g. program ran in <1 ms).
    """
    if not _S.sample_hist:
        return ""
    items = sorted(_S.sample_hist.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(_S.sample_hist.values())
    lines = [
        "",
        f"=== sampled hotspots ({total} samples @ "
        f"{int(1.0 / _S.sampling_interval_s)} Hz) ===",
        "",
    ]
    for stk, cnt in items[:top_n]:
        pct = 100.0 * cnt / total
        chain = stk.split(";")
        leaf = chain[-1]
        if len(chain) > 4:
            display_chain = "...;" + ";".join(chain[-3:])
        else:
            display_chain = ";".join(chain)
        lines.append(f"  {pct:5.1f}%  ({cnt:>5})  {_fmt_location(leaf)}")
        lines.append(f"          {display_chain}")
    if len(items) > top_n:
        lines.append(f"  ... ({len(items) - top_n} more stacks truncated)")
    lines.append("")
    return "\n".join(lines)

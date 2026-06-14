"""
Phase timing for the AILang compiler driver.

A small, dependency-free profiler. Each compiler phase (lex, parse,
imports, type collect, ownership, helper scan, C emit, LLVM emit, gcc
invocation, ...) wraps its work in a ``Phase`` context manager; the
active ``PhaseProfile`` records elapsed wall time per phase. At the
end of a run the driver prints a column-aligned report when the user
passes ``--profile-phases``.

Design notes:

- ``time.perf_counter`` is the right clock here -- monotonic, sub-microsecond,
  unaffected by wall-clock adjustments. ``time.process_time`` would miss
  subprocess gcc time, which we very much want to count.
- Phases nest. ``with Phase("emit"):`` may contain ``with Phase("emit.string"):``
  internally, and we want both rows in the report. The collector keeps a stack
  so child phases attribute correctly to their parent for the indentation in
  the report (without subtracting -- both raw and parent-inclusive numbers
  matter for "where does the time go").
- The profile is process-global. A compile-and-link invocation runs a single
  pipeline; we don't need per-thread isolation. If the driver ever forks
  per-file workers, each worker gets its own profile.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class _PhaseRecord:
    name: str
    elapsed_ms: float
    depth: int


class PhaseProfile:
    """Collects ``(phase_name, elapsed_ms, depth)`` rows.

    Singleton-ish: a module-level ``_PROFILE`` is the default instance the
    ``Phase`` context manager talks to. Tests / parallel drivers can construct
    their own and pass it explicitly.
    """

    def __init__(self) -> None:
        self._records: list[_PhaseRecord] = []
        self._stack: list[tuple[str, float]] = []  # (name, start_perf_counter)

    def enter(self, name: str) -> None:
        self._stack.append((name, time.perf_counter()))

    def exit(self) -> None:
        if not self._stack:
            return  # defensive: unmatched exit shouldn't crash a release build
        name, start = self._stack.pop()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        depth = len(self._stack)
        self._records.append(_PhaseRecord(name, elapsed_ms, depth))

    def report(self, indent_str: str = "  ") -> str:
        """Return a multi-line column-aligned report of all recorded phases."""
        if not self._records:
            return "(no phases recorded)"
        # Records are appended in completion order; for human reading we want
        # the order they STARTED. depth is enough to reconstruct nesting,
        # but we do want the start-order. Easier: emit in reverse-completion
        # of the same depth and rely on the fact that a parent always finishes
        # AFTER its children -- so simply reversing within each depth band
        # gives start-order. Practical compromise: just print in completion
        # order, which is start-order for siblings since we record on exit.
        # (A child finishes before its parent; nothing within the parent
        # finishes after the parent's exit by construction.)
        rows: list[tuple[str, str]] = []
        for rec in self._records:
            label = indent_str * rec.depth + rec.name
            rows.append((label, f"{rec.elapsed_ms:>8.1f} ms"))
        label_width = max(len(label) for label, _ in rows)
        lines = ["AILang compile profile:"]
        for label, ms in rows:
            lines.append(f"  {label.ljust(label_width)}  {ms}")
        total = sum(r.elapsed_ms for r in self._records if r.depth == 0)
        lines.append(f"  {'-' * label_width}  --------")
        lines.append(f"  {'total'.ljust(label_width)}  {total:>8.1f} ms")
        return "\n".join(lines)


_PROFILE: Optional[PhaseProfile] = None


def get_profile() -> Optional[PhaseProfile]:
    """Return the active profile, or None if profiling is disabled."""
    return _PROFILE


def enable_profiling() -> PhaseProfile:
    """Turn on profiling. Driver calls this when --profile-phases is passed."""
    global _PROFILE
    _PROFILE = PhaseProfile()
    return _PROFILE


def disable_profiling() -> None:
    """Turn profiling back off (mainly for tests)."""
    global _PROFILE
    _PROFILE = None


class Phase:
    """Context manager that records elapsed time for one compiler phase.

    Always cheap to use -- when profiling is disabled the no-op path is two
    attribute lookups per phase, so we don't need a feature gate at the
    call site.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "Phase":
        if _PROFILE is not None:
            _PROFILE.enter(self.name)
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if _PROFILE is not None:
            _PROFILE.exit()

"""Crash/runtime execution helpers for fast_jit."""

from __future__ import annotations

import atexit
import faulthandler
import os
import signal
import sys
from typing import Any

_ATEXIT_HOOK_INSTALLED: dict[str, bool] = {"installed": False}

_JIT_CRASH_HINT = (
    "    likely cause: null pointer deref, out-of-bounds array_get/set,\n"
    "                  uninitialized class field, or a builtin used in a\n"
    "                  way the JIT does not support (see codegen.py).\n"
    "    tip: re-run with --emit-llvm to inspect the IR, or transpile to\n"
    "         C and run under a sanitizer for a real stack trace.\n"
)


def install_profile_crash_atexit(source_file: str) -> None:
    """Register an atexit hook that prints the AILang call stack iff the
    profile run did not complete cleanly (i.e. there are still live frames)."""
    if _ATEXIT_HOOK_INSTALLED["installed"]:
        return
    _ATEXIT_HOOK_INSTALLED["installed"] = True

    def _on_exit() -> None:
        try:
            from runtime import profiler

            if not profiler.is_enabled():
                return
            frames = profiler.format_crash_frames()
            if not frames:
                return
            sys.stdout.flush()
            sys.stderr.write("\n=== AILang program terminated mid-call ===\n")
            if source_file:
                sys.stderr.write(f"    source: {source_file}\n")
            sys.stderr.write(frames + "\n")
            sys.stderr.flush()
        except (ImportError, AttributeError):
            pass

    atexit.register(_on_exit)


def print_jit_crash_banner(why: str, source_file: str) -> None:
    """Emit a uniform 'JIT execution crashed' banner to stderr."""
    sys.stdout.flush()
    sys.stderr.write(f"\n=== JIT execution crashed ({why}) ===\n")
    if source_file:
        sys.stderr.write(f"    while running: {source_file}\n")
    sys.stderr.write(_JIT_CRASH_HINT)
    try:
        from runtime import profiler

        if profiler.is_enabled():
            frames = profiler.format_crash_frames()
            if frames:
                sys.stderr.write(frames + "\n")
    except (ImportError, AttributeError):
        # Profiler not available - static hint above is still useful.
        pass
    sys.stderr.flush()


def run_jit_main(
    cfunc: Any,
    source_file: str,
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
) -> int:
    """Execute JIT'd main with optional crash trap and profiler reporting."""
    sys.stdout.flush()
    sys.stderr.flush()

    faulthandler.enable()

    def _on_fatal(signum: int, _frame: Any) -> None:
        name = (
            signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        )
        print_jit_crash_banner(f"signal {name}", source_file)
        os._exit(139)

    install_signal_handlers = (
        os.name != "nt" and os.getenv("AILANG_JIT_SIGNAL_TRAP", "0") == "1"
    )
    prev_segv: Any = None
    prev_abrt: Any = None
    if install_signal_handlers:
        try:
            prev_segv = signal.signal(signal.SIGSEGV, _on_fatal)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            prev_segv = None
        if hasattr(signal, "SIGABRT"):
            try:
                prev_abrt = signal.signal(signal.SIGABRT, _on_fatal)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                prev_abrt = None
    try:
        if sample_hz > 0:
            from runtime import profiler as _prof_mod

            _prof_mod.start_sampling(interval_s=1.0 / max(sample_hz, 1))
        try:
            result = int(cfunc())
        finally:
            if sample_hz > 0:
                from runtime import profiler as _prof_mod

                _prof_mod.stop_sampling()
        if profile:
            from runtime import profiler

            sys.stdout.flush()
            sys.stderr.write(profiler.format_blame())
            if sample_hz > 0:
                sys.stderr.write(profiler.format_sample_hotspots())
            if flame_path:
                lines = profiler.write_folded_stacks(flame_path)
                sys.stderr.write(
                    f"  flame data: {flame_path} ({lines} stacks). "
                    f"render with `flamegraph.pl {flame_path} > flame.svg`\n"
                )
            sys.stderr.flush()
        return result
    except OSError as exc:
        print_jit_crash_banner(f"OSError: {exc}", source_file)
        raise
    finally:
        if install_signal_handlers and prev_segv is not None:
            try:
                signal.signal(signal.SIGSEGV, prev_segv)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass
        if install_signal_handlers and prev_abrt is not None:
            try:
                signal.signal(signal.SIGABRT, prev_abrt)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass

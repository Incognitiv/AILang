#!/usr/bin/env python3
"""Session benchmark + safety snapshot harness."""

from __future__ import annotations

try:
    from .session_benchmark_cli import main
except ImportError:
    from session_benchmark_cli import main


if __name__ == "__main__":
    raise SystemExit(main())

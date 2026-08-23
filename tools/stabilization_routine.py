#!/usr/bin/env python3
"""One-command stabilization routine."""

from __future__ import annotations

try:
    from .stabilization_routine_flow import main
except ImportError:
    from stabilization_routine_flow import main


if __name__ == "__main__":
    raise SystemExit(main())

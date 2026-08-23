#!/usr/bin/env python3
"""Probe selected C ABI/constants from a small binding spec and emit AILang bindings."""

from __future__ import annotations

try:
    from .cbind_probe_emit import *  # noqa: F403
except ImportError:
    import sys
    from pathlib import Path

    TOOLS_DIR = Path(__file__).resolve().parent
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    from cbind_probe_emit import *  # type: ignore # noqa: F403


if __name__ == "__main__":
    raise SystemExit(main())

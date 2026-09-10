#!/usr/bin/env python3
"""Compatibility entrypoint for the AILang Golden Gate.

Use ``tools/golden_gate.py`` for new automation.  The old command remains so
existing external scripts fail neither silently nor during the transition.
"""

from golden_gate import GATE_STEP_IDS, main

__all__ = ["GATE_STEP_IDS", "main"]


if __name__ == "__main__":
    raise SystemExit(main())

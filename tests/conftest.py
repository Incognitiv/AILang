"""Test-suite bootstrap that makes repository-local packages importable.

The installed package already exposes ``source/``.  Direct checkout/ZIP test
runs should behave the same way instead of depending on another test module
having mutated ``sys.path`` earlier in collection order.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

"""Shared fixed-array type parsing helpers."""

from __future__ import annotations

from typing import Optional, Tuple


def parse_fixed_array_type_spec(atype: str) -> Optional[Tuple[str, int]]:
    """Parse canonical fixed-array type string: ``[elem;N]``."""
    spec = atype.strip()
    if not (spec.startswith("[") and spec.endswith("]")):
        return None
    inner = spec[1:-1].strip()
    if ";" not in inner:
        return None
    elem_part, size_part = inner.rsplit(";", 1)
    elem_type = elem_part.strip()
    size_text = size_part.strip()
    if not elem_type or not size_text.isdigit():
        return None
    size = int(size_text)
    if size <= 0:
        return None
    return elem_type, size

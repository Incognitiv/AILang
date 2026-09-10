#!/usr/bin/env python3
"""Temporary patcher that makes the parser consume canonical numeric joins."""

from pathlib import Path

PATH = Path("source/parser/return_type_inference.py")
text = PATH.read_text(encoding="utf-8")

old_import = '''from type_semantics import (
    ConversionKind,
    canonical_type_name,
    classify_conversion,
    is_implicit_conversion,
)
'''
new_import = '''from type_semantics import (
    ConversionKind,
    canonical_type_name,
    classify_conversion,
    is_implicit_conversion,
    join_numeric_types,
)
'''
if old_import not in text:
    raise SystemExit("type_semantics import anchor not found")
text = text.replace(old_import, new_import, 1)

old_constants = '''_INT_RE = re.compile(r"^([iu])(8|16|32|64|128|256|512|1024|2048|4096|8192)$")
_INT_WIDTHS = (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)

'''
if old_constants not in text:
    raise SystemExit("legacy numeric constant anchor not found")
text = text.replace(old_constants, "", 1)

old_join = '''def _join_ints(a: str, b: str) -> str | None:
    ma, mb = _INT_RE.match(a), _INT_RE.match(b)
    if not ma or not mb:
        return None
    sa, wa = ma.group(1), int(ma.group(2))
    sb, wb = mb.group(1), int(mb.group(2))
    if sa == sb:
        return f"{sa}{max(wa, wb)}"
    signed_w = wa if sa == "i" else wb
    unsigned_w = wa if sa == "u" else wb
    needed = max(signed_w, unsigned_w + 1)
    width = next((w for w in _INT_WIDTHS if w >= needed), None)
    return f"i{width}" if width is not None else None


def _join(a: str, b: str) -> str | None:
    a, b = _canon(a), _canon(b)
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    ints = _join_ints(a, b)
    if ints:
        return ints
    floats = {"f32": 24, "f64": 53, "f128": 113}
    if a in floats and b in floats:
        return max((a, b), key=lambda t: floats[t])
    # Mixed integer/float inference is allowed only when every value in the
    # integer type is exactly representable by that float type.
    m = _INT_RE.match(b)
    if a in floats and m is not None:
        bits = int(m.group(2)) - (1 if m.group(1) == "i" else 0)
        return a if bits <= floats[a] else None
    if b in floats and _INT_RE.match(a):
        return _join(a=b, b=a)
    return None
'''
new_join = '''def _join(a: str, b: str) -> str | None:
    a, b = _canon(a), _canon(b)
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    return join_numeric_types(a, b)
'''
if old_join not in text:
    raise SystemExit("legacy numeric join anchor not found")
text = text.replace(old_join, new_join, 1)

PATH.write_text(text, encoding="utf-8")

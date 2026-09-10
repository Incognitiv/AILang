"""Conformance tests while numeric joins move from parser logic into semantics."""

from parser.return_type_inference import _join

from type_semantics import INT_WIDTHS, join_numeric_types


def _numeric_types() -> list[str]:
    integers = [f"{sign}{width}" for sign in ("i", "u") for width in INT_WIDTHS]
    return integers + ["f32", "f64", "f128"]


def test_canonical_numeric_join_matches_existing_frontend_exhaustively() -> None:
    numeric_types = _numeric_types()
    mismatches = []
    for left in numeric_types:
        for right in numeric_types:
            canonical = join_numeric_types(left, right)
            frontend = _join(left, right)
            if canonical != frontend:
                mismatches.append((left, right, canonical, frontend))

    assert not mismatches, mismatches[:20]
    assert len(numeric_types) ** 2 == 625

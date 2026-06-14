"""Identifier-mangling utilities used during parsing and codegen.

Lives in ``parser/`` (not ``codegen/``) because the parser already
constructs mangled names when instantiating generic types -- the codegen
side is downstream and just consumes them. Keeping this here breaks the
``parser`` <-> ``codegen`` cycle that existed when ``mangle_generic_name``
lived in ``codegen.monomorphize``.
"""

from __future__ import annotations


def mangle_generic_name(base_name: str, type_args: list[str]) -> str:
    """Create a mangled name for a generic instantiation.

    Example: Pair[int, string] -> Pair_int_string
    Handles nested generics: List[Pair[int, string]] -> List_Pair_int_string
    """
    # Sanitize all special characters that could appear in nested generics.
    # Brackets, commas, spaces all become underscores.
    sanitized_args = [
        arg.replace("[", "_")
        .replace("]", "")
        .replace(" ", "_")
        .replace(",", "_")
        .replace("__", "_")  # Collapse double underscores
        .strip("_")
        for arg in type_args
    ]
    return f"{base_name}_{'_'.join(sanitized_args)}"

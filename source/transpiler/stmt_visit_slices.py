"""C statement helpers for slice/view declarations."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str


def try_emit_fixed_array_slice_alias(self, node: A.VarDecl) -> bool:
    """Lower local slice[int] aliases over fixed int arrays without allocation."""
    if not _is_i64_slice_type(node.type_name):
        return False
    if not isinstance(node.init_value, A.Variable):
        return False

    source_name = node.init_value.name
    elem_count = _fixed_array_len_hint(self, source_name)
    if elem_count is None:
        return False
    if not _is_i64_fixed_array_source(self, source_name):
        return False

    target = self._mangle_var(node.var_name)
    source = self._mangle_var(source_name)
    self.emit(f"{target}.data = {source};")
    self.emit(f"{target}.length = {elem_count};")
    self.emit(f"{target}.capacity = {elem_count};")
    return True


def _is_i64_slice_type(type_spec: object) -> bool:
    text = parsed_type_to_str(type_spec).strip().lower()
    return text in ("slice[int]", "view[int]", "slice[i64]", "view[i64]")


def _fixed_array_len_hint(self, source_name: str) -> int | None:
    hints = getattr(self, "_array_len_hints", {})
    if not isinstance(hints, dict):
        return None
    scoped = (self.current_function, source_name)
    if scoped in hints:
        return int(hints[scoped])
    global_key = (None, source_name)
    if global_key in hints:
        return int(hints[global_key])
    return None


def _is_i64_fixed_array_source(self, source_name: str) -> bool:
    vtype = getattr(self, "_var_types", {}).get(source_name, "")
    if not isinstance(vtype, str):
        return False
    if not hasattr(self, "_resolve_type_alias_spec") or not hasattr(
        self, "_parse_fixed_array_type_spec"
    ):
        return False
    resolved = self._resolve_type_alias_spec(vtype)
    fixed = self._parse_fixed_array_type_spec(resolved)
    if fixed is None:
        return False
    elem_text, _size = fixed
    elem_text = elem_text.strip().lower()
    return elem_text in ("int", "i64")

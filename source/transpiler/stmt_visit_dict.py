from __future__ import annotations

import hashlib
from parser import ast as A

from transpiler.codegen_int_ranges import remember_fixed_dict_range
from transpiler.dict_specialization import dict_literal_stack_capacity


def _can_stack_back_dict_literal(self, node: A.DictLit) -> bool:
    if self.current_function is None or self.indent != 1:
        return False
    if not node.pairs:
        return True
    return all(isinstance(key, A.StringLit) for key, _value in node.pairs)


def _emit_stack_dict_literal_assign(self, var_name: str, node: A.DictLit) -> bool:
    if not _can_stack_back_dict_literal(self, node):
        return False
    var = self._mangle_var(var_name)
    counter = getattr(self, "_stack_dict_literal_counter", 0)
    setattr(self, "_stack_dict_literal_counter", counter + 1)
    fixed = getattr(self, "_fixed_dict_literal_slots", {})
    if var_name in fixed:
        values = {}
        for key, _item_val in node.pairs:
            if not isinstance(key, A.StringLit) or key.value in values:
                continue
            digest = hashlib.blake2s(key.value.encode("utf-8"), digest_size=4)
            cvar = f"__ailang_dict_{var}_{counter}_{digest.hexdigest()}"
            self.emit(f"int64_t {cvar};")
            values[key.value] = cvar
        for key, item_val in node.pairs:
            if isinstance(key, A.StringLit) and key.value in values:
                self.emit(f"{values[key.value]} = {self.expr(item_val)};")
                remember_fixed_dict_range(self, var_name, key.value, item_val)
        self._fixed_dict_scalar_values[var_name] = values
        return True
    base = f"__ailang_stack_dict_{var}_{counter}"
    entries = f"{base}_entries"
    capacity = dict_literal_stack_capacity(len(node.pairs))
    self.emit(f"ailang_dict_entry {entries}[{capacity}] = {{0}};")
    self.emit(f"ailang_dict {base} = {{ {entries}, {capacity}, 0, 0, 0 }};")
    self.emit(f"if ({var}) dict_destroy_fn({var});")
    self.emit(f"{var} = &{base};")
    for key, item_val in node.pairs:
        self.emit(f"dict_set({var}, {self.expr(key)}, {self.expr(item_val)});")
    return True


def _emit_dict_literal_assign(self, var_name: str, node: A.DictLit) -> None:
    """Emit dict literal assignment, cleaning an old tracked local first."""
    tracked = var_name in (getattr(self, "_dict_locals_for_cleanup", None) or [])
    if tracked and _emit_stack_dict_literal_assign(self, var_name, node):
        return
    var = self._mangle_var(var_name)
    target = "__pre_assign" if tracked else var
    if tracked:
        self.emit(f"{{ typeof({var}) __pre_assign = dict_new();")
    else:
        self.emit(f"{var} = dict_new();")
    for key, item_val in node.pairs:
        self.emit(f"dict_set({target}, {self.expr(key)}, {self.expr(item_val)});")
    if tracked:
        self.emit(f"if ({var}) dict_destroy_fn({var});")
        self.emit(f"{var} = __pre_assign;")
        self.emit("}")

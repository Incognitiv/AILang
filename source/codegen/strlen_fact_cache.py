"""LLVM strlen fact cache for straight-line codegen.

The cache is deliberately conservative: it records only explicit
``len_var = strlen(source_var)`` assignments and invalidates on writes to
either variable.  Control-flow merge analysis can extend this later.
"""

from __future__ import annotations

from parser.ast import Assign, Call, Variable
from typing import Any

from llvmlite import ir


def invalidate_strlen_facts(cg: Any, written_var: str) -> None:
    cache = getattr(cg, "_llvm_strlen_cache", None)
    if not isinstance(cache, dict):
        return
    for source, (length_var, _value) in list(cache.items()):
        if written_var == source or written_var == length_var:
            cache.pop(source, None)


def clear_strlen_facts(cg: Any) -> None:
    cache = getattr(cg, "_llvm_strlen_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def maybe_register_strlen_fact(cg: Any, node: Assign, value: ir.Value) -> None:
    builder = getattr(cg, "current_builder", None)
    block_name = str(getattr(getattr(builder, "block", None), "name", ""))
    if block_name != "entry":
        return
    if not isinstance(node.value, Call):
        return
    if node.value.name.lower() != "strlen" or len(node.value.args) != 1:
        return
    (source,) = node.value.args
    if not isinstance(source, Variable):
        return
    cache = getattr(cg, "_llvm_strlen_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(cg, "_llvm_strlen_cache", cache)
    cache[source.name] = (node.var_name, value)


def register_strlen_fact(cg: Any, source_name: str, value: ir.Value) -> None:
    cache = getattr(cg, "_llvm_strlen_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(cg, "_llvm_strlen_cache", cache)
    cache[source_name] = ("", value)


def lookup_strlen_fact(cg: Any, string_arg: Any) -> ir.Value | None:
    if not isinstance(string_arg, Variable):
        return None
    cache = getattr(cg, "_llvm_strlen_cache", None)
    if not isinstance(cache, dict):
        return None
    fact = cache.get(string_arg.name)
    if fact is None:
        return None
    _length_var, value = fact
    return value


def register_value_strlen_fact(cg: Any, value: ir.Value, length: ir.Value) -> None:
    cache = getattr(cg, "_llvm_value_strlen_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(cg, "_llvm_value_strlen_cache", cache)
    cache[id(value)] = length


def consume_value_strlen_fact(cg: Any, value: ir.Value) -> ir.Value | None:
    cache = getattr(cg, "_llvm_value_strlen_cache", None)
    if not isinstance(cache, dict):
        return None
    fact = cache.pop(id(value), None)
    return fact if isinstance(fact, ir.Value) else None

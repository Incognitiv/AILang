"""Import views preserve precedence and avoid transitive dictionary copies."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler.module_symbols import ModuleSymbols


def symbols(**entries: int) -> ModuleSymbols[int]:
    table: ModuleSymbols[int] = ModuleSymbols()
    for name, value in entries.items():
        table[name] = value
    return table


def test_local_definitions_and_first_import_win() -> None:
    root = symbols(own=1)
    root.include(symbols(own=9, conflict=2, left=3))
    root.include(symbols(conflict=7, right=4))
    assert root.copy() == {"own": 1, "conflict": 2, "left": 3, "right": 4}
    assert list(root.items()) == list(root.copy().items())
    assert root["conflict"] == 2
    assert root.get("missing") is None
    assert "right" in root
    with pytest.raises(KeyError):
        _ = root["missing"]


def test_diamond_keeps_depth_first_first_definition() -> None:
    leaf = symbols(shared=11, leaf=5)
    left = symbols(left=2)
    left.include(leaf)
    right = symbols(shared=22, right=3)
    right.include(leaf)
    root = symbols(root=1)
    root.include(left)
    root.include(right)
    assert list(root.items()) == [
        ("root", 1), ("left", 2), ("shared", 11),
        ("leaf", 5), ("right", 3),
    ]
    assert root["shared"] == 11
    assert len(list(root._tables())) == 4


def test_repeated_import_does_not_add_storage() -> None:
    root = symbols(a=1)
    child = symbols(b=2)
    for _ in range(100):
        root.include(child)
    assert len(root._imports) == 1
    assert len(root) == 2


def test_deep_graph_uses_iterative_lookup_and_iteration() -> None:
    tables = []
    for index in range(2500):
        table = symbols(**{f"symbol_{index}": index})
        if tables:
            table.include(tables[-1])
        tables.append(table)
    root = tables[-1]
    assert root["symbol_0"] == 0
    assert len(root) == 2500
    assert len(list(root.items())) == 2500
    assert sum(len(table._local) for table in tables) == 2500
    assert sum(len(table._imports) for table in tables) == 2499


def test_copy_is_detached_from_the_shared_tables() -> None:
    child = symbols(value=1)
    root = symbols()
    root.include(child)
    copied = root.copy()
    copied["value"] = 99
    assert root["value"] == 1


def test_import_views_retain_exact_node_identity() -> None:
    child: ModuleSymbols[object] = ModuleSymbols()
    node = object()
    child["function"] = node
    root: ModuleSymbols[object] = ModuleSymbols()
    root.include(child)
    assert root["function"] is node
    assert root.copy()["function"] is node


def test_indirect_cycles_do_not_hang_introspection() -> None:
    one = symbols(one=1)
    two = symbols(two=2)
    one.include(two)
    two.include(one)
    assert one.copy() == {"one": 1, "two": 2}


def test_direct_self_import_is_rejected() -> None:
    root = symbols()
    with pytest.raises(ValueError):
        root.include(root)


def test_items_do_not_search_graph_again_for_every_key(monkeypatch) -> None:
    root = symbols(a=1, b=2)
    root.include(symbols(c=3))
    def forbidden(*args):
        raise AssertionError("items must use one traversal")
    monkeypatch.setattr(ModuleSymbols, "__getitem__", forbidden)
    assert list(root.items()) == [("a", 1), ("b", 2), ("c", 3)]


def test_order_matches_eager_merge_for_a_dag() -> None:
    eager: list[dict[str, int]] = []
    shared: list[ModuleSymbols[int]] = []
    for index in range(100):
        local = {f"key_{index}": index, f"collision_{index % 7}": index}
        flat = dict(local)
        view = symbols(**local)
        for previous in (index - 1, index - 4, index - 7):
            if previous < 0:
                continue
            for name, value in eager[previous].items():
                flat.setdefault(name, value)
            view.include(shared[previous])
        eager.append(flat)
        shared.append(view)
        assert list(view.items()) == list(flat.items())

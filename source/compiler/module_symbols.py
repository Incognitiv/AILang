"""Ordered shared import bindings without copying transitive symbol closures."""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, Mapping
from typing import Generic, TypeVar

T = TypeVar("T")


class ModuleSymbols(Mapping[str, T], Generic[T]):
    """Local definitions precede imported views in source import order.

    Import graphs must be acyclic and completed before code generation. Views
    retain node identities; copy() is the explicit flat-dictionary boundary.
    """

    def __init__(self) -> None:
        self._local: dict[str, T] = {}
        self._imports: list[ModuleSymbols[T]] = []
        self._import_ids: set[int] = set()

    def __setitem__(self, name: str, value: T) -> None:
        self._local[name] = value

    def include(self, symbols: ModuleSymbols[T]) -> None:
        """Share a completed import; repeated imports do not duplicate edges."""
        if symbols is self:
            raise ValueError("a module cannot include its own symbol table")
        identity = id(symbols)
        if identity not in self._import_ids:
            self._imports.append(symbols)
            self._import_ids.add(identity)

    def _tables(self) -> Iterator[ModuleSymbols[T]]:
        pending = [self]
        seen: set[int] = set()
        while pending:
            table = pending.pop()
            identity = id(table)
            if identity in seen:
                continue
            seen.add(identity)
            yield table
            pending.extend(reversed(table._imports))

    def __getitem__(self, name: str) -> T:
        for table in self._tables():
            if name in table._local:
                return table._local[name]
        raise KeyError(name)

    def pairs(self) -> Iterator[tuple[str, T]]:
        """Visit each table once and yield the first definition of each name."""
        names: set[str] = set()
        for table in self._tables():
            for name, value in table._local.items():
                if name not in names:
                    names.add(name)
                    yield name, value

    def __iter__(self) -> Iterator[str]:
        for name, _value in self.pairs():
            yield name

    def __len__(self) -> int:
        return sum(1 for _name in self)

    def items(self) -> ItemsView[str, T]:
        return _SymbolItems(self)

    def copy(self) -> dict[str, T]:
        """Materialize only when a consumer actually requires a flat mapping."""
        return dict(self.pairs())


class _SymbolItems(ItemsView[str, T]):
    """Avoid Mapping.items() doing another graph search for every symbol."""

    def __init__(self, symbols: ModuleSymbols[T]) -> None:
        super().__init__(symbols)
        self._symbols = symbols

    def __iter__(self) -> Iterator[tuple[str, T]]:
        return self._symbols.pairs()

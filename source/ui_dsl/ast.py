"""AST nodes for the canonical AILang UI authoring DSL."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UiValue:
    """A typed UI property value, preserving source-level units when present."""

    kind: str
    value: Any
    unit: str = ""
    line: int = 0
    col: int = 0

    def as_css(self) -> str:
        if self.kind == "dimension":
            return f"{self.value:g}{self.unit}"
        if self.kind == "string":
            return str(self.value)
        if self.kind == "bool":
            return "true" if self.value else "false"
        return str(self.value)


@dataclass(frozen=True)
class UiProperty:
    name: str
    value: UiValue
    line: int = 0
    col: int = 0


@dataclass
class UiNode:
    tag: str
    name: str = ""
    properties: list[UiProperty] = field(default_factory=list)
    children: list["UiNode"] = field(default_factory=list)
    line: int = 0
    col: int = 0

    def property(self, name: str) -> UiValue | None:
        for prop in reversed(self.properties):
            if prop.name == name:
                return prop.value
        return None

    def property_map(self) -> dict[str, UiValue]:
        return {prop.name: prop.value for prop in self.properties}


@dataclass(frozen=True)
class UiInclude:
    target: str
    resolved: Path | None = None
    line: int = 0
    col: int = 0


@dataclass
class UiDocument:
    path: Path | None = None
    includes: list[UiInclude] = field(default_factory=list)
    nodes: list[UiNode] = field(default_factory=list)

"""Parser for the canonical AILang UI authoring DSL.

The normal AILang parser still skips UI blocks so codegen remains stable.
This module is the semantic parser for the UI authoring surface.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple, Sequence

from lexer.scan import tokenize, unescape_string

from .ast import UiDocument, UiInclude, UiNode, UiProperty, UiValue
from .validation import validate_ui_document

RawToken = tuple[str, str, int, int]


class Token(NamedTuple):
    kind: str
    text: str
    line: int
    col: int


def _token_from_raw(token: RawToken | Token) -> Token:
    if isinstance(token, Token):
        return token
    kind, text, line, col = token
    return Token(kind=kind, text=text, line=line, col=col)


_UI_TOP_LEVEL_TAGS = {"window", "background", "font", "dock", "panel", "scrollable"}
_UI_VALUE_TOKENS = {
    "STRLIT",
    "INTERP_STRLIT",
    "UI_COLOR",
    "NUMBER",
    "FLOAT",
    "TRUE",
    "FALSE",
    "IDENT",
}
_UNIT_TOKENS = {"IDENT", "MOD"}
_KNOWN_UNITS = {"px", "pt", "em", "rem", "vh", "vw", "vmin", "vmax", "%"}
_PROPERTY_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class UiDslParser:
    def __init__(
        self,
        tokens: Sequence[RawToken | Token],
        *,
        source_path: Path | None = None,
        include_paths: Sequence[Path] = (),
        expand_includes: bool = False,
        seen: set[Path] | None = None,
    ):
        self.tokens = [_token_from_raw(token) for token in tokens]
        self.pos = 0
        self.source_path = source_path
        self.include_paths = list(include_paths)
        self.expand_includes = expand_includes
        self.seen = seen if seen is not None else set()

    def peek(self, offset: int = 0) -> Token | None:
        index = self.pos + offset
        if 0 <= index < len(self.tokens):
            return self.tokens[index]
        return None

    def peek_type(self, offset: int = 0) -> str:
        token = self.peek(offset)
        return token.kind if token else ""

    def peek_text(self, offset: int = 0) -> str:
        token = self.peek(offset)
        return token.text if token else ""

    def advance(self) -> Token:
        token = self.peek()
        if token is None:
            raise SyntaxError("Unexpected end of UI DSL input")
        self.pos += 1
        return token

    def expect(self, token_type: str) -> Token:
        token = self.advance()
        if token.kind != token_type:
            raise SyntaxError(
                f"Line {token.line}, Col {token.col}: expected {token_type}, got {token.kind} ({token.text!r})"
            )
        return token

    def parse_document(self) -> UiDocument:
        document = UiDocument(path=self.source_path)
        while self.peek() is not None:
            if self._is_include():
                include = self._parse_include()
                document.includes.append(include)
                if self.expand_includes and include.resolved is not None:
                    child = parse_ui_file(
                        include.resolved,
                        expand_includes=True,
                        include_paths=self.include_paths,
                        seen=self.seen,
                    )
                    document.includes.extend(child.includes)
                    document.nodes.extend(child.nodes)
                continue

            if self._is_node_start(top_level=True):
                document.nodes.append(self._parse_node())
                continue

            self._skip_non_ui_construct()
        validate_ui_document(document)
        return document

    def _is_include(self) -> bool:
        return (
            self.peek_type() in {"UI_INCLUDE", "IDENT"}
            and self.peek_text().lower() == "include"
            and self.peek_type(1) == "STRLIT"
        )

    def _parse_include(self) -> UiInclude:
        token = self.advance()
        target_token = self.expect("STRLIT")
        target = unescape_string(target_token.text)
        return UiInclude(
            target=target,
            resolved=self._resolve_include(target),
            line=token.line,
            col=token.col,
        )

    def _resolve_include(self, target: str) -> Path | None:
        candidates: list[Path] = []
        if self.source_path is not None:
            candidates.append(self.source_path.parent / target)
        candidates.extend(path / target for path in self.include_paths)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return None

    def _is_node_start(self, *, top_level: bool = False) -> bool:
        if self.peek_type() != "IDENT":
            return False
        if top_level and self.peek_text().lower() not in _UI_TOP_LEVEL_TAGS:
            return False
        return self._node_colon_offset() is not None

    def _node_colon_offset(self) -> int | None:
        for offset in range(1, 4):
            token_type = self.peek_type(offset)
            if token_type == "COLON":
                return offset
            if token_type not in {"IDENT", "STRLIT", "STRING"}:
                return None
        return None

    def _parse_node(self) -> UiNode:
        tag_token = self.expect("IDENT")
        tag = tag_token.text
        name = ""
        if self.peek_type() == "COLON":
            pass
        elif (
            self.peek_type() in {"IDENT", "STRLIT", "STRING"}
            and self.peek_type(1) == "COLON"
        ):
            name = self._token_name_value(self.advance())
        elif (
            self.peek_type() in {"IDENT", "STRLIT", "STRING"}
            and self.peek_type(1) in {"IDENT", "STRLIT", "STRING"}
            and self.peek_type(2) == "COLON"
        ):
            name = f"{self._token_name_value(self.advance())} {self._token_name_value(self.advance())}"
        else:
            raise SyntaxError(
                f"Line {tag_token.line}, Col {tag_token.col}: expected ':' after UI node {tag!r}"
            )
        self.expect("COLON")

        node = UiNode(tag=tag, name=name, line=tag_token.line, col=tag_token.col)
        self._parse_block(node)
        self.expect("END")
        return node

    def _parse_block(self, node: UiNode) -> None:
        while self.peek() is not None and self.peek_type() != "END":
            if self._is_node_start():
                node.children.append(self._parse_node())
                continue
            if self._is_property_start():
                node.properties.extend(self._parse_property())
                continue
            self._skip_non_ui_construct()

    def _is_property_start(self) -> bool:
        return self._is_property_name(self.peek()) and self.peek_type(1) in {
            "UI_ARROW",
            "COMMA",
        }

    def _parse_property(self) -> list[UiProperty]:
        names: list[Token] = [self._consume_property_name()]
        while self.peek_type() == "COMMA":
            self.advance()
            names.append(self._consume_property_name())
        self.expect("UI_ARROW")
        value = self._parse_value()
        return [
            UiProperty(name=name.text, value=value, line=name.line, col=name.col)
            for name in names
        ]

    def _consume_property_name(self) -> Token:
        token = self.advance()
        if not self._is_property_name(token):
            raise SyntaxError(
                f"Line {token.line}, Col {token.col}: expected UI property name, got {token.kind}"
            )
        return token

    @staticmethod
    def _is_property_name(token: Token | None) -> bool:
        return (
            token is not None and _PROPERTY_NAME_PATTERN.match(token.text) is not None
        )

    def _parse_value(self) -> UiValue:
        token = self.peek()
        if token is None:
            raise SyntaxError("Unexpected end of UI property value")
        if token.kind not in _UI_VALUE_TOKENS:
            self.advance()
            return UiValue(kind="raw", value=token.text, line=token.line, col=token.col)

        self.advance()
        if token.kind in {"STRLIT", "INTERP_STRLIT"}:
            return UiValue(
                kind="string",
                value=unescape_string(token.text),
                line=token.line,
                col=token.col,
            )
        if token.kind == "UI_COLOR":
            return UiValue(
                kind="color", value=token.text.lower(), line=token.line, col=token.col
            )
        if token.kind in {"TRUE", "FALSE"}:
            return UiValue(
                kind="bool",
                value=token.kind == "TRUE",
                line=token.line,
                col=token.col,
            )
        if token.kind in {"NUMBER", "FLOAT"}:
            value = self._number_value(token.text, token.kind)
            unit = self._consume_optional_unit()
            if unit:
                return UiValue(
                    kind="dimension",
                    value=value,
                    unit=unit,
                    line=token.line,
                    col=token.col,
                )
            return UiValue(kind="number", value=value, line=token.line, col=token.col)
        return UiValue(kind="ident", value=token.text, line=token.line, col=token.col)

    def _consume_optional_unit(self) -> str:
        if self.peek_type() not in _UNIT_TOKENS:
            return ""
        unit = "%" if self.peek_type() == "MOD" else self.peek_text()
        if unit not in _KNOWN_UNITS:
            return ""
        self.advance()
        return unit

    @staticmethod
    def _number_value(text: str, token_type: str) -> int | float:
        cleaned = text.rstrip("lLfFdDqQ")
        if token_type == "FLOAT":
            return float(cleaned)
        return int(cleaned, 0)

    @staticmethod
    def _token_name_value(token: Token) -> str:
        if token.kind == "STRLIT":
            return unescape_string(token.text)
        return token.text

    def _skip_non_ui_construct(self) -> None:
        if self.peek() is None:
            return
        start = self.advance()
        if start.kind == "END":
            return

        saw_colon = False
        depth = 0
        while self.peek() is not None:
            token_type = self.peek_type()
            if token_type == "COLON":
                saw_colon = True
                depth += 1
            elif token_type == "END":
                if saw_colon:
                    depth -= 1
                    self.advance()
                    if depth <= 0:
                        return
                    continue
                return
            elif saw_colon and self._is_node_start():
                depth += 1
            if not saw_colon and self._is_node_start(top_level=True):
                return
            self.advance()


def parse_ui_source(
    source: str,
    *,
    source_path: str | Path | None = None,
    expand_includes: bool = False,
    include_paths: Sequence[str | Path] = (),
) -> UiDocument:
    path = Path(source_path).resolve() if source_path is not None else None
    parser = UiDslParser(
        tokenize(source),
        source_path=path,
        include_paths=[Path(p).resolve() for p in include_paths],
        expand_includes=expand_includes,
        seen={path} if path is not None else set(),
    )
    return parser.parse_document()


def parse_ui_file(
    path: str | Path,
    *,
    expand_includes: bool = True,
    include_paths: Sequence[str | Path] = (),
    seen: set[Path] | None = None,
) -> UiDocument:
    resolved = Path(path).resolve()
    seen = seen if seen is not None else set()
    if resolved in seen:
        return UiDocument(path=resolved)
    seen.add(resolved)
    parser = UiDslParser(
        tokenize(resolved.read_text(encoding="utf-8")),
        source_path=resolved,
        include_paths=[Path(p).resolve() for p in include_paths],
        expand_includes=expand_includes,
        seen=seen,
    )
    return parser.parse_document()

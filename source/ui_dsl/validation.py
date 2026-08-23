"""Semantic validation for the canonical AILang UI DSL AST."""

from __future__ import annotations

from .ast import UiDocument, UiNode, UiValue

_GRID_PARENT_TAGS = {"body", "vbox", "hbox", "panel", "scrollable", "tab"}
_CAPTION_TYPES = {"default", "traffic"}
_CONTROL_POSITIONS = {"left", "right"}


def validate_ui_document(document: UiDocument) -> None:
    for node in document.nodes:
        _validate_node(node, parent=None)


def _validate_node(node: UiNode, parent: UiNode | None) -> None:
    if node.tag == "grid" and (parent is None or parent.tag not in _GRID_PARENT_TAGS):
        parent_name = parent.tag if parent is not None else "top level"
        raise SyntaxError(
            f"Line {node.line}, Col {node.col}: grid blocks must be inside "
            f"body, panel, tab, vbox, hbox, or scrollable; got {parent_name}"
        )

    if node.tag == "caption":
        caption_type = node.property("type")
        if caption_type is not None:
            _validate_caption_type(caption_type)

    if node.tag == "controls":
        position = node.property("position")
        if position is not None:
            _validate_control_position(position)

    for child in node.children:
        _validate_node(child, parent=node)


def _validate_caption_type(value: UiValue) -> None:
    if value.kind not in {"string", "ident"} or str(value.value) not in _CAPTION_TYPES:
        raise SyntaxError(
            f"Line {value.line}, Col {value.col}: caption type must be "
            '"default" or "traffic"'
        )


def _validate_control_position(value: UiValue) -> None:
    if (
        value.kind not in {"string", "ident"}
        or str(value.value) not in _CONTROL_POSITIONS
    ):
        raise SyntaxError(
            f"Line {value.line}, Col {value.col}: caption controls position must "
            'be "left" or "right"'
        )

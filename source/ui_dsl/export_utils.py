"""Shared helpers for UI DSL preview exporters."""

from __future__ import annotations

from .ast import UiNode


def css_prop(node: UiNode | None, name: str, default: str) -> str:
    if node is None:
        return default
    value = node.property(name)
    return value.as_css() if value else default


def int_prop(node: UiNode | None, name: str, default: int) -> int:
    if node is None:
        return default
    value = node.property(name)
    if value is None:
        return default
    try:
        return int(value.value)
    except (TypeError, ValueError):
        return default


def px_prop(node: UiNode | None, name: str, default: int) -> int:
    if node is None:
        return default
    value = node.property(name)
    if value is None:
        return default
    try:
        return int(float(value.value))
    except (TypeError, ValueError):
        return default


def bool_prop(node: UiNode, name: str, default: bool) -> bool:
    value = node.property(name)
    if value is None or value.kind != "bool":
        return default
    return bool(value.value)


def caption_type(node: UiNode) -> str:
    caption = caption_node(node)
    if caption is None:
        return "default"
    value = caption.property("type")
    if value is None:
        return "default"
    text = str(value.value)
    return text if text in {"default", "traffic"} else "default"


def caption_controls_position(node: UiNode) -> str:
    default = "left" if caption_type(node) == "traffic" else "right"
    controls = caption_controls_node(node)
    if controls is None:
        return default
    value = controls.property("position")
    if value is None:
        return default
    text = str(value.value)
    return text if text in {"left", "right"} else default


def caption_title_x(node: UiNode, x: int) -> int:
    position = caption_controls_position(node)
    if position == "left":
        control_width = 66 if caption_type(node) == "traffic" else 134
        return x + control_width + 10
    return x + 12


def caption_node(node: UiNode) -> UiNode | None:
    return next((child for child in node.children if child.tag == "caption"), None)


def caption_controls_node(node: UiNode) -> UiNode | None:
    caption = caption_node(node)
    if caption is None:
        return None
    return next((child for child in caption.children if child.tag == "controls"), None)


def node_label(node: UiNode) -> str:
    for name in ("text", "name", "label", "title"):
        value = node.property(name)
        if value is not None:
            return value.as_css()
    return node.name or node.tag


def has_box_props(node: UiNode) -> bool:
    return any(
        node.property(name) is not None
        for name in ("x", "y", "width", "height", "fill", "background", "border")
    )


def has_position_props(node: UiNode | None) -> bool:
    if node is None:
        return False
    return node.property("x") is not None or node.property("y") is not None


def has_position_props_for(node: UiNode, x_name: str, y_name: str) -> bool:
    return node.property(x_name) is not None or node.property(y_name) is not None


def join_style(*parts: str) -> str:
    return ";".join(part.strip().rstrip(";") for part in parts if part)

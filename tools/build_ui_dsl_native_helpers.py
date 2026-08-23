#!/usr/bin/env python3
"""Build native UI previews from an AILang UI DSL file."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
AILANG = REPO_ROOT / "ailang.py"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ui_dsl import parse_ui_file, ui_document_to_html, ui_document_to_svg  # noqa: E402
from ui_dsl.ast import UiDocument, UiNode  # noqa: E402
from ui_dsl.export_utils import (  # noqa: E402
    caption_controls_node,
    caption_controls_position,
    caption_node,
    css_prop,
    int_prop,
    px_prop,
)

OUT_DIR = REPO_ROOT / "out" / "generated" / "ui"

def _window(document: UiDocument) -> UiNode:
    windows = [node for node in document.nodes if node.tag == "window"]
    if not windows:
        raise ValueError("UI DSL native build needs a top-level window node")
    return windows[0]
def _color(node: UiNode | None, name: str, default: str | None = None) -> int | None:
    text = css_prop(node, name, default or "")
    if text in {"", "none", "transparent"}:
        return None
    if text.startswith("#") and len(text) == 7:
        return int(text[1:], 16)
    if text.startswith("0x"):
        return int(text, 16)
    return None
def _ail_color(value: int | None, default: int = -1) -> str:
    if value is None:
        value = default
    if value < 0:
        return str(value)
    return f"0x{value:06x}"
def _ail_string(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )
    return f'"{escaped}"'
def _add_expr(base: str, offset: int) -> str:
    if offset == 0:
        return base
    if base == "0":
        return str(offset)
    if offset < 0:
        return f"({base} - {-offset})"
    return f"({base} + {offset})"
def _join_expr(base: str, offset: str) -> str:
    if offset == "0":
        return base
    if base == "0":
        return offset
    return f"({base} + {offset})"
def _axis_expr(
    node: UiNode,
    base: str,
    start_name: str,
    size_name: str,
    end_name: str,
    root_name: str,
    root_value: int,
) -> tuple[str, str, int]:
    start = px_prop(node, start_name, 0)
    size = px_prop(node, size_name, 0)
    has_start = node.property(start_name) is not None
    has_end = node.property(end_name) is not None
    end = px_prop(node, end_name, 0)
    if has_end and has_start:
        is_trailing_anchor = (
            size > 0 and start + size + end == root_value and start > root_value // 2
        )
        if is_trailing_anchor:
            return (
                _join_expr(base, f"({root_name} - {end} - {size})"),
                str(size),
                size,
            )
        design_size = root_value - start - end
        return (
            _join_expr(base, str(start)),
            f"({root_name} - {start} - {end})",
            design_size,
        )
    if has_end and size > 0:
        return _join_expr(base, f"({root_name} - {end} - {size})"), str(size), size
    size_expr = root_name if size == root_value else str(size)
    return _add_expr(base, start), size_expr, size
def _emit_rect(
    lines: list[str],
    x: str,
    y: str,
    width: str,
    height: str,
    fill: int | None,
    border: int | None,
    *,
    radius: int = 0,
    shadow_x: int = 0,
    shadow_y: int = 0,
    shadow_color: int | None = None,
) -> None:
    if shadow_color is not None and (shadow_x != 0 or shadow_y != 0):
        sx = _add_expr(x, shadow_x)
        sy = _add_expr(y, shadow_y)
        if radius > 0:
            lines.append(
                f"    dsl_draw_rounded_rect(window, {sx}, {sy}, {width}, {height}, {radius}, {_ail_color(shadow_color)})"
            )
        else:
            lines.append(
                f"    ail_ui_draw_rect(window, {sx}, {sy}, {width}, {height}, {_ail_color(shadow_color)})"
            )
    if radius > 0:
        if border is not None and fill is not None:
            inner_radius = max(0, radius - 1)
            lines.append(
                f"    dsl_draw_rounded_rect(window, {x}, {y}, {width}, {height}, {radius}, {_ail_color(border)})"
            )
            lines.append(
                f"    dsl_draw_rounded_rect(window, ({x} + 1), ({y} + 1), ({width} - 2), ({height} - 2), {inner_radius}, {_ail_color(fill)})"
            )
            return
        if fill is not None:
            lines.append(
                f"    dsl_draw_rounded_rect(window, {x}, {y}, {width}, {height}, {radius}, {_ail_color(fill)})"
            )
        if border is not None:
            lines.append(
                f"    ui_draw_border(window, {x}, {y}, {width}, {height}, {_ail_color(border)}, 1)"
            )
        return
    if fill is not None:
        lines.append(
            f"    ail_ui_draw_rect(window, {x}, {y}, {width}, {height}, {_ail_color(fill)})"
        )
    if border is not None:
        lines.append(
            f"    ui_draw_border(window, {x}, {y}, {width}, {height}, {_ail_color(border)}, 1)"
        )
def _emit_text(lines: list[str], node: UiNode, base_x: str, base_y: str) -> None:
    x = _add_expr(base_x, px_prop(node, "x", 0))
    y = _add_expr(base_y, px_prop(node, "y", 0))
    color = _color(node, "color", "#17212b") or 0x17212B
    text = _ail_string(css_prop(node, "text", node.name or ""))
    scale = px_prop(node, "scale", 2)
    font_size = px_prop(node, "font_size", 0)
    weight = int_prop(node, "font_weight", 400)
    if font_size > 0 or node.property("font_weight") is not None:
        if font_size <= 0:
            font_size = 12 + ((scale - 1) * 4)
        lines.append(
            f"    ail_ui_draw_text_style(window, {x}, {y}, {font_size}, {weight}, {_ail_color(color)}, {text})"
        )
    else:
        lines.append(
            f"    ail_ui_draw_text(window, {x}, {y}, {scale}, {_ail_color(color)}, {text})"
        )
def _emit_property_grid(
    lines: list[str], node: UiNode, base_x: str, base_y: str
) -> None:
    x = _add_expr(base_x, px_prop(node, "x", 0))
    y = _add_expr(base_y, px_prop(node, "y", 0))
    key_w = px_prop(node, "key_width", 132)
    value_w = px_prop(node, "value_width", 184)
    row_h = px_prop(node, "row_height", 32)
    key_fill = _color(node, "key_fill", "#2d2d30") or 0x2D2D30
    value_fill = _color(node, "value_fill", "#1e1e1e") or 0x1E1E1E
    border = _color(node, "border", "#3f3f46") or 0x3F3F46
    key_color = _color(node, "key_color", "#9399a1") or 0x9399A1
    value_color = _color(node, "value_color", "#c7ccd3") or 0xC7CCD3
    for index, row in enumerate(child for child in node.children if child.tag == "row"):
        row_y = f"({y} + {index * row_h})"
        label = _ail_string(css_prop(row, "label", ""))
        value = _ail_string(css_prop(row, "value", ""))
        lines.append(
            f"    ail_ui_draw_rect(window, {x}, {row_y}, {key_w}, {row_h}, {_ail_color(key_fill)})"
        )
        lines.append(
            f"    ui_draw_border(window, {x}, {row_y}, {key_w}, {row_h}, {_ail_color(border)}, 1)"
        )
        lines.append(
            f"    ail_ui_draw_rect(window, ({x} + {key_w}), {row_y}, {value_w}, {row_h}, {_ail_color(value_fill)})"
        )
        lines.append(
            f"    ui_draw_border(window, ({x} + {key_w}), {row_y}, {value_w}, {row_h}, {_ail_color(border)}, 1)"
        )
        lines.append(
            f"    ail_ui_draw_text(window, ({x} + 8), ({row_y} + 8), 2, {_ail_color(key_color)}, {label})"
        )
        lines.append(
            f"    ail_ui_draw_text(window, ({x} + {key_w + 8}), ({row_y} + 8), 2, {_ail_color(value_color)}, {value})"
        )
def _emit_circle(lines: list[str], node: UiNode, base_x: str, base_y: str) -> None:
    cx = _add_expr(base_x, px_prop(node, "cx", px_prop(node, "x", 0)))
    cy = _add_expr(base_y, px_prop(node, "cy", px_prop(node, "y", 0)))
    radius = px_prop(node, "radius", px_prop(node, "r", 4))
    fill = _color(node, "fill", "#000000") or 0
    lines.append(
        f"    dsl_draw_circle(window, {cx}, {cy}, {radius}, {_ail_color(fill)})"
    )
def _emit_line(lines: list[str], node: UiNode, base_x: str, base_y: str) -> None:
    x1_raw = px_prop(node, "x1", 0)
    y1_raw = px_prop(node, "y1", 0)
    x2_raw = px_prop(node, "x2", 0)
    y2_raw = px_prop(node, "y2", 0)
    x1 = _add_expr(base_x, x1_raw)
    y1 = _add_expr(base_y, y1_raw)
    stroke = _color(node, "stroke", "#000000") or 0
    stroke_width = max(1, px_prop(node, "stroke_width", 1))
    if y1_raw == y2_raw:
        width = abs(x2_raw - x1_raw)
        left = _add_expr(base_x, min(x1_raw, x2_raw))
        lines.append(
            f"    ail_ui_draw_rect(window, {left}, {y1}, {width}, {stroke_width}, {_ail_color(stroke)})"
        )
        return
    if x1_raw == x2_raw:
        height = abs(y2_raw - y1_raw)
        top = _add_expr(base_y, min(y1_raw, y2_raw))
        lines.append(
            f"    ail_ui_draw_rect(window, {x1}, {top}, {stroke_width}, {height}, {_ail_color(stroke)})"
        )
def _emit_grid_cell_child(
    lines: list[str],
    node: UiNode,
    cell_x: str,
    cell_y: str,
    cell_w: str,
    cell_h: str,
    cell_design_width: int,
    cell_design_height: int,
) -> None:
    if node.tag == "circle":
        radius = px_prop(node, "radius", px_prop(node, "r", 4))
        fill = _color(node, "fill", "#000000") or 0
        align = css_prop(node, "align", "center")
        if align == "start":
            cx = f"({cell_x} + {radius})"
        elif align == "end":
            cx = f"({cell_x} + {cell_w} - {radius})"
        else:
            cx = f"({cell_x} + ({cell_w} / 2))"
        cy = f"({cell_y} + ({cell_h} / 2))"
        lines.append(
            f"    dsl_draw_circle(window, {cx}, {cy}, {radius}, {_ail_color(fill)})"
        )
        return

    if (
        node.property("x") is not None
        or node.property("y") is not None
        or node.property("width") is not None
        or node.property("height") is not None
        or node.property("right") is not None
        or node.property("bottom") is not None
    ):
        _emit_node(
            lines,
            node,
            cell_x,
            cell_y,
            cell_w,
            cell_h,
            cell_design_width,
            cell_design_height,
        )
        return

    if node.tag in {"text", "label"}:
        x = _add_expr(cell_x, px_prop(node, "x", 12))
        y = _add_expr(cell_y, px_prop(node, "y", 12))
        color = _color(node, "color", "#17212b") or 0x17212B
        text = _ail_string(css_prop(node, "text", node.name or ""))
        scale = px_prop(node, "scale", 2)
        lines.append(
            f"    ail_ui_draw_text(window, {x}, {y}, {scale}, {_ail_color(color)}, {text})"
        )
        return

    fill = _color(node, "fill", None)
    if fill is None:
        fill = _color(node, "background", "#ffffff")
    border = _color(node, "border", "#cbd5e1")
    _emit_rect(
        lines,
        cell_x,
        cell_y,
        cell_w,
        cell_h,
        fill,
        border,
        radius=px_prop(node, "radius", 0),
        shadow_x=px_prop(node, "shadow_x", 0),
        shadow_y=px_prop(node, "shadow_y", 0),
        shadow_color=_color(node, "shadow", None),
    )

    if node.tag == "button":
        label = _ail_string(css_prop(node, "name", css_prop(node, "text", "button")))
        color = _color(node, "color", "#17212b") or 0x17212B
        lines.append(
            f"    ail_ui_draw_text(window, ({cell_x} + 12), ({cell_y} + 12), 2, {_ail_color(color)}, {label})"
        )
        return

    for child in node.children:
        _emit_node(
            lines,
            child,
            cell_x,
            cell_y,
            cell_w,
            cell_h,
            cell_design_width,
            cell_design_height,
        )
def _emit_grid(
    lines: list[str],
    node: UiNode,
    base_x: str,
    base_y: str,
    scope_width: str,
    scope_height: str,
    scope_design_width: int,
    scope_design_height: int,
) -> None:
    x, width, design_width = _axis_expr(
        node, base_x, "x", "width", "right", scope_width, scope_design_width
    )
    y, height, design_height = _axis_expr(
        node, base_y, "y", "height", "bottom", scope_height, scope_design_height
    )
    cols = max(1, int_prop(node, "columns", 2))
    row_default = (len(node.children) + cols - 1) // cols
    rows = max(1, int_prop(node, "rows", row_default))
    gap = px_prop(node, "gap", 0)
    cell_w = f"(({width} - ({gap} * {cols - 1})) / {cols})"
    cell_h = f"(({height} - ({gap} * {rows - 1})) / {rows})"
    cell_design_width = max(1, (design_width - (gap * (cols - 1))) // cols)
    cell_design_height = max(1, (design_height - (gap * (rows - 1))) // rows)
    for index, child in enumerate(node.children):
        col = index % cols
        row = index // cols
        cell_x = f"({x} + ({col} * ({cell_w} + {gap})))"
        cell_y = f"({y} + ({row} * ({cell_h} + {gap})))"
        _emit_grid_cell_child(
            lines,
            child,
            cell_x,
            cell_y,
            cell_w,
            cell_h,
            cell_design_width,
            cell_design_height,
        )
def _emit_node(
    lines: list[str],
    node: UiNode,
    base_x: str,
    base_y: str,
    scope_width: str,
    scope_height: str,
    scope_design_width: int,
    scope_design_height: int,
) -> None:
    if node.tag in {"caption", "row"}:
        return
    if node.tag in {"text", "label"}:
        _emit_text(lines, node, base_x, base_y)
        return
    if node.tag == "grid":
        _emit_grid(
            lines,
            node,
            base_x,
            base_y,
            scope_width,
            scope_height,
            scope_design_width,
            scope_design_height,
        )
        return
    if node.tag == "property_grid":
        _emit_property_grid(lines, node, base_x, base_y)
        return
    if node.tag == "circle":
        _emit_circle(lines, node, base_x, base_y)
        return
    if node.tag == "line":
        _emit_line(lines, node, base_x, base_y)
        return

    x, width, design_width = _axis_expr(
        node, base_x, "x", "width", "right", scope_width, scope_design_width
    )
    y, height, design_height = _axis_expr(
        node, base_y, "y", "height", "bottom", scope_height, scope_design_height
    )
    fill = _color(node, "fill", None)
    if fill is None:
        fill = _color(node, "background", None)
    border = _color(node, "border", None)
    if px_prop(node, "width", 0) > 0 and px_prop(node, "height", 0) > 0:
        _emit_rect(
            lines,
            x,
            y,
            width,
            height,
            fill,
            border,
            radius=px_prop(node, "radius", 0),
            shadow_x=px_prop(node, "shadow_x", 0),
            shadow_y=px_prop(node, "shadow_y", 0),
            shadow_color=_color(node, "shadow", None),
        )

    bottom = _color(node, "border_bottom", None)
    if (
        bottom is not None
        and px_prop(node, "width", 0) > 0
        and px_prop(node, "height", 0) > 0
    ):
        lines.append(
            f"    ail_ui_draw_rect(window, {x}, ({y} + {height} - 1), {width}, 1, {_ail_color(bottom)})"
        )

    for child in node.children:
        _emit_node(
            lines,
            child,
            x,
            y,
            width,
            height,
            design_width,
            design_height,
        )
def _caption_constants(window: UiNode) -> dict[str, int]:
    controls = caption_controls_node(window)
    caption = caption_node(window)
    return {
        "button_hover_fill": _color(controls, "hover_fill", "#212b34") or 0x212B34,
        "button_pressed_fill": _color(controls, "pressed_fill", "#2a3743") or 0x2A3743,
        "close_hover_fill": _color(controls, "close_hover_fill", "#ce564b") or 0xCE564B,
        "close_pressed_fill": _color(controls, "close_pressed_fill", "#5b2822")
        or 0x5B2822,
        "button_border": _color(controls, "button_border", "#334351") or 0x334351,
        "close_border": _color(controls, "close_border", "#d57167") or 0xD57167,
        "button_background": _color(controls, "button_background", "#14191f")
        or 0x14191F,
        "glyph": _color(controls, "glyph", "#e7f8f4") or 0xE7F8F4,
        "radius": px_prop(controls, "corner_radius", 8),
        "border_width": px_prop(controls, "border_width", 1),
        "caption_background": _color(caption, "background", "#172730") or 0x172730,
    }
def _emit_caption(lines: list[str], window: UiNode, root_width: int) -> None:
    caption = caption_node(window)
    if caption is None:
        return
    h = px_prop(caption, "height", 34)
    start = _color(caption, "background", "#f8fafc") or 0xF8FAFC
    end = _color(caption, "background_end", css_prop(caption, "background", "#f8fafc"))
    if end is None:
        end = start
    border = _color(caption, "border", "#ccd6e0") or 0xCCD6E0
    title = _ail_string(css_prop(window, "title", window.name or "Window"))
    title_x = px_prop(caption, "title_x", 12)
    title_y = px_prop(caption, "title_y", 8)
    title_color = _color(caption, "title_color", "#17212b") or 0x17212B
    lines.append(
        f"    ui_draw_gradient_rect(window, 0, 0, width, {h}, {_ail_color(start)}, {_ail_color(end)}, 1)"
    )
    lines.append(
        f"    ail_ui_draw_rect(window, 0, {h - 1}, width, 1, {_ail_color(border)})"
    )
    if caption.property("icon_x") is not None:
        ix = px_prop(caption, "icon_x", 10)
        iy = px_prop(caption, "icon_y", 10)
        fill = _color(caption, "icon_fill", "#0f253c") or 0x0F253C
        icon_border = _color(caption, "icon_border", "#1169b1") or 0x1169B1
        lines.append(
            f"    ail_ui_draw_rect(window, {ix}, {iy}, 14, 12, {_ail_color(fill)})"
        )
        lines.append(
            f"    ui_draw_border(window, {ix}, {iy}, 14, 12, {_ail_color(icon_border)}, 1)"
        )
        lines.append(
            f"    ail_ui_draw_rect(window, {ix + 3}, {iy + 3}, 8, 6, 0x0e1116)"
        )
    lines.append(
        f"    ail_ui_draw_text(window, {title_x}, {title_y}, 2, {_ail_color(title_color)}, {title})"
    )

    controls = caption_controls_node(window)
    if controls is None:
        return
    total_w = px_prop(controls, "width", 132)
    top = px_prop(controls, "top", 1)
    right = px_prop(controls, "right", 10)
    left = px_prop(controls, "left", 10)
    height = px_prop(controls, "height", 30)
    min_w = px_prop(controls, "minimize_width", 42)
    mid_w = px_prop(controls, "middle_width", 42)
    close_w = px_prop(controls, "close_width", 50)
    mid_off = px_prop(controls, "middle_offset", 41)
    close_off = px_prop(controls, "close_offset", 82)
    normal = _color(controls, "normal_fill", None)
    close_fill = _color(controls, "close_fill", None)
    lines.append(f"    caption_controls_right = {right}")
    lines.append("    if maximized == 1 then")
    lines.append("        caption_controls_right = 2")
    lines.append("    end")
    if caption_controls_position(window) == "right":
        lines.append(
            f"    caption_controls_x = width - caption_controls_right - {total_w}"
        )
    else:
        lines.append(f"    caption_controls_x = {left}")
    bx = "caption_controls_x"
    if normal is not None:
        lines.append(
            f"    ail_ui_draw_rect(window, {bx}, {top}, {min_w}, {height}, {_ail_color(normal)})"
        )
        lines.append(
            f"    ail_ui_draw_rect(window, ({bx} + {mid_off}), {top}, {mid_w}, {height}, {_ail_color(normal)})"
        )
    if close_fill is not None:
        lines.append(
            f"    ail_ui_draw_rect(window, ({bx} + {close_off}), {top}, {close_w}, {height}, {_ail_color(close_fill)})"
        )
    lines.append(
        f"    dsl_draw_caption_button_face(window, {bx}, {top}, {min_w}, {height}, 1, dsl_caption_button_visual(1, hovered_caption_button, pressed_caption_button), maximized)"
    )
    lines.append(
        f"    dsl_draw_caption_button_face(window, ({bx} + {mid_off}), {top}, {mid_w}, {height}, 2, dsl_caption_button_visual(2, hovered_caption_button, pressed_caption_button), maximized)"
    )
    lines.append(
        f"    dsl_draw_caption_button_face(window, ({bx} + {close_off}), {top}, {close_w}, {height}, 3, dsl_caption_button_visual(3, hovered_caption_button, pressed_caption_button), maximized)"
    )
    lines.append("    middle_kind = 2")
    lines.append("    if maximized == 1 then")
    lines.append("        middle_kind = 3")
    lines.append("    end")
    lines.append(
        f"    ui_draw_caption_icon(window, 1, {bx}, {top}, {min_w}, {height}, dsl_caption_glyph_color(1, dsl_caption_button_visual(1, hovered_caption_button, pressed_caption_button)))"
    )
    lines.append(
        f"    ui_draw_caption_icon(window, middle_kind, ({bx} + {mid_off}), {top}, {mid_w}, {height}, dsl_caption_glyph_color(2, dsl_caption_button_visual(2, hovered_caption_button, pressed_caption_button)))"
    )
    lines.append(
        f"    ui_draw_caption_icon(window, 4, ({bx} + {close_off}), {top}, {close_w}, {height}, dsl_caption_glyph_color(3, dsl_caption_button_visual(3, hovered_caption_button, pressed_caption_button)))"
    )
def _caption_hit_function(window: UiNode) -> str:
    controls = caption_controls_node(window)
    if controls is None:
        return """def dsl_caption_button_hit(width: int, x: int, y: int, maximized: int): int
    return 0
end
"""
    total_w = px_prop(controls, "width", 132)
    top = px_prop(controls, "top", 1)
    right = px_prop(controls, "right", 10)
    left = px_prop(controls, "left", 10)
    height = px_prop(controls, "height", 30)
    min_w = px_prop(controls, "minimize_width", 42)
    mid_w = px_prop(controls, "middle_width", 42)
    close_w = px_prop(controls, "close_width", 50)
    mid_off = px_prop(controls, "middle_offset", 41)
    close_off = px_prop(controls, "close_offset", 82)
    if caption_controls_position(window) == "right":
        bx_expr = f"width - caption_controls_right - {total_w}"
    else:
        bx_expr = str(left)
    return f"""def dsl_caption_button_hit(width: int, x: int, y: int, maximized: int): int
    caption_controls_right = {right}
    if maximized == 1 then
        caption_controls_right = 2
    end
    bx = {bx_expr}
    if y < {top} or y >= {top + height} then
        return 0
    end
    if x >= bx and x < bx + {min_w} then
        return 1
    end
    if x >= bx + {mid_off} and x < bx + {mid_off + mid_w} then
        return 2
    end
    if x >= bx + {close_off} and x < bx + {close_off + close_w} then
        return 3
    end
    return 0
end
"""

__all__ = [name for name in globals() if not name.startswith("__")]

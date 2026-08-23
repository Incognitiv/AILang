"""SVG preview exporter for the canonical AILang UI DSL AST."""

from __future__ import annotations

from html import escape

from .ast import UiDocument, UiNode
from .export_utils import caption_controls_node as _caption_controls_node
from .export_utils import caption_controls_position as _caption_controls_position
from .export_utils import caption_node as _caption_node
from .export_utils import caption_title_x as _caption_title_x
from .export_utils import caption_type as _caption_type
from .export_utils import css_prop as _css_prop
from .export_utils import has_position_props as _has_position_props
from .export_utils import px_prop as _px_prop


def ui_document_to_svg(document: UiDocument) -> str:
    windows = _visible_nodes(document)
    if len(windows) == 1 and _css_prop(windows[0], "layout", "") == "absolute":
        node = windows[0]
        width = _px_prop(node, "width", 520)
        height = _px_prop(node, "height", 360)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            + _window_to_svg(node, 0, 0, width, height)
            + "</svg>"
        )
    width = max((_px_prop(node, "width", 520) for node in windows), default=520)
    y = 16
    fragments: list[str] = []
    for node in windows:
        h = _px_prop(node, "height", 360)
        fragments.append(_window_to_svg(node, 16, y, _px_prop(node, "width", 520), h))
        y += h + 24
    height = max(y, 420)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width + 32}" '
        f'height="{height}" viewBox="0 0 {width + 32} {height}">'
        '<rect width="100%" height="100%" fill="#eef3f8"/>'
        + "".join(fragments)
        + "</svg>"
    )


def _visible_nodes(document: UiDocument) -> list[UiNode]:
    windows = [node for node in document.nodes if node.tag == "window"]
    return windows if windows else document.nodes


def _window_to_svg(node: UiNode, x: int, y: int, width: int, height: int) -> str:
    bg = _css_prop(node, "background", "#ffffff")
    title = escape(_css_prop(node, "title", node.name or "Window"))
    caption = _caption_node(node)
    caption_h = _px_prop(caption, "height", 34)
    caption_bg = _css_prop(caption, "background", "#f8fafc")
    caption_border = _css_prop(caption, "border", "#ccd6e0")
    title_color = _css_prop(caption, "title_color", "#17212b")
    title_x = _caption_title_x(node, x)
    if caption is not None and caption.property("title_x") is not None:
        title_x = x + _px_prop(caption, "title_x", 12)
    title_y = y + (22 if caption is None else _px_prop(caption, "title_y", 22) + 10)
    body = "".join(
        _svg_children(node.children, x + 18, y + 52, width - 36, height - 70)
    )
    if _css_prop(node, "layout", "") == "absolute":
        body = "".join(_svg_children(node.children, x, y, width, height))
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" '
        f'fill="{escape(bg)}" stroke="#ccd6e0"/>'
        f'<rect x="{x}" y="{y}" width="{width}" height="{caption_h}" rx="8" '
        f'fill="{caption_bg}" stroke="{caption_border}"/>'
        f'<text x="{title_x}" y="{title_y}" font-family="Segoe UI, sans-serif" '
        f'font-size="13" fill="{title_color}">{title}</text>'
        + _caption_icon_svg(caption, x, y)
        + _caption_controls_to_svg(node, x, y, width)
        + body
    )


def _caption_icon_svg(caption: UiNode | None, base_x: int, base_y: int) -> str:
    if caption is None or caption.property("icon_x") is None:
        return ""
    x = base_x + _px_prop(caption, "icon_x", 10)
    y = base_y + _px_prop(caption, "icon_y", 10)
    fill = _css_prop(caption, "icon_fill", "#0f253c")
    border = _css_prop(caption, "icon_border", "#1169b1")
    return (
        f'<rect x="{x}" y="{y}" width="14" height="12" '
        f'fill="{fill}" stroke="{border}"/>'
        f'<rect x="{x + 3}" y="{y + 3}" width="8" height="6" fill="#0e1116"/>'
    )


def _caption_controls_to_svg(node: UiNode, x: int, y: int, width: int) -> str:
    position = _caption_controls_position(node)
    if _caption_type(node) == "traffic":
        cy = y + 17
        start_x = x + 18 if position == "left" else x + width - 58
        return (
            f'<circle cx="{start_x}" cy="{cy}" r="6" fill="#ff5f57" '
            'stroke="rgba(0,0,0,.18)"/>'
            f'<circle cx="{start_x + 20}" cy="{cy}" r="6" fill="#ffbd2e" '
            'stroke="rgba(0,0,0,.18)"/>'
            f'<circle cx="{start_x + 40}" cy="{cy}" r="6" fill="#28c840" '
            'stroke="rgba(0,0,0,.18)"/>'
        )

    controls = _caption_controls_node(node)
    if controls is None:
        return _default_caption_controls_to_svg(x, y, width, position)

    total_w = _px_prop(controls, "width", 132)
    bx = x + _px_prop(controls, "left", 2)
    if position == "right":
        bx = x + width - _px_prop(controls, "right", 2) - total_w
    by = y + _px_prop(controls, "top", 3)
    glyph = _css_prop(controls, "glyph", "#17212b")
    normal_fill = _css_prop(controls, "normal_fill", "#e8eef5")
    close_fill = _css_prop(controls, "close_fill", "#d94d54")
    return (
        _svg_caption_button_face(
            bx,
            by,
            _px_prop(controls, "minimize_width", 42),
            _px_prop(controls, "height", 28),
            normal_fill,
        )
        + _svg_minimize_glyph(bx, by, _px_prop(controls, "minimize_width", 42), glyph)
        + _svg_caption_button_face(
            bx + _px_prop(controls, "middle_offset", 44),
            by,
            _px_prop(controls, "middle_width", 42),
            _px_prop(controls, "height", 28),
            normal_fill,
        )
        + _svg_maximize_glyph(
            bx + _px_prop(controls, "middle_offset", 44),
            by,
            _px_prop(controls, "middle_width", 42),
            glyph,
        )
        + _svg_caption_button_face(
            bx + _px_prop(controls, "close_offset", 88),
            by,
            _px_prop(controls, "close_width", 42),
            _px_prop(controls, "height", 28),
            close_fill,
        )
        + _svg_close_glyph(
            bx + _px_prop(controls, "close_offset", 88),
            by,
            _px_prop(controls, "close_width", 42),
            glyph,
        )
    )


def _default_caption_controls_to_svg(x: int, y: int, width: int, position: str) -> str:
    bx = x + 2 if position == "left" else x + width - 132
    by = y + 3
    return (
        f'<rect x="{bx}" y="{by}" width="42" height="28" fill="#e8eef5"/>'
        f'<line x1="{bx + 15}" y1="{by + 15}" x2="{bx + 27}" '
        f'y2="{by + 15}" stroke="#17212b"/>'
        f'<rect x="{bx + 44}" y="{by}" width="42" height="28" fill="#e8eef5"/>'
        f'<rect x="{bx + 59}" y="{by + 9}" width="10" height="10" '
        'fill="none" stroke="#17212b"/>'
        f'<rect x="{bx + 88}" y="{by}" width="42" height="28" fill="#d94d54"/>'
        f'<line x1="{bx + 102}" y1="{by + 9}" x2="{bx + 116}" '
        f'y2="{by + 23}" stroke="#ffffff"/>'
        f'<line x1="{bx + 116}" y1="{by + 9}" x2="{bx + 102}" '
        f'y2="{by + 23}" stroke="#ffffff"/>'
    )


def _svg_caption_button_face(x: int, y: int, width: int, height: int, fill: str) -> str:
    if fill in {"none", "transparent"}:
        return ""
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="{fill}"/>'


def _svg_minimize_glyph(x: int, y: int, width: int, color: str) -> str:
    gx = x + ((width - 14) // 2)
    return f'<rect x="{gx}" y="{y + 19}" width="14" height="1" fill="{color}"/>'


def _svg_maximize_glyph(x: int, y: int, width: int, color: str) -> str:
    gx = x + ((width - 16) // 2)
    return (
        f'<rect x="{gx}" y="{y + 9}" width="16" height="12" '
        f'fill="none" stroke="{color}"/>'
    )


def _svg_close_glyph(x: int, y: int, width: int, color: str) -> str:
    gx = x + ((width - 12) // 2)
    gy = y + 9
    return (
        f'<line x1="{gx}" y1="{gy}" x2="{gx + 11}" y2="{gy + 11}" '
        f'stroke="{color}"/>'
        f'<line x1="{gx + 11}" y1="{gy}" x2="{gx}" y2="{gy + 11}" '
        f'stroke="{color}"/>'
    )


def _svg_children(children: list[UiNode], x: int, y: int, width: int, height: int):
    cursor_y = y
    for child in children:
        if child.tag == "caption":
            continue
        if _is_svg_absolute(child):
            yield _absolute_node_to_svg(child, x, y, width, height)
        elif child.tag == "property_grid":
            yield _property_grid_to_svg(child, x, y)
        elif child.tag == "grid":
            yield _grid_to_svg(child, x, cursor_y, width, min(height, 180))
            cursor_y += min(height, 180) + 12
        elif child.tag in {"body", "scrollable", "vbox", "hbox", "tab", "panel"}:
            yield from _svg_children(child.children, x, cursor_y, width, height)
        elif child.tag == "label":
            text = escape(_css_prop(child, "text", child.name or "Label"))
            yield (
                f'<text x="{x}" y="{cursor_y + 16}" '
                'font-family="Segoe UI, sans-serif" font-size="13" '
                f'fill="#17212b">{text}</text>'
            )
            cursor_y += 24
        elif child.tag == "button":
            text = escape(_css_prop(child, "name", _css_prop(child, "text", "Button")))
            yield (
                f'<rect x="{x}" y="{cursor_y}" width="120" height="30" rx="5" '
                'fill="#e4f2fd" stroke="#9cc6e8"/>'
                f'<text x="{x + 14}" y="{cursor_y + 20}" '
                'font-family="Segoe UI, sans-serif" font-size="12" '
                f'fill="#075985">{text}</text>'
            )
            cursor_y += 42


def _is_svg_absolute(node: UiNode) -> bool:
    return _has_position_props(node) or node.tag in {
        "circle",
        "line",
        "rect",
        "text",
        "menubar",
        "toolbar",
        "group",
        "tree",
        "tabs",
    }


def _svg_axis(
    node: UiNode,
    base: int,
    scope: int,
    start_name: str,
    size_name: str,
    end_name: str,
) -> tuple[int, int]:
    start = _px_prop(node, start_name, 0)
    size = _px_prop(node, size_name, 0)
    end = _px_prop(node, end_name, 0)
    has_start = node.property(start_name) is not None
    has_end = node.property(end_name) is not None
    if has_start and has_end:
        return base + start, max(0, scope - start - end)
    if has_end and size > 0:
        return base + scope - end - size, size
    return base + start, size


def _absolute_node_to_svg(
    node: UiNode, base_x: int, base_y: int, scope_width: int, scope_height: int
) -> str:
    x, width = _svg_axis(node, base_x, scope_width, "x", "width", "right")
    y, height = _svg_axis(node, base_y, scope_height, "y", "height", "bottom")
    if node.tag in {"text", "label"}:
        return _svg_text_top(
            x,
            y,
            _css_prop(node, "text", node.name or ""),
            _css_prop(node, "color", "#17212b"),
            _px_prop(node, "font_size", 14),
            _px_prop(node, "font_weight", 400),
        )
    if node.tag == "circle":
        cx = base_x + _px_prop(node, "cx", _px_prop(node, "x", 0))
        cy = base_y + _px_prop(node, "cy", _px_prop(node, "y", 0))
        radius = _px_prop(node, "radius", _px_prop(node, "r", 4))
        fill = _css_prop(node, "fill", "#000000")
        return f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{fill}"/>'
    if node.tag == "line":
        x1 = base_x + _px_prop(node, "x1", 0)
        y1 = base_y + _px_prop(node, "y1", 0)
        x2 = base_x + _px_prop(node, "x2", 0)
        y2 = base_y + _px_prop(node, "y2", 0)
        stroke = _css_prop(node, "stroke", "#000000")
        width = _px_prop(node, "stroke_width", 1)
        return (
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{stroke}" stroke-width="{width}"/>'
        )
    if node.tag == "property_grid":
        return _property_grid_to_svg(node, base_x, base_y)
    if node.tag == "grid":
        return _grid_to_svg(node, x, y, width, height)

    fill = _css_prop(node, "fill", _css_prop(node, "background", "none"))
    border = _css_prop(node, "border", "none")
    parts = []
    if width > 0 and height > 0:
        radius = _px_prop(node, "radius", 0)
        shadow = node.property("shadow")
        if shadow is not None:
            sx = _px_prop(node, "shadow_x", 0)
            sy = _px_prop(node, "shadow_y", 0)
            parts.append(
                f'<rect x="{x + sx}" y="{y + sy}" width="{width}" height="{height}" '
                f'rx="{radius}" fill="{shadow.as_css()}"/>'
            )
        parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{fill}" stroke="{border}"/>'
        )
    border_bottom = node.property("border_bottom")
    if border_bottom is not None and width > 0 and height > 0:
        parts.append(
            f'<rect x="{x}" y="{y + height - 1}" width="{width}" height="1" '
            f'fill="{border_bottom.as_css()}"/>'
        )
    parts.extend(_svg_children(node.children, x, y, width, height))
    return "".join(parts)


def _property_grid_to_svg(node: UiNode, base_x: int, base_y: int) -> str:
    x = base_x + _px_prop(node, "x", 0)
    y = base_y + _px_prop(node, "y", 0)
    key_w = _px_prop(node, "key_width", 132)
    value_w = _px_prop(node, "value_width", 184)
    row_h = _px_prop(node, "row_height", 32)
    key_fill = _css_prop(node, "key_fill", "#2d2d30")
    value_fill = _css_prop(node, "value_fill", "#1e1e1e")
    border = _css_prop(node, "border", "#3f3f46")
    key_color = _css_prop(node, "key_color", "#9399a1")
    value_color = _css_prop(node, "value_color", "#c7ccd3")
    parts = []
    for index, row in enumerate(child for child in node.children if child.tag == "row"):
        row_y = y + index * row_h
        parts.append(
            f'<rect x="{x}" y="{row_y}" width="{key_w}" height="{row_h}" '
            f'fill="{key_fill}" stroke="{border}"/>'
        )
        parts.append(
            f'<rect x="{x + key_w}" y="{row_y}" width="{value_w}" '
            f'height="{row_h}" fill="{value_fill}" stroke="{border}"/>'
        )
        parts.append(
            _svg_text_top(x + 8, row_y + 8, _css_prop(row, "label", ""), key_color, 14)
        )
        parts.append(
            _svg_text_top(
                x + key_w + 8,
                row_y + 8,
                _css_prop(row, "value", ""),
                value_color,
                14,
            )
        )
    return "".join(parts)


def _svg_text_top(
    x: int, y: int, text: str, fill: str, size: int, weight: int = 400
) -> str:
    return (
        f'<text x="{x}" y="{y}" dominant-baseline="hanging" '
        f'font-family="Segoe UI, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{escape(text)}</text>'
    )


def _grid_to_svg(node: UiNode, x: int, y: int, width: int, height: int) -> str:
    cols = max(1, _px_prop(node, "columns", 2))
    rows = max(1, _px_prop(node, "rows", (len(node.children) + cols - 1) // cols))
    gap = _px_prop(node, "gap", 8)
    cell_w = max(1, (width - gap * (cols - 1)) // cols)
    cell_h = max(1, (height - gap * (rows - 1)) // rows)
    parts = [f'<g data-ui-tag="grid" data-columns="{cols}" data-rows="{rows}">']
    for index, child in enumerate(node.children):
        row = index // cols
        col = index % cols
        cx = x + col * (cell_w + gap)
        cy = y + row * (cell_h + gap)
        parts.append(_grid_child_to_svg(child, cx, cy, cell_w, cell_h))
    parts.append("</g>")
    return "".join(parts)


def _grid_child_to_svg(node: UiNode, x: int, y: int, width: int, height: int) -> str:
    if node.tag == "circle" and node.property("cx") is None:
        radius = _px_prop(node, "radius", _px_prop(node, "r", 4))
        align = _css_prop(node, "align", "center")
        if align == "start":
            cx = x + radius
        elif align == "end":
            cx = x + width - radius
        else:
            cx = x + width // 2
        cy = y + height // 2
        fill = _css_prop(node, "fill", "#000000")
        return f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{fill}"/>'

    if node.tag in {"text", "label"}:
        return _absolute_node_to_svg(node, x, y, width, height)

    if node.tag == "button":
        label = escape(_css_prop(node, "name", _css_prop(node, "text", "button")))
        button_w = _px_prop(node, "width", min(120, width))
        button_h = _px_prop(node, "height", min(30, height))
        button_x, button_w = _svg_axis(node, x, width, "x", "width", "right")
        button_y, button_h = _svg_axis(node, y, height, "y", "height", "bottom")
        if node.property("width") is None:
            button_w = min(120, width)
        if node.property("height") is None:
            button_h = min(30, height)
        fill = _css_prop(node, "background", "#e4f2fd")
        border = _css_prop(node, "border", "#9cc6e8")
        color = _css_prop(node, "color", "#075985")
        return (
            f'<rect x="{button_x}" y="{button_y}" width="{button_w}" '
            f'height="{button_h}" rx="5" fill="{fill}" stroke="{border}"/>'
            f'<text x="{button_x + 14}" y="{button_y + 9}" '
            'dominant-baseline="hanging" font-family="Segoe UI, sans-serif" '
            f'font-size="12" fill="{color}">{label}</text>'
        )

    if node.tag in {"panel", "body", "group", "tab", "scrollable", "vbox", "hbox"}:
        child_x = x
        child_y = y
        child_w = width
        child_h = height
        if (
            node.property("x") is not None
            or node.property("right") is not None
            or node.property("width") is not None
        ):
            child_x, child_w = _svg_axis(node, x, width, "x", "width", "right")
        if (
            node.property("y") is not None
            or node.property("bottom") is not None
            or node.property("height") is not None
        ):
            child_y, child_h = _svg_axis(node, y, height, "y", "height", "bottom")
        parts = [_svg_box_to_svg(node, child_x, child_y, child_w, child_h)]
        parts.extend(_svg_children(node.children, child_x, child_y, child_w, child_h))
        return "".join(parts)

    return _absolute_node_to_svg(node, x, y, width, height)


def _svg_box_to_svg(node: UiNode, x: int, y: int, width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    fill = _css_prop(node, "fill", _css_prop(node, "background", "none"))
    border = _css_prop(node, "border", "none")
    radius = _px_prop(node, "radius", 0)
    parts = []
    shadow = node.property("shadow")
    if shadow is not None:
        sx = _px_prop(node, "shadow_x", 0)
        sy = _px_prop(node, "shadow_y", 0)
        parts.append(
            f'<rect x="{x + sx}" y="{y + sy}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{shadow.as_css()}"/>'
        )
    parts.append(
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{radius}" fill="{fill}" stroke="{border}"/>'
    )
    return "".join(parts)

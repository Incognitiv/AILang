"""Static preview exporters for the canonical AILang UI DSL AST."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .ast import UiDocument, UiNode, UiValue
from .export_utils import bool_prop as _bool_prop
from .export_utils import caption_controls_node as _caption_controls_node
from .export_utils import caption_controls_position as _caption_controls_position
from .export_utils import caption_node as _caption_node
from .export_utils import caption_type as _caption_type
from .export_utils import css_prop as _css_prop
from .export_utils import has_box_props as _has_box_props
from .export_utils import has_position_props as _has_position_props
from .export_utils import has_position_props_for as _has_position_props_for
from .export_utils import int_prop as _int_prop
from .export_utils import join_style as _join_style
from .export_utils import px_prop as _px_prop
from .svg_exporter import ui_document_to_svg


def ui_document_to_dict(document: UiDocument) -> dict[str, Any]:
    return {
        "path": str(document.path) if document.path else None,
        "includes": [
            {
                "target": include.target,
                "resolved": str(include.resolved) if include.resolved else None,
                "line": include.line,
                "col": include.col,
            }
            for include in document.includes
        ],
        "nodes": [_node_to_dict(node) for node in document.nodes],
    }


def _node_to_dict(node: UiNode) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tag": node.tag,
        "line": node.line,
        "col": node.col,
    }
    if node.name:
        result["name"] = node.name
    if node.properties:
        result["properties"] = {
            prop.name: _value_to_dict(prop.value) for prop in node.properties
        }
    if node.children:
        result["children"] = [_node_to_dict(child) for child in node.children]
    return result


def _value_to_dict(value: UiValue) -> dict[str, Any]:
    result = {"kind": value.kind, "value": value.value}
    if value.unit:
        result["unit"] = value.unit
    return result


def ui_document_to_html(
    document: UiDocument, *, title: str = "AILang UI Preview"
) -> str:
    body = "\n".join(_node_to_html(node) for node in _visible_nodes(document))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{
  color-scheme: light dark;
  --ui-bg: #eef3f8;
  --ui-panel: #ffffff;
  --ui-border: #ccd6e0;
  --ui-text: #17212b;
  --ui-muted: #64748b;
  --ui-accent: #178bd6;
}}
body {{
  margin: 0;
  min-height: 100vh;
  background: var(--ui-bg);
  color: var(--ui-text);
  font: 14px/1.35 "Segoe UI", system-ui, sans-serif;
}}
.preview-root {{
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  padding: 24px;
}}
.ui-window {{
  position: relative;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: var(--ui-panel);
  box-shadow: 0 14px 40px rgba(15, 23, 42, 0.18);
}}
.ui-caption {{
  height: 34px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  border-bottom: 1px solid var(--ui-border);
  background: linear-gradient(180deg, rgba(255,255,255,.85), rgba(241,245,249,.88));
  font-weight: 600;
}}
.ui-caption-title {{ flex: 1; min-width: 0; }}
.ui-caption-controls {{
  display: flex;
  align-items: flex-start;
  gap: 2px;
  align-self: stretch;
}}
.ui-caption-controls-left {{ order: -1; }}
.ui-caption-controls-right {{ order: 1; margin-left: auto; }}
.ui-caption-traffic .ui-caption-controls {{
  align-items: center;
  gap: 8px;
  align-self: center;
}}
.ui-caption-button {{
  position: relative;
  width: 42px;
  height: 28px;
  border: 0;
  background: rgba(23, 33, 43, .08);
  color: #17212b;
}}
.ui-caption-button.close {{ color: #ffffff; background: #d94d54; }}
.ui-caption-button.min::before {{
  content: "";
  position: absolute;
  left: 15px;
  top: 15px;
  width: 12px;
  height: 1px;
  background: currentColor;
}}
.ui-caption-button.max::before {{
  content: "";
  position: absolute;
  left: 15px;
  top: 9px;
  width: 10px;
  height: 10px;
  border: 1px solid currentColor;
}}
.ui-caption-button.close::before,
.ui-caption-button.close::after {{
  content: "";
  position: absolute;
  left: 14px;
  top: 14px;
  width: 14px;
  height: 1px;
  background: currentColor;
}}
.ui-caption-button.close::before {{ transform: rotate(45deg); }}
.ui-caption-button.close::after {{ transform: rotate(-45deg); }}
.ui-traffic-light {{
  width: 12px;
  height: 12px;
  border-radius: 999px;
  border: 1px solid rgba(0, 0, 0, .18);
}}
.ui-traffic-light.close {{ background: #ff5f57; }}
.ui-traffic-light.min {{ background: #ffbd2e; }}
.ui-traffic-light.max {{ background: #28c840; }}
.ui-body, .ui-vbox, .ui-hbox, .ui-grid, .ui-panel, .ui-scrollable, .ui-tab,
.ui-menubar, .ui-toolbar, .ui-rect, .ui-text, .ui-property-grid {{
  box-sizing: border-box;
}}
.ui-body, .ui-scrollable, .ui-tab {{
  padding: 18px;
}}
.ui-vbox {{ display: flex; flex-direction: column; }}
.ui-hbox {{ display: flex; flex-direction: row; }}
.ui-grid {{ display: grid; }}
.ui-panel {{
  position: relative;
  padding: 0;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: rgba(255,255,255,.72);
}}
.ui-grid > .ui-panel {{
  min-width: 0;
  min-height: 0;
  height: 100%;
}}
.ui-label {{ color: var(--ui-text); }}
.ui-text {{
  color: var(--ui-text);
  white-space: pre;
}}
.ui-rect {{ pointer-events: none; }}
.ui-property-grid {{
  display: grid;
  overflow: hidden;
}}
.ui-property-cell {{ box-sizing: border-box; padding: 7px 8px; }}
.ui-button {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 30px;
  padding: 0 14px;
  border: 1px solid #9cc6e8;
  border-radius: 5px;
  background: #e4f2fd;
  color: #075985;
}}
.ui-input {{
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
}}
.ui-slider-track {{
  height: 6px;
  border-radius: 999px;
  background: #dbe4ed;
}}
.ui-slider-fill {{
  height: 6px;
  border-radius: inherit;
  background: var(--ui-accent);
}}
</style>
</head>
<body>
<main class="preview-root">
{body}
</main>
</body>
</html>
"""


def _visible_nodes(document: UiDocument) -> list[UiNode]:
    windows = [node for node in document.nodes if node.tag == "window"]
    return windows if windows else document.nodes


def _node_to_html(node: UiNode) -> str:
    if node.tag == "window":
        width = _css_prop(node, "width", "520px")
        height = _css_prop(node, "height", "360px")
        background = _css_prop(node, "background", "#ffffff")
        title = _css_prop(node, "title", node.name or "Window")
        caption = _window_caption(node, title)
        children = "\n".join(
            _node_to_html(child) for child in node.children if child.tag != "caption"
        )
        style = _join_style(
            f"width:{width}",
            f"height:{height}",
            f"background:{background}",
            _position_style(node, absolute=False),
            _border_style(node),
        )
        return (
            f'<section class="ui-window" data-ui-source="dsl" style="{style}">'
            f"{caption}{children}</section>"
        )

    if node.tag == "property_grid":
        return _property_grid_html(node)
    if node.tag in {
        "body",
        "scrollable",
        "tab",
        "panel",
        "menubar",
        "toolbar",
        "group",
        "tree",
        "tabs",
    }:
        return _container_html(node, f"ui-{node.tag}", _box_style(node))
    if node.tag == "rect":
        return f'<div class="ui-rect" style="{_box_style(node)}"></div>'
    if node.tag == "circle":
        radius = _int_prop(node, "radius", _int_prop(node, "r", 4))
        if node.property("x") is None and node.property("cx") is None:
            align = _css_prop(node, "align", "center")
            justify = {"start": "start", "end": "end"}.get(align, "center")
            style = _join_style(
                f"width:{radius * 2}px",
                f"height:{radius * 2}px",
                "border-radius:999px",
                f"justify-self:{justify}",
                "align-self:center",
                _fill_style(node),
            )
        else:
            x = _css_prop(node, "x", f"{_int_prop(node, 'cx', 0) - radius}px")
            y = _css_prop(node, "y", f"{_int_prop(node, 'cy', 0) - radius}px")
            style = _join_style(
                "position:absolute",
                f"left:{x}",
                f"top:{y}",
                f"width:{radius * 2}px",
                f"height:{radius * 2}px",
                "border-radius:999px",
                _fill_style(node),
            )
        return f'<div class="ui-circle" style="{style}"></div>'
    if node.tag == "line":
        x1 = _int_prop(node, "x1", 0)
        y1 = _int_prop(node, "y1", 0)
        x2 = _int_prop(node, "x2", 0)
        y2 = _int_prop(node, "y2", 0)
        stroke = _css_prop(node, "stroke", "#000000")
        stroke_width = _css_prop(node, "stroke_width", "1px")
        if y1 == y2:
            style = _join_style(
                "position:absolute",
                f"left:{min(x1, x2)}px",
                f"top:{y1}px",
                f"width:{abs(x2 - x1)}px",
                f"height:{stroke_width}",
                f"background:{stroke}",
            )
            return f'<div class="ui-line" style="{style}"></div>'
        if x1 == x2:
            style = _join_style(
                "position:absolute",
                f"left:{x1}px",
                f"top:{min(y1, y2)}px",
                f"width:{stroke_width}",
                f"height:{abs(y2 - y1)}px",
                f"background:{stroke}",
            )
            return f'<div class="ui-line" style="{style}"></div>'
        return ""
    if node.tag == "text":
        text = escape(_css_prop(node, "text", node.name or ""))
        return f'<div class="ui-text" style="{_text_style(node)}">{text}</div>'
    if node.tag in {"vbox", "hbox"}:
        gap = _css_prop(node, "gap", "8px")
        return _container_html(node, f"ui-{node.tag}", _box_style(node, f"gap:{gap}"))
    if node.tag == "grid":
        cols = _int_prop(node, "columns", 2)
        rows = _int_prop(node, "rows", 0)
        gap = _css_prop(node, "gap", "8px")
        style = f"grid-template-columns:repeat({cols}, minmax(0, 1fr));gap:{gap}"
        if rows > 0:
            style += f";grid-template-rows:repeat({rows}, minmax(0, 1fr))"
        return _container_html(node, "ui-grid", _box_style(node, style))
    if node.tag == "label":
        text = escape(_css_prop(node, "text", node.name or "Label"))
        if _has_box_props(node):
            return f'<div class="ui-label" style="{_text_style(node)}">{text}</div>'
        return f'<div class="ui-label">{text}</div>'
    if node.tag == "button":
        return f'<button class="ui-button" type="button">{escape(_css_prop(node, "name", _css_prop(node, "text", "Button")))}</button>'
    if node.tag == "input":
        placeholder = escape(_css_prop(node, "placeholder", ""))
        return f'<input class="ui-input" value="" placeholder="{placeholder}">'
    if node.tag == "checkbox":
        label = escape(_css_prop(node, "label", "Checkbox"))
        checked = " checked" if _bool_prop(node, "checked", False) else ""
        return f'<label class="ui-checkbox"><input type="checkbox"{checked}> {label}</label>'
    if node.tag == "slider":
        value = max(0, min(100, _int_prop(node, "value", 50)))
        label = escape(_css_prop(node, "label", "Slider"))
        return f'<label class="ui-slider">{label}<div class="ui-slider-track"><div class="ui-slider-fill" style="width:{value}%"></div></div></label>'
    return _container_html(node, f"ui-{escape(node.tag)}")


def _container_html(node: UiNode, class_name: str, extra_style: str = "") -> str:
    children = "\n".join(_node_to_html(child) for child in node.children)
    style = f' style="{extra_style}"' if extra_style else ""
    return f'<div class="{class_name}"{style}>{children}</div>'


def _property_grid_html(node: UiNode) -> str:
    x = _css_prop(node, "x", "0px")
    y = _css_prop(node, "y", "0px")
    key_w = _css_prop(node, "key_width", "132px")
    value_w = _css_prop(node, "value_width", "184px")
    row_h = _css_prop(node, "row_height", "32px")
    key_fill = _css_prop(node, "key_fill", "#2d2d30")
    value_fill = _css_prop(node, "value_fill", "#1e1e1e")
    border = _css_prop(node, "border", "#3f3f46")
    key_color = _css_prop(node, "key_color", "#9399a1")
    value_color = _css_prop(node, "value_color", "#c7ccd3")
    rows = [child for child in node.children if child.tag == "row"]
    height = f"calc({row_h} * {len(rows)})"
    style = _join_style(
        "position:absolute",
        f"left:{x}",
        f"top:{y}",
        f"width:calc({key_w} + {value_w})",
        f"height:{height}",
        f"grid-template-columns:{key_w} {value_w}",
        f"grid-auto-rows:{row_h}",
        f"border:1px solid {border}",
    )
    cells: list[str] = []
    for row in rows:
        label = escape(_css_prop(row, "label", ""))
        value = escape(_css_prop(row, "value", ""))
        key_style = _join_style(
            f"background:{key_fill}",
            f"color:{key_color}",
            f"border-right:1px solid {border}",
            f"border-bottom:1px solid {border}",
        )
        value_style = _join_style(
            f"background:{value_fill}",
            f"color:{value_color}",
            f"border-bottom:1px solid {border}",
        )
        cells.append(f'<div class="ui-property-cell" style="{key_style}">{label}</div>')
        cells.append(
            f'<div class="ui-property-cell" style="{value_style}">{value}</div>'
        )
    return f'<div class="ui-property-grid" style="{style}">{"".join(cells)}</div>'


def _box_style(node: UiNode, extra_style: str = "") -> str:
    return _join_style(
        _position_style(node),
        _size_style(node),
        _fill_style(node),
        _border_style(node),
        _border_bottom_style(node),
        _radius_style(node),
        _shadow_style(node),
        _layout_style(node),
        extra_style,
    )


def _text_style(node: UiNode) -> str:
    return _join_style(
        _position_style(node),
        _size_style(node),
        f"color:{_css_prop(node, 'color', 'var(--ui-text)')}",
        f"font-size:{_css_prop(node, 'font_size', '14px')}",
        f"font-weight:{_css_prop(node, 'font_weight', '400')}",
    )


def _position_style(node: UiNode, *, absolute: bool = True) -> str:
    has_position = any(
        node.property(name) is not None for name in ("x", "y", "right", "bottom")
    )
    if not has_position:
        return ""
    position = "position:absolute" if absolute else "position:relative"
    parts = [position]
    if node.property("x") is not None:
        parts.append(f"left:{_css_prop(node, 'x', '0px')}")
    elif node.property("right") is None:
        parts.append("left:0px")
    if node.property("right") is not None:
        parts.append(f"right:{_css_prop(node, 'right', '0px')}")
    if node.property("y") is not None:
        parts.append(f"top:{_css_prop(node, 'y', '0px')}")
    elif node.property("bottom") is None:
        parts.append("top:0px")
    if node.property("bottom") is not None:
        parts.append(f"bottom:{_css_prop(node, 'bottom', '0px')}")
    return _join_style(*parts)


def _size_style(node: UiNode) -> str:
    parts = []
    if node.property("width") is not None and not (
        node.property("x") is not None and node.property("right") is not None
    ):
        parts.append(f"width:{_css_prop(node, 'width', 'auto')}")
    if node.property("height") is not None and not (
        node.property("y") is not None and node.property("bottom") is not None
    ):
        parts.append(f"height:{_css_prop(node, 'height', 'auto')}")
    return _join_style(*parts)


def _fill_style(node: UiNode) -> str:
    fill = node.property("fill")
    background = node.property("background")
    if fill is not None:
        return f"background:{fill.as_css()}"
    if background is not None:
        return f"background:{background.as_css()}"
    return ""


def _border_style(node: UiNode) -> str:
    border = node.property("border")
    if border is None:
        return ""
    return f"border:1px solid {border.as_css()}"


def _border_bottom_style(node: UiNode) -> str:
    border = node.property("border_bottom")
    if border is None:
        return ""
    return f"border-bottom:1px solid {border.as_css()}"


def _radius_style(node: UiNode) -> str:
    radius = node.property("radius")
    if radius is None:
        return ""
    return f"border-radius:{radius.as_css()}"


def _shadow_style(node: UiNode) -> str:
    shadow = node.property("shadow")
    if shadow is None:
        return ""
    x = _css_prop(node, "shadow_x", "0px")
    y = _css_prop(node, "shadow_y", "0px")
    return f"box-shadow:{x} {y} 0 {shadow.as_css()}"


def _layout_style(node: UiNode) -> str:
    value = node.property("layout")
    if value is not None and str(value.value) == "absolute":
        if _has_position_props(node):
            return "padding:0"
        return "position:relative;padding:0"
    return ""


def _window_caption(node: UiNode, title: str) -> str:
    caption = _caption_node(node)
    caption_type = _caption_type(node)
    position = _caption_controls_position(node)
    controls = _caption_controls_html(node, caption_type, position)
    caption_style = _caption_style(caption)
    title_style = _caption_title_style(caption)
    icon = _caption_icon_html(caption)
    return (
        f'<div class="ui-caption ui-caption-{caption_type}" '
        f'data-drag-region="titlebar" style="{caption_style}">'
        f'{icon}<span class="ui-caption-title" style="{title_style}">'
        f"{escape(title)}</span>{controls}</div>"
    )


def _caption_style(caption: UiNode | None) -> str:
    if caption is None:
        return ""
    background = _css_prop(caption, "background", "")
    background_end = _css_prop(caption, "background_end", "")
    if background and background_end:
        fill = f"background:linear-gradient(180deg,{background},{background_end})"
    elif background:
        fill = f"background:{background}"
    else:
        fill = ""
    return _join_style(
        f"height:{_css_prop(caption, 'height', '34px')}",
        fill,
        f"border-bottom:1px solid {_css_prop(caption, 'border', 'var(--ui-border)')}",
        "position:relative",
        "padding:0",
    )


def _caption_title_style(caption: UiNode | None) -> str:
    if caption is None or not _has_position_props_for(caption, "title_x", "title_y"):
        return ""
    return _join_style(
        "position:absolute",
        f"left:{_css_prop(caption, 'title_x', '12px')}",
        f"top:{_css_prop(caption, 'title_y', '8px')}",
        f"color:{_css_prop(caption, 'title_color', '#17212b')}",
    )


def _caption_icon_html(caption: UiNode | None) -> str:
    if caption is None or caption.property("icon_x") is None:
        return ""
    style = _join_style(
        "position:absolute",
        f"left:{_css_prop(caption, 'icon_x', '10px')}",
        f"top:{_css_prop(caption, 'icon_y', '10px')}",
        "width:14px",
        "height:12px",
        f"background:{_css_prop(caption, 'icon_fill', '#0f253c')}",
        f"border:1px solid {_css_prop(caption, 'icon_border', '#1169b1')}",
    )
    return f'<span class="ui-caption-app-icon" style="{style}"></span>'


def _caption_controls_html(node: UiNode, caption_type: str, position: str) -> str:
    controls = _caption_controls_node(node)
    if caption_type == "traffic":
        return (
            f'<div class="ui-caption-controls ui-caption-controls-{position}">'
            '<span class="ui-traffic-light close"></span>'
            '<span class="ui-traffic-light min"></span>'
            '<span class="ui-traffic-light max"></span>'
            "</div>"
        )
    if controls is None:
        return (
            f'<div class="ui-caption-controls ui-caption-controls-{position}">'
            '<button class="ui-caption-button min" aria-label="Minimize"></button>'
            '<button class="ui-caption-button max" aria-label="Maximize"></button>'
            '<button class="ui-caption-button close" aria-label="Close"></button>'
            "</div>"
        )
    top = _css_prop(controls, "top", "3px")
    right = _css_prop(controls, "right", "2px")
    width = _css_prop(controls, "width", "132px")
    height = _css_prop(controls, "height", "28px")
    glyph = _css_prop(controls, "glyph", "#17212b")
    normal_fill = _css_prop(controls, "normal_fill", "rgba(23,33,43,.08)")
    close_fill = _css_prop(controls, "close_fill", "#d94d54")
    group_style = _join_style(
        "position:absolute",
        f"top:{top}",
        f"right:{right}",
        f"width:{width}",
        f"height:{height}",
        "display:block",
    )
    return (
        f'<div class="ui-caption-controls ui-caption-controls-{position}" '
        f'style="{group_style}">'
        f'{_caption_button_html("min", controls, "minimize_width", 0, normal_fill, glyph)}'
        f'{_caption_button_html("max", controls, "middle_width", _px_prop(controls, "middle_offset", 44), normal_fill, glyph)}'
        f'{_caption_button_html("close", controls, "close_width", _px_prop(controls, "close_offset", 88), close_fill, glyph)}'
        "</div>"
    )


def _caption_button_html(
    kind: str, controls: UiNode, width_prop: str, left: int, fill: str, glyph: str
) -> str:
    height = _css_prop(controls, "height", "28px")
    width = _css_prop(controls, width_prop, "42px")
    style = _join_style(
        "position:absolute",
        f"left:{left}px",
        "top:0",
        f"width:{width}",
        f"height:{height}",
        f"background:{fill}",
        f"color:{glyph}",
    )
    label = {"min": "Minimize", "max": "Maximize", "close": "Close"}[kind]
    return (
        f'<button class="ui-caption-button {kind}" '
        f'aria-label="{label}" style="{style}"></button>'
    )


def write_preview(document: UiDocument, output: str | Path, *, fmt: str) -> Path:
    out = Path(output)
    if fmt == "html":
        out.write_text(ui_document_to_html(document), encoding="utf-8")
    elif fmt == "svg":
        out.write_text(ui_document_to_svg(document), encoding="utf-8")
    else:
        raise ValueError(f"unsupported UI preview format: {fmt}")
    return out

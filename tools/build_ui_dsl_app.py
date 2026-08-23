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
TOOLS_ROOT = REPO_ROOT / "tools"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

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

try:
    from .build_ui_dsl_native_helpers import *
except ImportError:
    from build_ui_dsl_native_helpers import *

def ui_document_to_native_ail(document: UiDocument) -> str:
    win = _window(document)
    root_width = px_prop(win, "width", 960)
    root_height = px_prop(win, "height", 640)
    bg = _color(win, "background", "#ffffff") or 0xFFFFFF
    title = _ail_string(css_prop(win, "title", win.name or "AILang UI"))
    constants = _caption_constants(win)
    render_lines: list[str] = [f"    ail_ui_begin_frame(window, {_ail_color(bg)})"]
    _emit_caption(render_lines, win, root_width)
    for child in win.children:
        _emit_node(
            render_lines,
            child,
            "0",
            "0",
            "width",
            "height",
            root_width,
            root_height,
        )
    render_lines.append("    ail_ui_end_frame(window)")
    render_body = "\n".join(render_lines)
    caption_hit = _caption_hit_function(win)
    return f"""// Generated from AILang UI DSL. Do not edit this generated file directly.

import source.ui.ui_backend
import stdlib.ui.paint
import stdlib.ui.platform

const int DSL_CAPTION_BUTTON_HOVER_FILL = {_ail_color(constants["button_hover_fill"])}
const int DSL_CAPTION_BUTTON_PRESSED_FILL = {_ail_color(constants["button_pressed_fill"])}
const int DSL_CAPTION_CLOSE_HOVER_FILL = {_ail_color(constants["close_hover_fill"])}
const int DSL_CAPTION_CLOSE_PRESSED_FILL = {_ail_color(constants["close_pressed_fill"])}
const int DSL_CAPTION_BUTTON_BORDER = {_ail_color(constants["button_border"])}
const int DSL_CAPTION_CLOSE_BORDER = {_ail_color(constants["close_border"])}
const int DSL_CAPTION_BUTTON_BACKGROUND = {_ail_color(constants["button_background"])}
const int DSL_CAPTION_GLYPH = {_ail_color(constants["glyph"])}
const int DSL_CAPTION_RADIUS = {constants["radius"]}
const int DSL_CAPTION_BORDER_WIDTH = {constants["border_width"]}


def dsl_corner_inset(pos: int, radius: int): int
    if radius <= 0 then
        return 0
    end
    if pos < 0 then
        return 0
    end
    if pos >= radius then
        return 0
    end

    y_from_center = radius - 1 - pos
    limit = (radius * radius) - (y_from_center * y_from_center)
    dx = radius
    while dx > 0 and dx * dx > limit then
        dx = dx - 1
    end

    inset = radius - dx
    if inset < 0 then
        return 0
    end
    return inset
end


def dsl_draw_rounded_rect(
    window: int,
    x: int,
    y: int,
    width: int,
    height: int,
    radius: int,
    color: int
): void
    if radius <= 0 then
        ail_ui_draw_rect(window, x, y, width, height, color)
        return
    end
    if width <= radius * 2 or height <= radius * 2 then
        ail_ui_draw_rect(window, x, y, width, height, color)
        return
    end

    yy = 0
    while yy < height then
        left = 0
        right = 0
        if yy < radius then
            inset = dsl_corner_inset(yy, radius)
            left = inset
            right = inset
        end
        if yy >= height - radius then
            inset = dsl_corner_inset(height - 1 - yy, radius)
            left = inset
            right = inset
        end
        row_width = width - left - right
        if row_width > 0 then
            ail_ui_draw_rect(window, x + left, y + yy, row_width, 1, color)
        end
        yy = yy + 1
    end
end


def dsl_draw_circle(window: int, cx: int, cy: int, radius: int, color: int): void
    if radius <= 0 then
        return
    end
    yy = 0 - radius
    while yy <= radius then
        xx = 0 - radius
        while xx <= radius then
            if (xx * xx) + (yy * yy) <= radius * radius then
                ail_ui_draw_rect(window, cx + xx, cy + yy, 1, 1, color)
            end
            xx = xx + 1
        end
        yy = yy + 1
    end
end


def dsl_caption_button_visual(id: int, hovered: int, pressed: int): int
    if pressed == id then
        return 2
    end
    if hovered == id then
        return 1
    end
    return 0
end


def dsl_caption_button_fill(id: int, visual: int): int
    if visual == 0 then
        return -1
    end
    if id == 3 then
        if visual == 2 then
            return DSL_CAPTION_CLOSE_PRESSED_FILL
        end
        return DSL_CAPTION_CLOSE_HOVER_FILL
    end
    if visual == 2 then
        return DSL_CAPTION_BUTTON_PRESSED_FILL
    end
    return DSL_CAPTION_BUTTON_HOVER_FILL
end


def dsl_caption_glyph_color(id: int, visual: int): int
    if visual == 0 then
        return DSL_CAPTION_GLYPH
    end
    return 0xffffff
end


def dsl_draw_caption_button_face(
    window: int,
    x: int,
    y: int,
    width: int,
    height: int,
    id: int,
    visual: int,
    maximized: int
): void
    fill = dsl_caption_button_fill(id, visual)
    if fill < 0 then
        return
    end
    border = DSL_CAPTION_BUTTON_BORDER
    if id == 3 then
        border = DSL_CAPTION_CLOSE_BORDER
    end
    round_left = 0
    round_right = 0
    if maximized == 0 then
        if id == 1 then
            round_left = 1
        end
        if id == 3 then
            round_right = 1
        end
    end
    ui_draw_bottom_cornered_box_aa(
        window,
        x,
        y,
        width,
        height,
        fill,
        border,
        DSL_CAPTION_BORDER_WIDTH,
        DSL_CAPTION_RADIUS,
        round_left,
        round_right,
        DSL_CAPTION_BUTTON_BACKGROUND
    )
end


{caption_hit}

def dsl_render(
    window: int,
    width: int,
    height: int,
    hovered_caption_button: int,
    pressed_caption_button: int
): void
    maximized = ail_ui_window_maximized(window)
{render_body}
end


def main(): int
    comptime if target_os() != "windows" and target_os() != "linux" then
        print("ui backend unsupported")
        return 0
    end

    if ui_backend_supported() == 0 then
        print("ui backend unsupported")
        return 0
    end

    if ail_ui_init() == 0 then
        print("ui init failed")
        return 1
    end

    window = ail_ui_open_borderless_window({title}, {root_width}, {root_height})
    if window == 0 then
        print("window open failed")
        ail_ui_shutdown()
        return 1
    end

    frame_limit = 0
    limit_text = getenv("AILANG_UI_DEMO_FRAMES")
    if strlen(limit_text) > 0 then
        frame_limit = parse_int(limit_text)
    end

    frame_count = 0
    hovered_caption_button = 0
    pressed_caption_button = 0
    dirty = 1
    while ail_ui_window_alive(window) == 1 and (frame_limit == 0 or frame_count < frame_limit) then
        event_kind = ail_ui_poll_event(window)
        if event_kind == 1 then
            break
        end
        if event_kind == 3 then
            next_hover = dsl_caption_button_hit(ail_ui_window_width_px(window), ail_ui_event_x(), ail_ui_event_y(), ail_ui_window_maximized(window))
            if next_hover != hovered_caption_button then
                hovered_caption_button = next_hover
                dirty = 1
            end
        end
        if event_kind == 4 then
            pressed_caption_button = dsl_caption_button_hit(ail_ui_window_width_px(window), ail_ui_event_x(), ail_ui_event_y(), ail_ui_window_maximized(window))
            if pressed_caption_button != 0 then
                dirty = 1
            end
        end
        if event_kind == 5 then
            released_caption_button = dsl_caption_button_hit(ail_ui_window_width_px(window), ail_ui_event_x(), ail_ui_event_y(), ail_ui_window_maximized(window))
            if pressed_caption_button != 0 and pressed_caption_button == released_caption_button then
                if released_caption_button == 1 then
                    ail_ui_minimize_window(window)
                end
                if released_caption_button == 2 then
                    ail_ui_toggle_maximize_window(window)
                end
                if released_caption_button == 3 then
                    break
                end
            end
            pressed_caption_button = 0
            hovered_caption_button = released_caption_button
            dirty = 1
        end
        if event_kind != 0 then
            dirty = 1
        end

        if dirty == 1 then
            dsl_render(
                window,
                ail_ui_window_width_px(window),
                ail_ui_window_height_px(window),
                hovered_caption_button,
                pressed_caption_button
            )
            dirty = 0
        else
            ail_ui_wait_event_ms(100)
        end
        frame_count = frame_count + 1
    end

    ail_ui_close_window(window)
    ail_ui_shutdown()
    return 0
end
"""
def build(source: Path, out_dir: Path, name: str) -> int:
    document = parse_ui_file(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{name}.html"
    svg_path = out_dir / f"{name}.svg"
    ail_path = out_dir / f"{name}.ail"
    exe_path = out_dir / (f"{name}.exe" if sys.platform.startswith("win") else name)
    html_path.write_text(ui_document_to_html(document), encoding="utf-8")
    svg_path.write_text(ui_document_to_svg(document), encoding="utf-8")
    ail_path.write_text(ui_document_to_native_ail(document), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(ail_path),
            "--backend=c",
            "--native-toolchain=gcc",
            "-o",
            str(exe_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        return proc.returncode
    print(html_path)
    print(svg_path)
    print(ail_path)
    print(exe_path)
    return 0
def main() -> int:
    parser = argparse.ArgumentParser(description="Build a native app from UI DSL.")
    parser.add_argument("source", type=Path, help="AILang UI DSL source file")
    parser.add_argument("--name", default="", help="Output base name")
    parser.add_argument(
        "--out-dir", type=Path, default=OUT_DIR, help="Output directory"
    )
    args = parser.parse_args()
    name = args.name or args.source.stem
    return build(args.source, args.out_dir, name)


if __name__ == "__main__":
    raise SystemExit(main())

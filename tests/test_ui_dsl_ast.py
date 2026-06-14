from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ui_dsl import (  # noqa: E402
    parse_ui_file,
    parse_ui_source,
    ui_document_to_dict,
    ui_document_to_html,
    ui_document_to_svg,
)

CANONICAL_UI_DSL = REPO_ROOT / "archived" / "source-cruft" / "Desktop Experiment"
BUILD_UI_DSL_APP = REPO_ROOT / "tools" / "build_ui_dsl_app.py"


def _load_native_ail_exporter():
    spec = importlib.util.spec_from_file_location("build_ui_dsl_app", BUILD_UI_DSL_APP)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ui_document_to_native_ail


def test_ui_dsl_parser_builds_typed_ast_for_grid() -> None:
    document = parse_ui_source(
        """
window dashboard:
    width -> 640 px
    height -> 420 px
    title -> "Dashboard"
    body:
        grid:
            columns -> 2
            rows -> 2
            gap -> 12 px
            label:
                text -> "CPU"
            end
            button:
                name -> "Refresh"
            end
        end
    end
end
"""
    )

    window = document.nodes[0]
    body = window.children[0]
    grid = body.children[0]

    assert window.tag == "window"
    assert window.name == "dashboard"
    assert window.property("width").kind == "dimension"
    assert window.property("width").unit == "px"
    assert grid.tag == "grid"
    assert grid.property("columns").value == 2
    assert [child.tag for child in grid.children] == ["label", "button"]


def test_ui_dsl_parser_expands_canonical_desktop_experiment_includes() -> None:
    if not (CANONICAL_UI_DSL / "main.ail").exists():
        pytest.skip("optional archived UI DSL corpus is not included")
    document = parse_ui_file(CANONICAL_UI_DSL / "main.ail", expand_includes=True)
    windows = [node for node in document.nodes if node.tag == "window"]

    assert {window.name for window in windows} >= {
        "login_screen",
        "welcome",
        "settings",
    }
    assert any(include.target == "handlers.ail" for include in document.includes)
    assert all(node.tag != "def" for node in document.nodes)


def test_ui_dsl_exporters_emit_grid_html_svg_and_dict() -> None:
    document = parse_ui_source(
        """
window demo:
    width -> 320 px
    height -> 220 px
    title -> "Grid demo"
    body:
        grid:
            columns -> 2
            gap -> 8 px
            label:
                text -> "One"
            end
            label:
                text -> "Two"
            end
        end
    end
end
"""
    )

    data = ui_document_to_dict(document)
    html = ui_document_to_html(document)
    svg = ui_document_to_svg(document)

    assert data["nodes"][0]["children"][0]["children"][0]["tag"] == "grid"
    assert "display: grid" in html
    assert "grid-template-columns:repeat(2" in html
    assert 'data-ui-tag="grid"' in svg
    assert 'data-columns="2"' in svg


def test_ui_dsl_grid_must_live_inside_content_container() -> None:
    try:
        parse_ui_source(
            """
window demo:
    grid:
        columns -> 2
    end
end
"""
        )
    except SyntaxError as exc:
        assert "grid blocks must be inside" in str(exc)
    else:
        raise AssertionError("grid directly under window should be rejected")


def test_ui_dsl_caption_type_controls_exported_chrome() -> None:
    traffic = parse_ui_source(
        """
window demo:
    title -> "Traffic"
    caption:
        type -> "traffic"
        controls:
            position -> "left"
        end
    end
    body:
        label:
            text -> "content"
        end
    end
end
"""
    )
    default = parse_ui_source(
        """
window demo:
    title -> "Default"
    caption:
        type -> "default"
    end
    body:
        label:
            text -> "content"
        end
    end
end
"""
    )

    traffic_html = ui_document_to_html(traffic)
    default_html = ui_document_to_html(default)
    traffic_svg = ui_document_to_svg(traffic)

    assert "ui-caption-traffic" in traffic_html
    assert "ui-traffic-light close" in traffic_html
    assert "ui-caption-default" in default_html
    assert "ui-caption-button close" in default_html
    assert "#ff5f57" in traffic_svg
    assert '<text x="92"' in traffic_svg


def test_ui_dsl_rejects_unknown_caption_type() -> None:
    try:
        parse_ui_source(
            """
window demo:
    caption:
        type -> "vista"
    end
end
"""
        )
    except SyntaxError as exc:
        assert 'caption type must be "default" or "traffic"' in str(exc)
    else:
        raise AssertionError("unknown caption type should be rejected")


def test_ui_dsl_rejects_unknown_caption_controls_position() -> None:
    try:
        parse_ui_source(
            """
window demo:
    caption:
        controls:
            position -> "middle"
        end
    end
end
"""
        )
    except SyntaxError as exc:
        assert 'caption controls position must be "left" or "right"' in str(exc)
    else:
        raise AssertionError("unknown caption controls position should be rejected")


def test_ui_dsl_svg_grid_uses_button_name() -> None:
    document = parse_ui_source(
        """
window demo:
    body:
        grid:
            columns -> 1
            button:
                name -> "Export SVG"
            end
        end
    end
end
"""
    )

    svg = ui_document_to_svg(document)

    assert "Export SVG" in svg
    assert ">button<" not in svg


def test_ui_desktop_demo_preview_exports_from_dsl_tree() -> None:
    document = parse_ui_file(REPO_ROOT / "examples" / "ui" / "ui_desktop_dsl.ail")

    html = ui_document_to_html(document)
    svg = ui_document_to_svg(document)
    data = ui_document_to_dict(document)

    assert data["nodes"][0]["name"] == "ui_desktop"
    assert 'data-ui-source="dsl"' in html
    assert 'data-drag-region="titlebar"' in html
    assert "SOLUTION EXPLORER" in html
    assert "PROPERTIES" in svg
    assert 'width="2048" height="1125"' in svg
    assert "desktop_demo_preview" not in html


def test_editor_dsl_exports_as_window_tree() -> None:
    document = parse_ui_file(REPO_ROOT / "examples" / "ui" / "editor.ail")

    html = ui_document_to_html(document)
    svg = ui_document_to_svg(document)
    data = ui_document_to_dict(document)

    assert data["nodes"][0]["name"] == "editor"
    assert "AILang Editor - workspace" in html
    assert "SOLUTION EXPLORER" in svg
    assert 'data-ui-source="dsl"' in html


def test_editor_dsl_native_preview_emits_ailang_caption_button_interaction() -> None:
    document = parse_ui_file(REPO_ROOT / "examples" / "ui" / "editor.ail")
    ui_document_to_native_ail = _load_native_ail_exporter()

    native_ail = ui_document_to_native_ail(document)

    assert "import source.ui.ui_backend" in native_ail
    assert "import stdlib.ui.paint" in native_ail
    assert "#include" not in native_ail
    assert "dsl_caption_button_hit" in native_ail
    assert "hovered_caption_button" in native_ail
    assert "pressed_caption_button" in native_ail
    assert "dsl_draw_caption_button_face" in native_ail
    assert "ail_ui_minimize_window(window)" in native_ail
    assert "ail_ui_toggle_maximize_window(window)" in native_ail
    assert "caption_controls_right = 2" in native_ail
    assert "dsl_caption_button_hit(ail_ui_window_width_px(window)" in native_ail


def test_editor_dsl_native_preview_keeps_trailing_panels_anchored() -> None:
    document = parse_ui_file(REPO_ROOT / "examples" / "ui" / "editor.ail")
    ui_document_to_native_ail = _load_native_ail_exporter()

    native_ail = ui_document_to_native_ail(document)

    assert "width - 1706 - 0" not in native_ail
    assert "height - 941 - 0" not in native_ail
    assert "(width - 0 - 342)" in native_ail
    assert "(height - 0 - 184)" in native_ail
    assert "342, (height - 0 - 184), 1364, 30" not in native_ail
    assert "342, (height - 0 - 184), (width - 342 - 342), 30" in native_ail


def test_ui_dsl_native_preview_emits_grid_cells() -> None:
    document = parse_ui_source(
        """
window demo:
    width -> 320 px
    height -> 220 px
    body:
        grid:
            x -> 10 px
            y -> 20 px
            width -> 300 px
            height -> 120 px
            columns -> 2
            rows -> 1
            gap -> 8 px
            button:
                name -> "One"
            end
            button:
                name -> "Two"
            end
        end
    end
end
"""
    )
    ui_document_to_native_ail = _load_native_ail_exporter()

    native_ail = ui_document_to_native_ail(document)

    assert "((300 - (8 * 1)) / 2)" in native_ail
    assert "10 + (0 * (((300 - (8 * 1)) / 2) + 8))" in native_ail
    assert "10 + (1 * (((300 - (8 * 1)) / 2) + 8))" in native_ail
    assert '"One"' in native_ail
    assert '"Two"' in native_ail


def test_ui_dsl_native_preview_emits_adaptive_grid_panels_and_markers() -> None:
    document = parse_ui_file(REPO_ROOT / "examples" / "ui" / "svg_card_app.ail")
    ui_document_to_native_ail = _load_native_ail_exporter()

    native_ail = ui_document_to_native_ail(document)
    html = ui_document_to_html(document)
    svg = ui_document_to_svg(document)

    assert "width - 62 - 62" in native_ail
    assert "width - 82 - 82" in native_ail
    assert "dsl_draw_rounded_rect" in native_ail
    assert "0xd1d5db" in native_ail
    assert "dsl_draw_circle(window, ((82 + (0 *" in native_ail
    assert "dsl_draw_circle(window, ((82 + (1 *" in native_ail
    assert "dsl_draw_circle(window, ((82 + (2 *" in native_ail
    assert "right:72px" in html
    assert "grid-template-columns:repeat(3, minmax(0, 1fr))" in html
    assert "padding: 14px" not in html
    assert '<rect x="552" y="58" width="96" height="26"' in svg
    assert '<rect x="62" y="138" width="180" height="96"' in svg
    assert '<rect x="270" y="138" width="180" height="96"' in svg
    assert '<rect x="478" y="138" width="180" height="96"' in svg
    assert '<circle cx="89" cy="276" r="7" fill="#2563eb"/>' in svg
    assert '<circle cx="359" cy="276" r="7" fill="#10b981"/>' in svg
    assert '<circle cx="630" cy="276" r="7" fill="#f59e0b"/>' in svg


def test_ui_dsl_native_preview_emits_styled_text_and_graphics_primitives() -> None:
    document = parse_ui_source(
        """
window demo:
    width -> 320 px
    height -> 220 px
    layout -> "absolute"
    body:
        layout -> "absolute"
        x -> 0 px
        y -> 0 px
        width -> 320 px
        height -> 220 px
        panel card:
            x -> 20 px
            y -> 20 px
            width -> 180 px
            height -> 80 px
            background -> #ffffff
            border -> #d8dee9
            radius -> 8 px
            shadow -> #d1d5db
            shadow_x -> 2 px
            shadow_y -> 3 px
            text:
                x -> 12 px
                y -> 14 px
                text -> "AILang UI"
                font_size -> 26 px
                font_weight -> 700
            end
        end
        line:
            x1 -> 20 px
            y1 -> 120 px
            x2 -> 240 px
            y2 -> 120 px
            stroke -> #d1d5db
            stroke_width -> 2 px
        end
        circle:
            cx -> 30 px
            cy -> 120 px
            radius -> 7 px
            fill -> #2563eb
        end
    end
end
"""
    )
    ui_document_to_native_ail = _load_native_ail_exporter()

    native_ail = ui_document_to_native_ail(document)
    html = ui_document_to_html(document)
    svg = ui_document_to_svg(document)

    assert "ail_ui_draw_text_style" in native_ail
    assert '26, 700, 0x17212b, "AILang UI"' in native_ail
    assert "dsl_draw_rounded_rect" in native_ail
    assert "dsl_draw_circle(window, 30, 120, 7, 0x2563eb)" in native_ail
    assert "ail_ui_draw_rect(window, 20, 120, 220, 2, 0xd1d5db)" in native_ail
    assert "font-size:26px" in html
    assert "font-weight:700" in html
    assert '<circle cx="30" cy="120" r="7" fill="#2563eb"/>' in svg
    assert '<line x1="20" y1="120" x2="240" y2="120"' in svg

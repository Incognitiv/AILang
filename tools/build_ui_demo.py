#!/usr/bin/env python3
"""Build the live-window UI demo.

This is intentionally outside the normal `ailang.py --backend=c` path because
the demo links an extra platform backend source file. It keeps the UI platform
repeatable while the core compiler has no first-class UI backend concept yet.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
DEMO_SOURCE = REPO_ROOT / "examples" / "ui" / "ui_desktop.ail"
DEMO_PREVIEW_SOURCE = REPO_ROOT / "examples" / "ui" / "ui_desktop_dsl.ail"
BACKEND_SOURCE = REPO_ROOT / "examples" / "ui" / "backends" / "ail_ui_win32_min.c"
OUT_DIR = REPO_ROOT / "out" / "generated" / "ui"


def _platform_libs() -> list[str]:
    if sys.platform.startswith("win"):
        return ["-luser32", "-lgdi32"]
    return []


def main() -> int:
    gcc = shutil.which("gcc")
    if gcc is None:
        print("Error: gcc not found on PATH", file=sys.stderr)
        return 1

    sys.path.insert(0, str(SOURCE_ROOT))
    from cli.compilation import _extract_ailang_link_flags, _merge_link_flags
    from transpiler.core import transpile_file

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_c = OUT_DIR / "ui_desktop.c"
    output_exe = OUT_DIR / (
        "ui_desktop_demo.exe" if sys.platform.startswith("win") else "ui_desktop_demo"
    )

    transpile_file(str(DEMO_SOURCE), str(generated_c))
    try:
        generated_link_flags = _extract_ailang_link_flags(
            generated_c.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    link_flags = _merge_link_flags(generated_link_flags, ["-lm"], _platform_libs())

    cmd = [
        gcc,
        "-std=gnu23",
        "-O2",
        str(generated_c),
        str(BACKEND_SOURCE),
        "-o",
        str(output_exe),
        *link_flags,
    ]
    proc = subprocess.run(
        cmd,
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

    from ui_dsl import parse_ui_file, ui_document_to_html, ui_document_to_svg

    document = parse_ui_file(DEMO_PREVIEW_SOURCE)
    html_path = OUT_DIR / "ui_desktop_demo.html"
    svg_path = OUT_DIR / "ui_desktop_demo.svg"
    html_path.write_text(ui_document_to_html(document), encoding="utf-8")
    svg_path.write_text(ui_document_to_svg(document), encoding="utf-8")
    print(html_path)
    print(svg_path)
    print(output_exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

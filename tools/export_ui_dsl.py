from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ui_dsl import (  # noqa: E402
    parse_ui_file,
    ui_document_to_dict,
    ui_document_to_html,
    ui_document_to_svg,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export canonical AILang UI DSL previews."
    )
    parser.add_argument("source", type=Path, help="AILang UI DSL source file")
    parser.add_argument(
        "--format",
        choices=("json", "html", "svg"),
        default="html",
        help="Preview/export format",
    )
    parser.add_argument("--out", type=Path, help="Output path")
    parser.add_argument(
        "--no-expand-includes",
        action="store_true",
        help="Only parse the selected file, without recursively loading include lines",
    )
    args = parser.parse_args()

    document = parse_ui_file(args.source, expand_includes=not args.no_expand_includes)
    if args.format == "json":
        output = json.dumps(ui_document_to_dict(document), indent=2)
        default_suffix = ".json"
    elif args.format == "svg":
        output = ui_document_to_svg(document)
        default_suffix = ".svg"
    else:
        output = ui_document_to_html(document)
        default_suffix = ".html"

    out_path = args.out
    if out_path is None:
        out_dir = REPO_ROOT / "out" / "generated" / "ui"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.source.stem}_preview{default_suffix}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

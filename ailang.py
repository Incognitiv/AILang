#!/usr/bin/env python3
"""Compatibility launcher for the CLI implementation in ``source/cli/main.py``."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Run the CLI entrypoint."""

    root = Path(__file__).resolve()
    source_root = root.with_name("source")
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

    from cli.main import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())

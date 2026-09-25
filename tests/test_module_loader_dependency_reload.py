"""Use the real parser and loader to test cached import-closure replacement."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler.modules import ModuleLoader


def test_changed_leaf_rebuilds_importing_modules(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf.ail"
    leaf.write_text("int leaf():\n    return 1\nend\n", encoding="utf-8")
    (tmp_path / "middle.ail").write_text(
        "from leaf import leaf\nint middle():\n    return leaf()\nend\n",
        encoding="utf-8",
    )
    (tmp_path / "root.ail").write_text(
        "from middle import middle\nint root():\n    return middle()\nend\n",
        encoding="utf-8",
    )
    loader = ModuleLoader([str(tmp_path)])
    first = loader.load_module("root")
    first_leaf = first.implementation["leaf"]
    with patch.object(loader, "_load_file", wraps=loader._load_file) as parse:
        assert loader.load_module("root") is first
    assert parse.call_count == 0
    leaf.write_text("int leaf():\n    return 1234567\nend\n", encoding="utf-8")
    second = loader.load_module("root")
    assert second is not first
    assert second.implementation["leaf"] is not first_leaf
    assert loader.load_module("root") is second

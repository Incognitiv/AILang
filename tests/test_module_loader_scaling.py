"""Real parser/loader stress and ordering tests; native backend tests are separate."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler.modules import ModuleLoader


def write_module(tmp_path: Path, name: str, source: str) -> None:
    (tmp_path / f"{name}.ail").write_text(source, encoding="utf-8")


def test_plain_and_selective_visibility_and_order(tmp_path: Path) -> None:
    write_module(tmp_path, "leaf", "int left():\nreturn 1\nend\nint hidden():\nreturn 2\nend\n")
    write_module(tmp_path, "middle", "from leaf import left\nint middle():\nreturn left()\nend\n")
    loader = ModuleLoader([str(tmp_path)])
    module = loader.load_module("middle")
    assert list(module.exports) == ["middle", "left"]
    assert list(module.implementation) == ["middle", "left", "hidden"]


def test_deep_import_graph_does_not_recurse(tmp_path: Path) -> None:
    count = 1200
    for index in range(count):
        dependency = "" if index == 0 else f"import item_{index - 1}\n"
        write_module(tmp_path, f"item_{index}", dependency + f"int value_{index}():\nreturn {index}\nend\n")
    loader = ModuleLoader([str(tmp_path)])
    root = loader.load_module(f"item_{count - 1}")
    assert len(root.implementation) == count
    assert root.implementation["value_0"].name == "value_0"
    assert len(loader.cache.modules) == count
    assert not loader.cache.loading
    assert loader.current_file is None
    with patch.object(loader, "_load_file", wraps=loader._load_file) as parse:
        assert loader.load_module(f"item_{count - 1}") is root
    assert parse.call_count == 0


def test_missing_plain_import_remains_a_warning(tmp_path: Path, capsys) -> None:
    write_module(tmp_path, "root", "import absent\nint answer():\nreturn 1\nend\n")
    loader = ModuleLoader([str(tmp_path)])
    assert "answer" in loader.load_module("root").exports
    assert "Warning:" in capsys.readouterr().err
    assert not loader.cache.loading


def test_failed_selective_import_unwinds_without_poisoning_cache(tmp_path: Path) -> None:
    write_module(tmp_path, "root", "from child import answer\n")
    write_module(tmp_path, "child", "from absent import missing\n")
    loader = ModuleLoader([str(tmp_path)])
    with pytest.raises(ImportError):
        loader.load_module("root")
    assert not loader.cache.loading
    assert loader.current_file is None
    write_module(tmp_path, "child", "int answer():\nreturn 42\nend\n")
    assert "answer" in loader.load_module("root").exports


def test_plain_parent_can_warn_about_failed_selective_child(tmp_path: Path, capsys) -> None:
    write_module(tmp_path, "root", "import child\nint answer():\nreturn 1\nend\n")
    write_module(tmp_path, "child", "from absent import missing\n")
    loader = ModuleLoader([str(tmp_path)])
    assert "answer" in loader.load_module("root").exports
    assert "Warning:" in capsys.readouterr().err
    assert not loader.cache.loading


def test_selective_cycle_fails_and_cleans_stack(tmp_path: Path) -> None:
    write_module(tmp_path, "one", "from two import two\nint one():\nreturn 1\nend\n")
    write_module(tmp_path, "two", "from one import one\nint two():\nreturn 2\nend\n")
    loader = ModuleLoader([str(tmp_path)])
    with pytest.raises(ImportError, match="Circular import"):
        loader.load_module("one")
    assert not loader.cache.loading


def test_million_source_lines_in_modular_frontend(tmp_path: Path) -> None:
    """One million actual source lines, not blank padding; parsing only.

    This regression tests the imported AST/symbol graph, not million-line LLVM
    optimization, linking, or native execution. Small native tests cover those.
    """
    modules = 1001
    functions_per_module = 333
    line_count = 0
    for index in range(modules):
        lines = []
        if index:
            lines.append(f"import unit_{index - 1}")
        for function in range(functions_per_module):
            name = f"f_{index}_{function}"
            lines.extend((f"int {name}():", f"return {function}", "end"))
        line_count += len(lines)
        write_module(tmp_path, f"unit_{index}", "\n".join(lines) + "\n")
    assert line_count >= 1_000_000
    loader = ModuleLoader([str(tmp_path)])
    root = loader.load_module(f"unit_{modules - 1}")
    assert len(root.implementation) == modules * functions_per_module
    local_symbols = sum(len(module.implementation._local) for module in loader.cache.modules.values())
    assert local_symbols == modules * functions_per_module
    assert not loader.cache.loading

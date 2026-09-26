"""Native linking is a compiler responsibility, not a user-selected step."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler.memory_link import _resolve_link_driver, memory_link_flags_supported


def test_explicit_driver_is_respected() -> None:
    with patch("shutil.which", return_value="/tools/custom") as lookup:
        assert _resolve_link_driver("custom") == "/tools/custom"
    lookup.assert_called_once_with("custom")


def test_linker_selection_is_automatic() -> None:
    with patch("shutil.which", side_effect=[None, "/usr/bin/gcc"]) as lookup:
        assert _resolve_link_driver(None) == "/usr/bin/gcc"
    assert [call.args for call in lookup.call_args_list] == [("clang",), ("gcc",)]


def test_no_driver_returns_no_false_success() -> None:
    with patch("shutil.which", return_value=None):
        assert _resolve_link_driver(None) is None


def test_supported_flags_and_specialist_flags_are_distinct() -> None:
    assert memory_link_flags_supported(("-lm", "-pthread", "-L/local/libs"))
    assert not memory_link_flags_supported(("-Wl,-Map,output.map",))
    assert not memory_link_flags_supported(("-o", "other-output"))

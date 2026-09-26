"""The line policy must cover tracked source even under excluded-looking paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from source_inventory import audit_tracked_sources, count_physical_lines, inventory_paths


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("terminated", [True, False])
def test_physical_line_boundaries(newline: str, terminated: bool) -> None:
    text = newline.join("x" for _ in range(750)) + (newline if terminated else "")
    assert count_physical_lines(text) == 750
    assert count_physical_lines(text + ("x" if terminated else newline + "x")) == 751


def test_empty_and_unicode_separators() -> None:
    assert count_physical_lines("") == 0
    assert count_physical_lines('value = "a\u2028b\u2029c"\n') == 1


@pytest.mark.parametrize("name", [
    "source/a.py", "source/generated/a.py", "out/a.ail",
    "tests/a.py", "lib/a.ail", "verifier/a.py", "types/a.pyi",
])
def test_tracked_sources_have_no_directory_exemption(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.parent.mkdir(parents=True)
    source.write_text("x\n" * 751)
    result = inventory_paths(tmp_path, [name])
    assert not result["passed"]
    assert result["oversized_files"] == [{"path": name, "lines": 751}]


def test_exact_limit_and_duplicate_paths(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("# line\n" * 750)
    result = inventory_paths(tmp_path, ["a.py", "a.py"])
    assert result["passed"]
    assert result["scanned_files"] == 1
    assert result["max_file_line_count"] == 750


@pytest.mark.parametrize("name", ["missing.py", "../outside.py", "/outside.py"])
def test_unavailable_or_escaping_source_fails(tmp_path: Path, name: str) -> None:
    assert not inventory_paths(tmp_path, [name])["passed"]


def test_invalid_encoding_fails(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_bytes(b"\xff")
    assert not inventory_paths(tmp_path, ["broken.py"])["passed"]


def test_empty_inventory_is_not_a_pass(tmp_path: Path) -> None:
    assert not inventory_paths(tmp_path, [])["passed"]


def test_non_source_data_is_not_code(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("pass\n")
    result = inventory_paths(tmp_path, ["a.py", "huge.json", "photo.png"])
    assert result["passed"]
    assert result["scanned_files"] == 1


def test_git_inventory_error_fails_closed(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("git unavailable")):
        assert not audit_tracked_sources(tmp_path)["passed"]


def test_real_git_inventory_includes_newly_staged_files_only(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "a.py").write_text("pass\n")
    (tmp_path / "untracked.py").write_text("x\n" * 1000)
    git("add", "a.py")
    assert audit_tracked_sources(tmp_path)["passed"]
    git("add", "untracked.py")
    assert not audit_tracked_sources(tmp_path)["passed"]


@pytest.mark.parametrize("name", [
    "examples/ui/backends/generated/wayland/xdg-shell-client-protocol.h",
    "source/ui/generated/wayland/xdg-shell-client-protocol.h",
    "tests/corpus/reference.c", "benchmarks/reference.rs", "boot/reference.asm",
])
def test_foreign_sources_do_not_expand_the_ailang_line_policy(tmp_path: Path, name: str) -> None:
    (tmp_path / "compiler.py").write_text("pass\n")
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("/* reference */\n" * 2381)
    result = inventory_paths(tmp_path, ["compiler.py", name])
    assert result["passed"]
    assert result["scanned_files"] == 1
    assert result["limit"] == 750
    assert result["source_suffixes"] == [".ail", ".py", ".pyi"]
    assert path.read_text().count("\n") == 2381

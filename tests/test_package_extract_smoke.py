from __future__ import annotations

import io
import importlib.util
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "package_extract_smoke.py"
SPEC = importlib.util.spec_from_file_location("package_extract_smoke_tool", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
pes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pes)


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)


def _write_tar_gz(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def test_extract_archive_rejects_zip_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "bad.zip"
        out = root / "out"
        out.mkdir()
        _write_zip(archive, {"../escape.txt": b"x"})
        with pytest.raises(ValueError, match="unsafe zip entry path"):
            pes._extract_archive(archive, out)


def test_extract_archive_rejects_tar_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "bad.tar.gz"
        out = root / "out"
        out.mkdir()
        _write_tar_gz(archive, {"../escape.txt": b"x"})
        with pytest.raises(ValueError, match="unsafe tar entry path"):
            pes._extract_archive(archive, out)


def test_find_first_matching_file_supports_split_release_layout() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bin_path = root / "AILang-1.0.0-linux-x64" / "dist" / "ailangc"
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.write_bytes(b"#!/bin/sh\necho ok\n")
        found = pes._find_first_matching_file(
            root, ["AILang-*-linux-x64/dist/ailangc"]
        )
        assert found == bin_path

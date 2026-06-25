from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"
VERSION_AIL = REPO_ROOT / "source" / "version.ail"


def _const_string(source: str, name: str) -> str:
    match = re.search(rf'^const string {name}\s*=\s*"([^"]*)"', source, re.M)
    assert match, f"missing string constant {name}"
    return match.group(1)


def _const_int(source: str, name: str) -> int:
    match = re.search(rf"^const int {name}\s*=\s*(\d+)", source, re.M)
    assert match, f"missing integer constant {name}"
    return int(match.group(1))


def test_ailang_version_seed_matches_python_metadata() -> None:
    py_version = importlib.import_module("source.version")
    ail_source = VERSION_AIL.read_text(encoding="utf-8")

    assert _const_string(ail_source, "AILANG_VERSION") == py_version.__version__
    assert (
        _const_int(ail_source, "AILANG_VERSION_MAJOR"),
        _const_int(ail_source, "AILANG_VERSION_MINOR"),
        _const_int(ail_source, "AILANG_VERSION_PATCH"),
    ) == py_version.__version_info__
    assert _const_string(ail_source, "AILANG_RELEASE_NAME") == py_version.RELEASE_NAME
    assert _const_string(ail_source, "AILANG_RELEASE_DATE") == py_version.RELEASE_DATE
    assert _const_string(ail_source, "AILANG_CODENAME") == py_version.CODENAME
    assert _const_int(ail_source, "AILANG_FEATURE_COUNT") == len(py_version.FEATURES)
    assert _const_int(ail_source, "AILANG_ENABLED_FEATURE_COUNT") == sum(
        1 for enabled in py_version.FEATURES.values() if enabled
    )


def test_ailang_version_seed_passes_check() -> None:
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(VERSION_AIL), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        proc.returncode == 0
    ), f"--check failed for version.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

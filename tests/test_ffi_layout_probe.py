from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def test_ffi_report_json_includes_c_layout_probe(tmp_path: Path) -> None:
    if _available_c_compiler() is None:
        pytest.skip("no C compiler available")

    src = tmp_path / "layout_surface.ail"
    src.write_text(
        """\
typedef [byte; 4] Hash4

@packed
record NativePacket then
    byte tag
    int value
    Hash4 digest
end

union NativeWord then
    int whole
    [byte; 8] bytes
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--ffi-report-json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    probe = payload["layout_probe"]
    assert probe["status"] == "ok"

    packet = probe["records"]["NativePacket"]
    assert packet["size"] == 13
    assert packet["align"] == 1
    assert packet["fields"]["tag"] == {"offset": 0, "size": 1}
    assert packet["fields"]["value"] == {"offset": 1, "size": 8}
    assert packet["fields"]["digest"] == {"offset": 9, "size": 4}
    assert payload["records"][0]["layout"] == packet

    word = probe["unions"]["NativeWord"]
    assert word["size"] == 8
    assert word["fields"]["whole"] == {"offset": 0, "size": 8}
    assert word["fields"]["bytes"] == {"offset": 0, "size": 8}
    assert payload["unions"][0]["layout"] == word

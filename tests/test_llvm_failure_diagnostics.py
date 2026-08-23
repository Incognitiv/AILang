from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cli.llvm_diagnostics import (  # noqa: E402
    _derive_missing_link_hints,
    _extract_undefined_symbols,
    _format_llvm_failure_diagnostics,
)


def test_extract_undefined_symbols_handles_gnu_and_msvc_formats() -> None:
    blob = """
/usr/bin/ld: /tmp/main.o: in function `main':
main.c:(.text+0x11): undefined reference to `sqlite3_open'
main.obj : error LNK2019: unresolved external symbol __imp_WSAStartup
referenced in function _main
ld.lld: error: undefined symbol: pthread_create
"""
    symbols = _extract_undefined_symbols(blob)
    assert "sqlite3_open" in symbols
    assert "WSAStartup" in symbols
    assert "pthread_create" in symbols


def test_derive_missing_link_hints_sqlite_flag_missing() -> None:
    hints = _derive_missing_link_hints(["sqlite3_open"], [], platform="linux")
    assert any("-lsqlite3" in row for row in hints)


def test_derive_missing_link_hints_sqlite_flag_present() -> None:
    hints = _derive_missing_link_hints(
        ["sqlite3_open", "sqlite3_exec"], ["-lsqlite3"], platform="linux"
    )
    joined = "\n".join(hints).lower()
    assert "lsqlite3" in joined
    assert "install sqlite" in joined or "search path" in joined


def test_format_llvm_failure_diagnostics_includes_hints_and_detail() -> None:
    stderr = "undefined reference to `sqlite3_open'\ncollect2: error: ld returned 1 exit status\n"
    text = _format_llvm_failure_diagnostics(
        "clang",
        stderr=stderr,
        stdout="",
        link_flags=[],
        platform="linux",
    )
    lower = text.lower()
    assert "clang failed with unresolved symbol" in lower
    assert "sqlite3_open" in text
    assert "hint:" in lower
    assert "-lsqlite3" in text
    assert "detail:" in lower


def test_format_llvm_failure_diagnostics_prefers_error_over_warning() -> None:
    stderr = (
        "warning: overriding module target triple with x86_64\n"
        "clang: error: unable to open output file '/missing/app': No such file\n"
    )
    text = _format_llvm_failure_diagnostics(
        "clang",
        stderr=stderr,
        stdout="",
        link_flags=[],
        platform="linux",
    )
    assert "detail: clang: error: unable to open output file" in text
    assert "detail: warning:" not in text

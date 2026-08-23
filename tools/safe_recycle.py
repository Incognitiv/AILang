#!/usr/bin/env python3
"""Move explicit paths to the OS trash/recycle bin.

This helper is intentionally narrow: it accepts explicit paths only, refuses
missing paths by default, and never expands shell globs itself.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _display(path: Path) -> str:
    return str(path).encode("ascii", "backslashreplace").decode("ascii")


def _windows_recycle(path: Path) -> bool:
    source = str(path.resolve()) + "\0\0"

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_bool),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    fo_delete = 3
    fof_allowundo = 0x0040
    fof_noconfirmation = 0x0010
    fof_silent = 0x0004
    fof_noerrorui = 0x0400
    op = SHFILEOPSTRUCTW(
        None,
        fo_delete,
        source,
        None,
        fof_allowundo | fof_noconfirmation | fof_silent | fof_noerrorui,
        False,
        None,
        None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted


def _fallback_quarantine(path: Path, root: Path) -> Path:
    quarantine = root / "archived" / f"safe-recycle-fallback-{datetime.now():%Y%m%d-%H%M%S}"
    quarantine.mkdir(parents=True, exist_ok=True)
    target = quarantine / path.name
    counter = 2
    while target.exists():
        target = quarantine / f"{path.name}.{counter}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


def recycle(path: Path, *, repo_root: Path, dry_run: bool) -> int:
    if not path.exists():
        print(f"missing: {_display(path)}", file=sys.stderr)
        return 1
    if dry_run:
        print(f"would recycle: {_display(path)}")
        return 0
    if os.name == "nt" and _windows_recycle(path):
        print(f"recycled: {_display(path)}")
        return 0
    target = _fallback_quarantine(path, repo_root)
    print(f"trash unavailable; moved to quarantine: {_display(target)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Explicit file or directory paths to recycle.")
    parser.add_argument("--yes", action="store_true", help="Actually recycle paths. Default is dry-run.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    status = 0
    for raw in args.paths:
        status |= recycle(Path(raw), repo_root=repo_root, dry_run=not args.yes)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

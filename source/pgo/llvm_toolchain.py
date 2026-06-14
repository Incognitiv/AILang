"""LLVM toolchain resolution helpers."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _exe_name(name: str) -> str:
    if sys.platform.startswith("win") and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def _bin_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.name.lower() == "bin" else path / "bin"


def _append_existing_unique(dirs: list[Path], path: Path | None) -> None:
    if path is None or not path.exists():
        return
    resolved = path.resolve()
    if not any(
        existing.resolve() == resolved for existing in dirs if existing.exists()
    ):
        dirs.append(path)


def _windows_system_drive_root() -> Path | None:
    drive = os.environ.get("SystemDrive", "").strip().rstrip("\\/")
    if not drive:
        return None
    if drive.endswith(":"):
        return Path(f"{drive}\\")
    return Path(drive)


def _windows_env_llvm_dirs() -> list[Path]:
    dirs: list[Path] = []
    # MSYS2 sets one of these when called from an MSYS2/MinGW environment.
    for name in ("MINGW_PREFIX", "MSYSTEM_PREFIX"):
        _append_existing_unique(dirs, _bin_dir(_env_path(name)))

    # Common MSYS2 roots, derived from SystemDrive instead of hardcoding C:.
    drive_root = _windows_system_drive_root()
    if drive_root is not None:
        for subsystem in ("mingw64", "clang64", "ucrt64"):
            _append_existing_unique(dirs, drive_root / "msys64" / subsystem / "bin")

    # ProgramW6432 bypasses 32-bit process redirection and points to native
    # 64-bit Program Files on 64-bit Windows.
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        root = _env_path(name)
        if root is not None:
            _append_existing_unique(dirs, root / "LLVM" / "bin")
    return dirs


def preferred_llvm_bin_dirs() -> list[Path]:
    """Return LLVM binary directories in coherence-preferred order."""
    dirs: list[Path] = []
    _append_existing_unique(dirs, _env_path("AILANG_LLVM_BIN"))
    for name in ("LLVM_HOME", "LLVM_ROOT"):
        _append_existing_unique(dirs, _bin_dir(_env_path(name)))
    if sys.platform.startswith("win"):
        dirs.extend(_windows_env_llvm_dirs())
    return dirs


def resolve_llvm_tool(name: str) -> str | None:
    """Resolve LLVM-family tools without mixing incompatible toolchain roots."""
    exe_name = _exe_name(name)
    for bin_dir in preferred_llvm_bin_dirs():
        candidate = bin_dir / exe_name
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def llvm_toolchain_root_for(tool_path: str | None) -> Path | None:
    """Return the bin directory for a resolved tool path."""
    if not tool_path:
        return None
    return Path(tool_path).resolve().parent


def same_llvm_root_tool(anchor_tool: str | None, name: str) -> str | None:
    """Resolve a tool from the same bin directory as an already chosen tool."""
    root = llvm_toolchain_root_for(anchor_tool)
    if root is not None:
        candidate = root / _exe_name(name)
        if candidate.exists():
            return str(candidate)
        return None
    return resolve_llvm_tool(name)

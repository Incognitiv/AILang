"""Shared target-name normalization for backend/runtime introspection."""

from __future__ import annotations

import sys


def normalize_os_name(name: str | None) -> str | None:
    """Normalize user-facing target OS aliases."""
    if name is None:
        return None
    value = name.strip().lower()
    if not value:
        return None
    if value in {"*", "all", "any"}:
        return value
    aliases = {
        "win": "windows",
        "win32": "windows",
        "win64": "windows",
        "windows": "windows",
        "linux": "linux",
        "freebsd": "freebsd",
        "mac": "macos",
        "macos": "macos",
        "darwin": "macos",
        "osx": "macos",
        "wasm": "wasm",
        "webassembly": "wasm",
    }
    return aliases.get(value, value)


def target_matches(directive_target: str | None, current_os: str | None) -> bool:
    """Return True when a directive target applies to the current target OS."""
    target = normalize_os_name(directive_target)
    if target is None or target in {"*", "all", "any"}:
        return True
    os_name = normalize_os_name(current_os)
    capability_targets = {
        # X11 is a window-system capability, not an OS. Keep the directive
        # readable while allowing checked bindings on Unix-like hosts.
        "x11": {"linux", "freebsd", "macos"},
    }
    if target in capability_targets:
        return os_name in capability_targets[target]
    return target == os_name


def os_from_platform(platform: str | None = None) -> str:
    """Return AILang's stable OS name for a Python platform string."""
    value = (platform or sys.platform).lower()
    if value.startswith(("win32", "cygwin", "msys")):
        return "windows"
    if value.startswith("linux"):
        return "linux"
    if value.startswith("freebsd"):
        return "freebsd"
    if value.startswith("darwin"):
        return "macos"
    return "unknown"


def os_from_triple(triple: str | None) -> str:
    """Return AILang's stable OS name for an LLVM target triple."""
    value = (triple or "").lower()
    if not value:
        return os_from_platform()
    if any(marker in value for marker in ("windows", "win32", "mingw", "msvc")):
        return "windows"
    if "linux" in value:
        return "linux"
    if "freebsd" in value:
        return "freebsd"
    if "darwin" in value or "apple" in value or "macos" in value:
        return "macos"
    if "wasm" in value:
        return "wasm"
    return "unknown"

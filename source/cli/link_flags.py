"""Native toolchain and AILang linker-flag normalization."""

from __future__ import annotations

import shlex

from target_info import normalize_os_name, os_from_platform, target_matches


def _split_ailang_link_flags(raw_flags: str) -> list[str]:
    """Split one #link payload into subprocess-safe argv tokens."""
    raw_flags = (raw_flags or "").strip()
    if not raw_flags:
        return []
    try:
        flags = shlex.split(raw_flags, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid #link flags {raw_flags!r}: {exc}") from exc
    bad = [flag for flag in flags if "\x00" in flag or "\n" in flag or "\r" in flag]
    if bad:
        raise ValueError(f"invalid #link flag contains control character: {bad[0]!r}")
    return flags


def _split_targeted_link_payload(raw: str) -> tuple[str | None, str]:
    """Split optional target prefix from a #link payload."""
    payload = raw.strip()
    if not payload or payload[0] in {'"', "-"}:
        return None, payload
    parts = payload.split(None, 1)
    if len(parts) != 2:
        return None, payload
    target, remainder = parts
    if any(ch in target for ch in '/\\.:"<>'):
        return None, payload
    return normalize_os_name(target), remainder.strip()


def _extract_ailang_link_flags(text: str, *, target_os: str | None = None) -> list[str]:
    """Extract explicit AILang link flags from source/C/LLVM text.

    Supported forms:
      #link "-luser32 -lgdi32"
      #link windows "-luser32 -lgdi32"
      /* AILANG_LINK: -luser32 -lgdi32 */
      ; AILANG_LINK: -luser32 -lgdi32
    """
    flags: list[str] = []
    current_os = target_os or os_from_platform()
    for line in (text or "").splitlines():
        stripped = line.strip()
        raw = ""
        directive_target = None
        if stripped.startswith("#link"):
            raw = stripped[len("#link") :].strip()
            directive_target, raw = _split_targeted_link_payload(raw)
            if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                raw = raw[1:-1]
        elif "AILANG_LINK:" in stripped:
            raw = stripped.split("AILANG_LINK:", 1)[1].strip()
            if "*/" in raw:
                raw = raw.split("*/", 1)[0].strip()
        if raw and target_matches(directive_target, current_os):
            flags.extend(_split_ailang_link_flags(raw))
    return flags


def _merge_link_flags(*groups: list[str]) -> list[str]:
    """Merge link flags preserving first occurrence order."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for flag in group:
            if flag in seen:
                continue
            merged.append(flag)
            seen.add(flag)
    return merged


def _normalize_native_toolchain(native_toolchain: str) -> str:
    """Normalize user-facing native toolchain aliases."""
    value = (native_toolchain or "auto").strip().lower().replace("_", "-")
    aliases = {
        "default": "auto",
        "llvm": "clang",
        "clang-llvm": "clang",
        "gnu": "gcc",
        "gcc-gnu": "gcc",
        "llc+gcc": "llc-gcc",
        "llc_gcc": "llc-gcc",
    }
    return aliases.get(value, value)

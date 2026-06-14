"""LLVM AOT linker/diagnostic helpers shared by CLI compilation paths."""

from __future__ import annotations

import re
import sys

_UNDEFINED_SYMBOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:undefined reference to|undefined symbol:)\s*[`'\" ]*([A-Za-z0-9_@$?.]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"unresolved external symbol\s+([A-Za-z0-9_@$?.]+)",
        re.IGNORECASE,
    ),
)


def _detect_llvm_link_flags(ir_src: str, *, platform: str | None = None) -> list[str]:
    """Infer required linker flags by scanning emitted LLVM IR text."""
    platform_name = platform or sys.platform
    flags: list[str] = []

    winsock_ref = re.search(
        r'@"?(?:WSA[A-Za-z0-9_]*|closesocket|ioctlsocket|getaddrinfo|freeaddrinfo|'
        r"socket|bind|listen|accept|connect|send|recv|shutdown|inet_pton|inet_ntop)"
        r'"?',
        ir_src,
    )
    if winsock_ref and platform_name.startswith("win"):
        flags.append("-lws2_32")
    win32_ref = re.search(
        r'@"?(?:LoadLibraryA|GetProcAddress|FreeLibrary|GetLastError|'
        r'MultiByteToWideChar|LocalFree)"?',
        ir_src,
    )
    if win32_ref and platform_name.startswith("win"):
        flags.append("-lkernel32")
    if re.search(r'@"?sqlite3_[A-Za-z0-9_]*"?', ir_src):
        flags.append("-lsqlite3")
    if re.search(
        r'@"?pthread_[A-Za-z0-9_]*"?', ir_src
    ) and not platform_name.startswith("win"):
        flags.append("-lpthread")
    return flags


def _first_nonempty_line(text: str) -> str:
    """Return the first useful non-empty diagnostic line."""
    lines = [row.strip() for row in text.splitlines() if row.strip()]
    for line in lines:
        lower = line.lower()
        if (
            "cannot open output file" in lower
            or "no such file or directory" in lower
            or "cannot find" in lower
        ):
            return line
    for line in lines:
        lower = line.lower()
        if "error:" in lower or "undefined reference" in lower:
            return line
    for line in lines:
        lower = line.lower()
        if "warning:" in lower or lower.startswith("note:"):
            continue
        if re.match(r"^\d+\s+\|", line) or line.startswith("|"):
            continue
        if line.startswith("^") or line.startswith("~"):
            continue
        return line
    return lines[0] if lines else ""


def _normalize_link_symbol(sym: str) -> str:
    """Normalize linker symbol spelling for hint classification."""
    text = sym.strip().strip("`'\"")
    # Common import-table decorations seen on Windows linkers.
    for prefix in ("__imp_", "_imp__", "__imp__", "_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def _extract_undefined_symbols(blob: str) -> list[str]:
    """Extract unresolved symbol names from linker/compiler diagnostics."""
    if not blob.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for pat in _UNDEFINED_SYMBOL_PATTERNS:
        for match in pat.finditer(blob):
            sym = _normalize_link_symbol(match.group(1))
            if not sym:
                continue
            if sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
    return out


def _derive_missing_link_hints(
    symbols: list[str],
    link_flags: list[str],
    *,
    platform: str | None = None,
) -> list[str]:
    """Map unresolved symbols to actionable link/toolchain hints."""
    if not symbols:
        return []
    flags = set(link_flags)
    syms = [s.lower() for s in symbols]
    platform_name = platform or sys.platform
    hints: list[str] = []

    sqlite_unresolved = any("sqlite3_" in s for s in syms)
    if sqlite_unresolved:
        if "-lsqlite3" in flags:
            hints.append(
                "SQLite symbols are unresolved even though -lsqlite3 is present; "
                "install SQLite dev libraries and ensure the linker search path "
                "can resolve libsqlite3."
            )
        else:
            hints.append(
                "SQLite symbols are unresolved; add -lsqlite3 (or fix LLVM "
                "link-flag auto-detection)."
            )

    pthread_unresolved = any(s.startswith("pthread_") for s in syms)
    if pthread_unresolved and not platform_name.startswith("win"):
        if "-lpthread" in flags:
            hints.append(
                "pthread symbols are unresolved even though -lpthread is present; "
                "ensure pthread runtime/dev libraries are installed."
            )
        else:
            hints.append(
                "pthread symbols are unresolved; add -lpthread (or fix LLVM "
                "link-flag auto-detection)."
            )

    winsock_markers = (
        "wsastartup",
        "wsacleanup",
        "closesocket",
        "ioctlsocket",
        "getaddrinfo",
        "freeaddrinfo",
    )
    winsock_unresolved = any(
        s.startswith("wsa") or any(marker in s for marker in winsock_markers)
        for s in syms
    )
    if winsock_unresolved and platform_name.startswith("win"):
        if "-lws2_32" in flags:
            hints.append(
                "Winsock symbols are unresolved even though -lws2_32 is present; "
                "verify MinGW/clang linker setup for ws2_32."
            )
        else:
            hints.append(
                "Winsock symbols are unresolved; add -lws2_32 (or fix LLVM "
                "link-flag auto-detection)."
            )

    return hints


def _format_llvm_failure_diagnostics(
    stage: str,
    *,
    stderr: str,
    stdout: str,
    link_flags: list[str],
    platform: str | None = None,
) -> str:
    """Build concise, actionable failure diagnostics for LLVM AOT stages."""
    blob = f"{stderr}\n{stdout}"
    symbols = _extract_undefined_symbols(blob)
    hints = _derive_missing_link_hints(symbols, link_flags, platform=platform)
    first_line = _first_nonempty_line(stderr) or _first_nonempty_line(stdout)

    lines: list[str] = []
    if symbols:
        preview = ", ".join(symbols[:6])
        if len(symbols) > 6:
            preview += ", ..."
        lines.append(f"{stage} failed with unresolved symbol(s): {preview}")
    else:
        lines.append(f"{stage} failed")
    for hint in hints:
        lines.append(f"hint: {hint}")
    if first_line:
        lines.append(f"detail: {first_line}")
    return "\n".join(lines)

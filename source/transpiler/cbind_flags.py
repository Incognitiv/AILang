"""Small helpers for cbind/cimport compiler-flag normalization."""

from __future__ import annotations


def headers_from_cflags(cflags: object) -> list[dict[str, object]]:
    """Convert cbind `-include header.h` flags into CInclude descriptors.

    The probe compiler sees these headers, so the final C translation unit
    must include them too when cimport relies on native header declarations.
    """

    if not isinstance(cflags, list):
        return []
    headers: list[dict[str, object]] = []
    i = 0
    while i < len(cflags):
        flag = str(cflags[i]).strip()
        header = ""
        if flag == "-include" and i + 1 < len(cflags):
            header = str(cflags[i + 1]).strip()
            i += 2
        elif flag.startswith("-include") and len(flag) > len("-include"):
            header = flag[len("-include") :].strip()
            i += 1
        else:
            i += 1
        if not header:
            continue
        header = header.strip('<>"')
        if header:
            headers.append({"path": header, "system": True})
    return headers

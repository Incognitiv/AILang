"""Linux memory-backed native linking and atomic final publication.

No disk-spill fallback is implicit. memfd pages are pageable OS memory; this is
not a guarantee against swap, toolchain caches, or unrelated process I/O.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024


def memory_link_supported() -> bool:
    """The initial adapter deliberately supports Linux memfd/procfs only."""
    return (
        sys.platform.startswith("linux")
        and hasattr(os, "memfd_create")
        and Path("/proc/self/fd").is_dir()
    )


def link_object_in_memory(
    object_code: bytes,
    linker: str,
    link_flags: tuple[str, ...] = (),
    *,
    timeout: float = 120,
    max_executable_bytes: int = DEFAULT_MAX_EXECUTABLE_BYTES,
) -> bytes:
    """Link with private memfd inputs/output and return a successful ELF image."""
    if not memory_link_supported():
        raise RuntimeError("memory linking requires Linux memfd and /proc/self/fd")
    if not object_code or max_executable_bytes < 1 or timeout <= 0:
        raise ValueError("object, output limit, and timeout must be positive")
    executable = shutil.which(linker)
    if executable is None:
        raise FileNotFoundError(f"native linker driver not found: {linker}")
    for flag in link_flags:
        if not _memory_link_flag_allowed(flag):
            raise ValueError(f"unsupported memory-link option: {flag!r}")
    with os.fdopen(os.memfd_create("ailang-object", os.MFD_CLOEXEC), "w+b") as source:
        with os.fdopen(
            os.memfd_create("ailang-executable", os.MFD_CLOEXEC), "w+b"
        ) as output:
            source.write(object_code)
            source.flush()
            result = subprocess.run(
                [
                    executable,
                    f"/proc/self/fd/{source.fileno()}",
                    "-o",
                    f"/proc/self/fd/{output.fileno()}",
                    *link_flags,
                ],
                pass_fds=(source.fileno(), output.fileno()),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
            if result.returncode:
                raise RuntimeError(
                    f"memory link failed ({result.returncode}): {result.stderr.strip()}"
                )
            size = os.fstat(output.fileno()).st_size
            if size > max_executable_bytes:
                raise RuntimeError(
                    "linked executable exceeds the configured byte limit"
                )
            output.seek(0)
            image = output.read(max_executable_bytes + 1)
    if not image.startswith(b"\x7fELF"):
        raise RuntimeError("linker did not produce an ELF executable")
    return image


def _memory_link_flag_allowed(flag: str) -> bool:
    """The first adapter accepts libraries, not output overrides or plugins."""
    if any(character in flag for character in "\x00\n\r"):
        return False
    return (
        flag == "-pthread"
        or re.fullmatch(r"-l[A-Za-z0-9_+.:\-]+", flag) is not None
        or (flag.startswith("-L") and len(flag) > 2)
    )


def publish_executable(image: bytes, destination: Path) -> None:
    """Publish only a completed image; keep the previous output on write failure."""
    if not image.startswith(b"\x7fELF"):
        raise ValueError("refusing to publish a non-ELF image")
    destination = Path(destination)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            staged = Path(output.name)
            output.write(image)
            output.flush()
            os.fchmod(output.fileno(), 0o700)
            os.fsync(output.fileno())
        os.replace(staged, destination)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)

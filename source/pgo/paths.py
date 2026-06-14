"""Shared PGO path helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def sanitize_stem(stem: str) -> str:
    """Return a filesystem-safe artifact stem."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return cleaned or "program"


def source_identity_tag(source_file: str | Path) -> str:
    """Stable short tag derived from absolute source path."""
    resolved = str(Path(source_file).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    return digest[:10]


def default_pgo_output_dir(source_file: str | Path, generated_root: Path) -> Path:
    """Default profile-data directory for a source file."""
    stem = sanitize_stem(Path(source_file).stem)
    tag = source_identity_tag(source_file)
    return generated_root / "pgo" / f"{stem}_{tag}"

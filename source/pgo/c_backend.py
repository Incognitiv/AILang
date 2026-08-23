"""C backend PGO flag helpers."""

from __future__ import annotations

from pathlib import Path


def c_pgo_compile_flags(
    *,
    pgo_generate_dir: str = "",
    pgo_use_dir: str = "",
) -> list[str]:
    """Return GCC/Clang C-backend PGO flags and create profile dirs."""
    if pgo_generate_dir and pgo_use_dir:
        raise ValueError("--pgo-generate and --pgo-use are mutually exclusive")
    if pgo_generate_dir:
        profile_dir = Path(pgo_generate_dir).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        return [f"-fprofile-generate={profile_dir}"]
    if pgo_use_dir:
        profile_dir = Path(pgo_use_dir).resolve()
        return [f"-fprofile-use={profile_dir}", "-Wno-missing-profile"]
    return []

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from c_strict_compile import (  # noqa: E402
    CORPUS_DIR,
    DEFAULT_PROGRAMS,
    _compile_generated_c,
    _resolve_compiler,
    _runtime_surface_sources,
    _strict_flags,
)


def test_freestanding_trivial_main_c23_object_is_warning_clean(tmp_path: Path) -> None:
    compiler = _resolve_compiler("auto")
    if compiler is None:
        pytest.skip("no C compiler available")

    source_file = tmp_path / "freestanding_probe.ail"
    source_file.write_text(
        "def main(): int\n" "    return 42\n" "end\n",
        encoding="utf-8",
    )

    result = _compile_generated_c(
        source_file,
        tmp_path,
        compiler,
        _strict_flags("c2x", freestanding=True),
        freestanding=True,
    )

    assert result.status == "pass", result.detail


def test_freestanding_default_corpus_c23_objects_are_warning_clean(
    tmp_path: Path,
) -> None:
    compiler = _resolve_compiler("auto")
    if compiler is None:
        pytest.skip("no C compiler available")

    strict_flags = _strict_flags("c2x", freestanding=True)
    failures: list[str] = []
    for name in DEFAULT_PROGRAMS:
        result = _compile_generated_c(
            CORPUS_DIR / f"{name}.ail",
            tmp_path,
            compiler,
            strict_flags,
            freestanding=True,
        )
        if result.status != "pass":
            failures.append(f"{name}: {result.detail}")

    assert not failures, "\n".join(failures)


def test_freestanding_runtime_surface_c23_objects_are_warning_clean(
    tmp_path: Path,
) -> None:
    compiler = _resolve_compiler("auto")
    if compiler is None:
        pytest.skip("no C compiler available")

    strict_flags = _strict_flags("c2x", freestanding=True)
    failures: list[str] = []
    for item in _runtime_surface_sources(tmp_path):
        if not isinstance(item, Path):
            failures.append(f"{item.name}: {item.detail}")
            continue
        result = _compile_generated_c(
            item,
            tmp_path,
            compiler,
            strict_flags,
            freestanding=True,
        )
        if result.status != "pass":
            failures.append(f"{result.name}: {result.detail}")

    assert not failures, "\n".join(failures)

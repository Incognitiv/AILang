from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cli.compilation import (  # noqa: E402
    _intermediate_artifact_path,
    default_emit_llvm_output_path,
)


def test_default_emit_llvm_output_goes_to_out_generated() -> None:
    src = REPO_ROOT / "benchmarks" / "ailang" / "fib_mix.ail"
    out = default_emit_llvm_output_path(str(src))
    assert out.suffix == ".ll"
    assert "out/generated/emit_llvm/" in out.as_posix()
    assert out.parent != src.parent


def test_intermediate_paths_are_stable_for_same_source() -> None:
    src = REPO_ROOT / "benchmarks" / "ailang" / "loop_hash.ail"
    p1 = _intermediate_artifact_path(str(src), stage="llvm_aot", suffix=".ll")
    p2 = _intermediate_artifact_path(str(src), stage="llvm_aot", suffix=".ll")
    assert p1 == p2


def test_stage_partitioning_changes_output_subdirectory() -> None:
    src = REPO_ROOT / "benchmarks" / "ailang" / "dict_ops.ail"
    c_path = _intermediate_artifact_path(str(src), stage="c_backend", suffix=".c")
    ll_path = _intermediate_artifact_path(str(src), stage="llvm_aot", suffix=".ll")
    assert c_path != ll_path
    assert "out/generated/c_backend/" in c_path.as_posix()
    assert "out/generated/llvm_aot/" in ll_path.as_posix()

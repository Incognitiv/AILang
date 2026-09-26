"""Real LLVM emission, native memory linking, and atomic publication probes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler.memory_link import (
    link_object_in_memory,
    memory_link_supported,
    publish_executable,
    validate_output_paths,
)
from compiler.memory_native import compile_ir_object

LINKER = shutil.which("clang") or shutil.which("gcc")
pytestmark = pytest.mark.skipif(
    not memory_link_supported() or LINKER is None,
    reason="Linux memfd and a native linker driver are required",
)


def object_for(value: int, opt: int = 3) -> bytes:
    return compile_ir_object(f"define i32 @main() {{ ret i32 {value} }}", opt)


def run_image(image: bytes, path: Path) -> int:
    publish_executable(image, path)
    return subprocess.run([str(path)], check=False, timeout=10).returncode


@pytest.mark.parametrize("opt", [0, 1, 2, 3])
def test_native_pipeline_returns_expected_exit(tmp_path: Path, opt: int) -> None:
    image = link_object_in_memory(object_for(42, opt), LINKER)
    assert image.startswith(b"\x7fELF")
    assert run_image(image, tmp_path / "answer") == 42
    assert sorted(path.name for path in tmp_path.iterdir()) == ["answer"]


def test_link_step_uses_only_private_memory_descriptors(tmp_path: Path) -> None:
    obj = object_for(17)
    with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("disk temp")):
        with patch("builtins.open", side_effect=AssertionError("named file")):
            image = link_object_in_memory(obj, LINKER)
    assert run_image(image, tmp_path / "answer") == 17


def test_invalid_ir_is_rejected_before_object_emission() -> None:
    with pytest.raises(RuntimeError):
        compile_ir_object("not valid LLVM IR")


@pytest.mark.parametrize("opt", [-1, 4])
def test_invalid_optimization_level_fails(opt: int) -> None:
    with pytest.raises(ValueError):
        object_for(0, opt)


def test_missing_linker_fails_closed() -> None:
    with pytest.raises(FileNotFoundError):
        link_object_in_memory(object_for(0), "ailang-missing-linker-driver")


def test_non_object_input_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="memory link failed"):
        link_object_in_memory(b"not an object", LINKER)


def test_output_limit_is_enforced() -> None:
    with pytest.raises(RuntimeError, match="byte limit"):
        link_object_in_memory(object_for(0), LINKER, max_executable_bytes=1)


def test_link_failure_closes_descriptors() -> None:
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            link_object_in_memory(b"invalid object", LINKER)
    assert len(os.listdir("/proc/self/fd")) == before


def test_parallel_links_do_not_share_intermediates(tmp_path: Path) -> None:
    objects = [object_for(17), object_for(29)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        images = list(pool.map(lambda obj: link_object_in_memory(obj, LINKER), objects))
    assert run_image(images[0], tmp_path / "first") == 17
    assert run_image(images[1], tmp_path / "second") == 29


def test_invalid_image_does_not_replace_previous_output(tmp_path: Path) -> None:
    path = tmp_path / "program"
    path.write_bytes(b"previous")
    with pytest.raises(ValueError):
        publish_executable(b"invalid", path)
    assert path.read_bytes() == b"previous"
    assert len(list(tmp_path.iterdir())) == 1


def test_publish_failure_preserves_previous_output(tmp_path: Path) -> None:
    path = tmp_path / "program"
    path.write_bytes(b"previous")
    image = link_object_in_memory(object_for(0), LINKER)
    with patch("os.replace", side_effect=OSError("injected publish failure")):
        with pytest.raises(OSError):
            publish_executable(image, path)
    assert path.read_bytes() == b"previous"
    assert len(list(tmp_path.iterdir())) == 1


def test_source_cli_integration(tmp_path: Path) -> None:
    """CI runs this against the real AILang frontend and import graph."""
    source = tmp_path / "program.ail"
    source.write_text('void main():\n    print("memory-native-ok")\nend\n', encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "program"
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "memory_build.py"), str(source),
         "-o", str(output), "--linker", LINKER],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run = subprocess.run([str(output)], capture_output=True, text=True,
                         timeout=10, check=False)
    assert run.returncode == 0
    assert run.stdout.strip() == "memory-native-ok"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["program", "program.ail"]


@pytest.mark.parametrize("flag", ["-o/tmp/ailang-bad", "-Wl,-Map,/tmp/map",
                                 "@options.txt", "-fplugin=tool.so", "-lbad\nflag"])
def test_output_overrides_and_side_outputs_are_rejected(flag: str) -> None:
    with pytest.raises(ValueError, match="unsupported memory-link option"):
        link_object_in_memory(object_for(0), LINKER, (flag,))


def test_library_flags_are_supported(tmp_path: Path) -> None:
    image = link_object_in_memory(object_for(0), LINKER, ("-lm", "-pthread"))
    assert run_image(image, tmp_path / "answer") == 0


def test_foreign_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="native target"):
        compile_ir_object('target triple = "aarch64-unknown-linux-gnu"\n'
                          'define i32 @main() { ret i32 0 }')


@pytest.mark.parametrize("diagnostic", ["source.ail", "program"])
def test_diagnostic_cannot_clobber_inputs_or_output(tmp_path: Path, diagnostic: str) -> None:
    with pytest.raises(ValueError, match="diagnostic"):
        validate_output_paths(tmp_path / "source.ail", tmp_path / "program", tmp_path / diagnostic)


def test_output_cannot_overwrite_main_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source file"):
        validate_output_paths(tmp_path / "source.ail", tmp_path / "source.ail")


def test_distinct_output_paths_are_accepted(tmp_path: Path) -> None:
    validate_output_paths(tmp_path / "source.ail", tmp_path / "program", tmp_path / "failed.ll")


def test_timeout_closes_memory_descriptors() -> None:
    before = len(os.listdir("/proc/self/fd"))
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("linker", 1)):
        with pytest.raises(subprocess.TimeoutExpired):
            link_object_in_memory(b"unused object", LINKER)
    assert len(os.listdir("/proc/self/fd")) == before


def test_tuning_options_implements_the_declared_resource_protocol() -> None:
    from llvmlite import binding

    tuning = binding.PipelineTuningOptions(speed_level=1)
    with tuning as entered:
        assert entered is tuning
        assert not tuning.closed
    assert tuning.closed

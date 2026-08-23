from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

AILANG = REPO_ROOT / "ailang.py"

from cli.compilation import _extract_ailang_link_flags  # noqa: E402
from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from target_info import os_from_platform  # noqa: E402


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def _compile_helper_object(tmp_path: Path) -> Path:
    cc = _available_c_compiler()
    if cc is None:
        pytest.skip("no C compiler available")
    helper_c = tmp_path / "linked_answer.c"
    helper_o = tmp_path / "linked_answer.o"
    helper_c.write_text(
        """\
#include <stdint.h>

int64_t linked_answer(void) {
    return 42;
}
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [cc, "-c", str(helper_c), "-o", str(helper_o)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        proc.returncode == 0
    ), f"helper object compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    return helper_o


def _write_linked_program(tmp_path: Path, helper_o: Path) -> Path:
    src = tmp_path / "uses_link_directive.ail"
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

extern fn linked_answer(): int

def main(): int
    print(linked_answer())
    return 0
end
""",
        encoding="utf-8",
    )
    return src


def _compile_and_run(src: Path, out_stem: Path, *, backend: str) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            f"--backend={backend}",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if backend == "llvm" and proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
    assert (
        proc.returncode == 0
    ), f"{backend} compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

    run_proc = subprocess.run(
        [str(_native_exe(out_stem))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        run_proc.returncode == 0
    ), f"{backend} run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    return run_proc.stdout.strip()


def test_extract_ailang_link_flags_from_source_c_and_llvm_comments() -> None:
    text = """\
#link "-luser32 -lgdi32"
/* AILANG_LINK: helper.o -lfoo */
; AILANG_LINK: -framework Cocoa
"""
    assert _extract_ailang_link_flags(text) == [
        "-luser32",
        "-lgdi32",
        "helper.o",
        "-lfoo",
        "-framework",
        "Cocoa",
    ]


def test_extract_ailang_link_flags_filters_targeted_source_directives() -> None:
    text = """\
#link windows "win_object.o"
#link linux "linux_object.o"
"""
    assert _extract_ailang_link_flags(text, target_os="windows") == ["win_object.o"]
    assert _extract_ailang_link_flags(text, target_os="linux") == ["linux_object.o"]


def test_c_backend_consumes_link_directive_object(tmp_path: Path) -> None:
    helper_o = _compile_helper_object(tmp_path)
    src = _write_linked_program(tmp_path, helper_o)
    assert _compile_and_run(src, tmp_path / "linked_c", backend="c") == "42"


def test_llvm_backend_consumes_link_directive_object(tmp_path: Path) -> None:
    helper_o = _compile_helper_object(tmp_path)
    src = _write_linked_program(tmp_path, helper_o)
    assert _compile_and_run(src, tmp_path / "linked_llvm", backend="llvm") == "42"


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_targeted_link_directive_filters_inactive_object(
    tmp_path: Path, backend: str
) -> None:
    helper_o = _compile_helper_object(tmp_path)
    current_os = os_from_platform()
    src = tmp_path / "uses_targeted_link_directive.ail"
    src.write_text(
        f"""\
#link {current_os} "{helper_o.as_posix()}"
#link never "missing_inactive_object.o"

extern fn linked_answer(): int

def main(): int
    print(linked_answer())
    return 0
end
""",
        encoding="utf-8",
    )
    assert _compile_and_run(src, tmp_path / f"targeted_link_{backend}", backend=backend) == "42"


def test_llvm_ir_preserves_imported_link_directives(tmp_path: Path) -> None:
    main_src = tmp_path / "main.ail"
    imported_src = tmp_path / "native_stub.ail"
    main_src.write_text(
        """\
import native_stub

def main(): int
    return helper()
end
""",
        encoding="utf-8",
    )
    imported_src.write_text(
        """\
#link "-lnative_stub"

def helper(): int
    return 1
end
""",
        encoding="utf-8",
    )
    ir_text = compile_to_ir_fast(
        main_src.read_text(encoding="utf-8"), source_file=str(main_src)
    )
    assert "; AILANG_LINK: -lnative_stub" in ir_text

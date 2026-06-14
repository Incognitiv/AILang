from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _native_exe(stem: Path) -> Path:
    return stem.with_suffix(".exe") if os.name == "nt" else stem


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def _compile_helper_object(tmp_path: Path) -> Path:
    cc = _available_c_compiler()
    if cc is None:
        pytest.skip("no C compiler available")
    helper_c = tmp_path / "native_handle.c"
    helper_o = tmp_path / "native_handle.o"
    helper_c.write_text(
        """\
#include <stdint.h>

typedef struct NativeHandle NativeHandle;
struct NativeHandle {
    int64_t value;
};

static NativeHandle g_handle = {41};

NativeHandle *make_handle(void) {
    return &g_handle;
}

int64_t handle_value(NativeHandle *h) {
    return h->value + 1;
}

int64_t handle_is_same(NativeHandle *h) {
    return h == &g_handle;
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


def _write_program(tmp_path: Path, helper_o: Path, record_decl: str) -> Path:
    src = tmp_path / (
        "opaque_handle.ail" if record_decl.startswith("opaque") else "extern_handle.ail"
    )
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

{record_decl}

extern fn make_handle(): NativeHandle
extern fn handle_value(h: NativeHandle): int
extern fn handle_is_same(h: NativeHandle): int

def main(): int
    NativeHandle h = make_handle()
    if handle_value(h) != 42 then
        return 1
    end
    if handle_is_same(h) != 1 then
        return 2
    end
    if sizeof("NativeHandle") != sizeof("ptr") then
        return 3
    end
    return 0
end
""",
        encoding="utf-8",
    )
    return src


def _compile_and_run(src: Path, out_stem: Path, *, backend: str) -> None:
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
    if proc.returncode != 0:
        msg = (proc.stdout + "\n" + proc.stderr).lower()
        if backend == "llvm" and (
            "llvm toolchain" in msg
            or "clang not found" in msg
            or "llc not found" in msg
        ):
            pytest.skip("LLVM native toolchain unavailable")
        if backend == "c" and "no c compiler found" in msg:
            pytest.skip("C compiler unavailable")
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


def _run_check(src: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("backend", ["c", "llvm"])
@pytest.mark.parametrize(
    "record_decl", ["opaque record NativeHandle", "extern record NativeHandle"]
)
def test_opaque_and_extern_records_are_pointer_only_native_handles(
    tmp_path: Path, backend: str, record_decl: str
) -> None:
    helper_o = _compile_helper_object(tmp_path)
    src = _write_program(tmp_path, helper_o, record_decl)

    check_proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        check_proc.returncode == 0
    ), f"check failed\nstdout:\n{check_proc.stdout}\n\nstderr:\n{check_proc.stderr}"

    suffix = "opaque" if record_decl.startswith("opaque") else "extern"
    _compile_and_run(src, tmp_path / f"{suffix}_record_{backend}", backend=backend)


@pytest.mark.parametrize("owner_decl", ["record Holder", "union Holder"])
@pytest.mark.parametrize(
    "record_decl", ["opaque record NativeHandle", "extern record NativeHandle"]
)
def test_layoutless_c_records_cannot_be_embedded_by_value(
    tmp_path: Path, owner_decl: str, record_decl: str
) -> None:
    src = tmp_path / "bad_opaque_field.ail"
    src.write_text(
        f"""\
{record_decl}

{owner_decl} then
    NativeHandle handle
end

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = _run_check(src)
    assert proc.returncode != 0
    output = proc.stdout + proc.stderr
    assert "no known by-value layout" in output
    assert "NativeHandle" in output


def test_extern_record_with_imported_layout_can_be_embedded_by_value(
    tmp_path: Path,
) -> None:
    src = tmp_path / "layout_field.ail"
    src.write_text(
        """\
extern record NativeHandle = "struct NativeHandle" layout size 8 align 8 then
    value offset 0 size 8
end

record Holder then
    NativeHandle handle
end

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = _run_check(src)
    assert (
        proc.returncode == 0
    ), f"check failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"

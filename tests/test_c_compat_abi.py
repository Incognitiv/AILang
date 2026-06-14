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


def _compile_c_object(tmp_path: Path, name: str, source: str) -> Path:
    cc = _available_c_compiler()
    if cc is None:
        pytest.skip("no C compiler available")

    c_path = tmp_path / f"{name}.c"
    obj_path = tmp_path / f"{name}.o"
    c_path.write_text(source, encoding="utf-8")

    proc = subprocess.run(
        [cc, "-c", str(c_path), "-o", str(obj_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        proc.returncode == 0
    ), f"C helper compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    return obj_path


def _compile_and_run(
    src: Path, out_stem: Path, *, backend: str
) -> subprocess.CompletedProcess[str]:
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
    return run_proc


def test_c_backend_defined_stdcall_and_fastcall_exports_compile_and_run(
    tmp_path: Path,
) -> None:
    src = tmp_path / "defined_callconv.ail"
    src.write_text(
        """\
@stdcall
@export
def callback_answer(x: int): int
    return x + 1
end

@fastcall
@export
def fast_answer(x: int): int
    return x + 2
end

def main(): int
    if callback_answer(41) != 42 then
        return 1
    end
    if fast_answer(40) != 42 then
        return 2
    end
    return 0
end
""",
        encoding="utf-8",
    )
    _compile_and_run(src, tmp_path / "defined_callconv_c", backend="c")


def test_windows_fastcall_callback_round_trips_through_native_object(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only live fastcall callback smoke")

    helper_o = _compile_c_object(
        tmp_path,
        "native_fastcall_callback",
        """\
#include <stdint.h>

#if defined(_MSC_VER)
#define NATIVE_FASTCALL __fastcall
#elif defined(__GNUC__) && (defined(__i386__) || defined(_M_IX86))
#define NATIVE_FASTCALL __attribute__((fastcall))
#else
#define NATIVE_FASTCALL
#endif

typedef int64_t (NATIVE_FASTCALL *FastAnswerCb)(int64_t, int64_t);

int64_t invoke_fast_answer(FastAnswerCb cb) {
    return cb(19, 23);
}
""",
    )
    src = tmp_path / "fastcall_callback.ail"
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

type FastAnswerCb = fn(left: int, right: int): int @fastcall

extern fn invoke_fast_answer(cb: FastAnswerCb): int

@fastcall
def fast_answer(left: int, right: int): int
    return left + right
end

def main(): int
    FastAnswerCb cb = fn_ptr("fast_answer", "FastAnswerCb")
    if fn_call(cb, "FastAnswerCb", 20, 22) != 42 then
        return 1
    end
    if cb(18, 24) != 42 then
        return 2
    end
    if invoke_fast_answer(cb) != 42 then
        return 3
    end
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / "fastcall_callback_c", backend="c")


def test_llvm_defined_stdcall_and_fastcall_exports_use_callconv(
    tmp_path: Path,
) -> None:
    src = tmp_path / "defined_callconv_ir.ail"
    out_ll = tmp_path / "defined_callconv_ir.ll"
    src.write_text(
        """\
@stdcall
@export
def callback_answer(x: int): int
    return x + 1
end

@fastcall
@export
def fast_answer(x: int): int
    return x + 2
end

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-llvm", "-o", str(out_ll)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    text = out_ll.read_text(encoding="utf-8")
    assert "x86_stdcallcc" in text
    assert "x86_fastcallcc" in text


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_extern_var_and_variadic_extern_link_through_object(
    tmp_path: Path, backend: str
) -> None:
    helper_o = _compile_c_object(
        tmp_path,
        "native_symbols",
        """\
#include <stdarg.h>
#include <stdint.h>

int64_t native_counter = 1234;

int64_t native_counter_plus(int64_t delta) {
    return native_counter + delta;
}

int64_t native_sum3(int64_t first, ...) {
    va_list ap;
    va_start(ap, first);
    int64_t second = va_arg(ap, int64_t);
    int64_t third = va_arg(ap, int64_t);
    va_end(ap);
    return first + second + third;
}
""",
    )
    src = tmp_path / "extern_var_varargs.ail"
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

extern var native_counter: int
extern fn native_counter_plus(delta: int): int
extern fn native_sum3(first: int, ...): int

def main(): int
    if native_counter != 1234 then
        return 10
    end
    native_counter = native_counter + 1
    if native_counter_plus(0) != 1235 then
        return 11
    end
    if native_sum3(10, 20, 12) != 42 then
        return 12
    end
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / f"extern_var_varargs_{backend}", backend=backend)


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_ptrptr_extern_output_pointer_round_trips_through_native_object(
    tmp_path: Path, backend: str
) -> None:
    helper_o = _compile_c_object(
        tmp_path,
        "native_ptrptr",
        """\
#include <stdint.h>

static int64_t native_cell = 0;

int64_t native_write_output_pointer(void **slot, int64_t value) {
    native_cell = value;
    *slot = &native_cell;
    return *(int64_t *)(*slot);
}

int64_t native_read_pointer(void *ptr) {
    return *(int64_t *)ptr;
}
""",
    )
    src = tmp_path / "ptrptr_output.ail"
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

extern fn native_write_output_pointer(slot: ptrptr, value: int): int
extern fn native_read_pointer(raw: ptr): int

def main(): int
    slot = alloc(8)
    poke64(slot, 0, 0)
    if native_write_output_pointer(reinterpret(ptrptr, slot), 42) != 42 then
        dealloc(slot)
        return 1
    end
    out = peek64(slot, 0)
    if out == 0 then
        dealloc(slot)
        return 2
    end
    if native_read_pointer(reinterpret(ptr, out)) != 42 then
        dealloc(slot)
        return 3
    end
    dealloc(slot)
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / f"ptrptr_output_{backend}", backend=backend)


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_typed_callback_alias_round_trips_through_native_object(
    tmp_path: Path, backend: str
) -> None:
    helper_o = _compile_c_object(
        tmp_path,
        "native_callback",
        """\
#include <stdint.h>

typedef int64_t (*AnswerCb)(int64_t);

int64_t invoke_answer(AnswerCb cb, int64_t value) {
    return cb(value) + 1;
}
""",
    )
    src = tmp_path / "typed_callback.ail"
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

type AnswerCb = fn(value: int): int

extern fn invoke_answer(cb: AnswerCb, value: int): int

def callback_answer(value: int): int
    return value + 1
end

def main(): int
    AnswerCb cb = fn_ptr("callback_answer", "AnswerCb")
    if fn_call(cb, "AnswerCb", 40) != 41 then
        return 1
    end
    if invoke_answer(cb, 40) != 42 then
        return 2
    end
    if cb(40) != 41 then
        return 3
    end
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / f"typed_callback_{backend}", backend=backend)


@pytest.mark.parametrize("backend", ["c", "llvm"])
def test_imported_record_layout_metadata_and_pointer_calls(
    tmp_path: Path, backend: str
) -> None:
    helper_o = _compile_c_object(
        tmp_path,
        "native_imported_record",
        """\
#include <stdint.h>

struct NativeImported {
    int64_t value;
};

static struct NativeImported g_imported = {41};

struct NativeImported *native_imported_make(void) {
    return &g_imported;
}

int64_t native_imported_read(struct NativeImported *item) {
    return item->value + 1;
}
""",
    )
    src = tmp_path / "imported_record_layout.ail"
    src.write_text(
        f"""\
#link "{helper_o.as_posix()}"

extern record NativeImported = "struct NativeImported" layout size 8 align 8 then
    value offset 0 size 8
end

extern fn native_imported_make(): NativeImported
extern fn native_imported_read(item: NativeImported): int

def main(): int
    if sizeof("NativeImported") != 8 then
        return 10
    end
    if alignof("NativeImported") != 8 then
        return 11
    end
    if offsetof("NativeImported", "value") != 0 then
        return 12
    end
    NativeImported item = native_imported_make()
    if native_imported_read(item) != 42 then
        return 13
    end
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(
        src, tmp_path / f"imported_record_layout_{backend}", backend=backend
    )


def test_c_backend_record_union_packed_and_fixed_array_abi_layout(
    tmp_path: Path,
) -> None:
    src = tmp_path / "abi_layout.ail"
    src.write_text(
        """\
#template ansi_c
#include <stdint.h>
#include <stddef.h>

typedef struct {
    uint8_t tag;
    int64_t value;
    uint8_t data[3];
} NativePacketRef;

typedef struct __attribute__((packed)) {
    uint8_t tag;
    int64_t value;
} PackedMiniRef;

typedef union {
    int64_t whole;
    uint8_t bytes[8];
} NativeWordRef;

int64_t ref_packet_size(void) { return (int64_t)sizeof(NativePacketRef); }
int64_t ref_packet_align(void) { return (int64_t)_Alignof(NativePacketRef); }
int64_t ref_packed_size(void) { return (int64_t)sizeof(PackedMiniRef); }
int64_t ref_packed_align(void) { return (int64_t)_Alignof(PackedMiniRef); }
int64_t ref_word_size(void) { return (int64_t)sizeof(NativeWordRef); }
int64_t ref_word_align(void) { return (int64_t)_Alignof(NativeWordRef); }
#end

record NativePacket then
    byte tag
    int value
    [byte;3] data
end

@packed
record PackedMini then
    byte tag
    int value
end

union NativeWord then
    int whole
    [byte;8] bytes
end

extern fn ref_packet_size(): int
extern fn ref_packet_align(): int
extern fn ref_packed_size(): int
extern fn ref_packed_align(): int
extern fn ref_word_size(): int
extern fn ref_word_align(): int

def main(): int
    if sizeof("NativePacket") != ref_packet_size() then
        return 1
    end
    if alignof("NativePacket") != ref_packet_align() then
        return 2
    end
    if sizeof("PackedMini") != ref_packed_size() then
        return 3
    end
    if alignof("PackedMini") != ref_packed_align() then
        return 4
    end
    if sizeof("NativeWord") != ref_word_size() then
        return 5
    end
    if alignof("NativeWord") != ref_word_align() then
        return 6
    end
    return 0
end
""",
        encoding="utf-8",
    )

    _compile_and_run(src, tmp_path / "abi_layout_c", backend="c")

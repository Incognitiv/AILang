from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def test_emit_header_writes_c_abi_surface(tmp_path: Path) -> None:
    src = tmp_path / "native_api.ail"
    header = tmp_path / "native_api.h"
    src.write_text(
        """\
#cinclude <stddef.h>
#cinclude <stdio.h>
typedef [byte; 4] Hash4
opaque record NativeHandle
extern record NativeFile = "FILE"
type NativeCallback = fn(value: int): int @stdcall

@packed
record NativePacket then
    byte tag
    int value
    Hash4 digest
end

union NativeWord then
    int whole
    [byte;8] bytes
end

@stdcall
@export
def exported_answer(packet: NativePacket, code: int): int
    return code + packet.value
end

@export
def handle_status(handle: NativeHandle): int
    return 0
end

@export
def file_status(handle: NativeFile): int
    return 0
end

@export
def call_status(cb: NativeCallback, value: int): int
    return 0
end

def hidden_helper(): int
    return 7
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(header)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = header.read_text(encoding="utf-8")
    assert "#include <stddef.h>" in text
    assert "#include <stdio.h>" in text
    assert "typedef struct NativeHandle NativeHandle;" in text
    assert "typedef FILE NativeFile;" in text
    assert "typedef int64_t (AILANG_STDCALL *NativeCallback)(int64_t value);" in text
    assert "typedef struct {" in text
    assert "uint8_t tag;" in text
    assert "int64_t value;" in text
    assert "uint8_t digest[4];" in text
    assert "} AILANG_PACKED NativePacket;" in text
    assert "typedef union {" in text
    assert "uint8_t bytes[8];" in text
    assert "#define AILANG_STDCALL" in text
    assert (
        "int64_t AILANG_STDCALL exported_answer(NativePacket packet, int64_t code);"
        in text
    )
    assert "int64_t handle_status(NativeHandle * handle);" in text
    assert "int64_t file_status(NativeFile * handle);" in text
    assert "int64_t call_status(NativeCallback cb, int64_t value);" in text
    assert "hidden_helper" not in text


def test_emit_header_output_compiles_as_c_header(tmp_path: Path) -> None:
    cc = _available_c_compiler()
    if cc is None:
        pytest.skip("no C compiler available")

    src = tmp_path / "native_api.ail"
    header = tmp_path / "native_api.h"
    consumer = tmp_path / "consumer.c"
    obj = tmp_path / "consumer.o"
    src.write_text(
        """\
record NativePacket then
    byte tag
    int value
end

@export
def exported_answer(packet: NativePacket): int
    return packet.value
end
""",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(header)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    consumer.write_text(
        """\
#include "native_api.h"

int64_t exported_answer(NativePacket packet) {
    return packet.value;
}
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [cc, "-std=gnu23", "-c", str(consumer), "-o", str(obj)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_emit_header_preserves_explicit_abi_width_types(tmp_path: Path) -> None:
    src = tmp_path / "abi_widths.ail"
    header = tmp_path / "abi_widths.h"
    src.write_text(
        """\
@export("abi_write")
def libc_write(fd: i32, buf: ptr, count: u64): i64
    return 0
end

@export("abi_close")
def libc_close(fd: i32): i32
    return 0
end

@export("abi_uid")
def libc_uid(): u32
    return 0
end

@export("abi_short_probe")
def libc_short_probe(value: i16): u16
    return value
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-header", "-o", str(header)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = header.read_text(encoding="utf-8")
    assert "int64_t abi_write(int32_t fd, void * buf, uint64_t count);" in text
    assert "int32_t abi_close(int32_t fd);" in text
    assert "uint32_t abi_uid(void);" in text
    assert "uint16_t abi_short_probe(int16_t value);" in text

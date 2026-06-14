from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_ffi_report_json_lists_native_surface(tmp_path: Path) -> None:
    src = tmp_path / "ffi_surface.ail"
    src.write_text(
        """\
#cinclude <stdint.h>
#link "-lm native_helper.o"

extern var native_counter: int
@stdcall
extern fn native_tick(hwnd: ptr, msg: int, ...): int

opaque record NativeHandle
extern record ImportedWindow = "struct ImportedWindow" layout size 16 align 8 then
    hwnd offset 0 size 8
    id offset 8 size 8
end
type NativeCallback = fn(value: int): int @stdcall

@packed
record NativePacket then
    byte tag
    int value
end

union NativeWord then
    int whole
    [byte;8] bytes
end

@export
def exported_answer(): int
    return 42
end

@export
def malloc(): int
    return 7
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--ffi-report-json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    payload = json.loads(proc.stdout)
    assert payload["target_os"]
    assert payload["cinclude_backend"] == "c_backend_only"
    assert payload["cinclude_support"]["c_backend"]["status"] == "emitted"
    assert payload["unsupported_cinclude_backends"] == ["llvm", "jit"]
    assert "LLVM/JIT cannot import C headers directly" in payload["backend_warnings"][0]
    assert payload["include_count"] == 1
    assert payload["active_include_count"] == 1
    assert payload["inactive_include_count"] == 0
    include = payload["includes"][0]
    assert include["path"] == "stdint.h"
    assert include["system"] is True
    assert include["target_os"] is None
    assert include["active"] is True
    assert include["spelling"] == "<stdint.h>"
    assert include["backend_support"]["llvm_aot"] == "ignored_header_import"
    assert payload["cinclude_diagnostics"] == []
    assert payload["links"] == ["-lm native_helper.o"]
    assert payload["active_link_count"] == 1
    link = payload["link_directives"][0]
    assert link["flags"] == "-lm native_helper.o"
    assert link["target_os"] is None
    assert link["active"] is True
    assert payload["extern_vars"][0]["name"] == "native_counter"
    assert payload["opaque_record_count"] == 2
    opaque_records = {row["name"]: row for row in payload["opaque_records"]}
    assert opaque_records["NativeHandle"]["opaque"] is True
    assert opaque_records["ImportedWindow"]["c_name"] == "struct ImportedWindow"
    assert opaque_records["ImportedWindow"]["layout"] == {
        "size": 16,
        "align": 8,
        "fields": {
            "hwnd": {"offset": 0, "size": 8},
            "id": {"offset": 8, "size": 8},
        },
    }
    assert payload["callback_alias_count"] == 1
    callback_aliases = {row["name"]: row for row in payload["callback_aliases"]}
    assert callback_aliases["NativeCallback"]["decorators"] == ["stdcall"]
    assert callback_aliases["NativeCallback"]["params"] == [
        {"name": "value", "type": "i64"}
    ]

    extern = payload["extern_functions"][0]
    assert extern["name"] == "native_tick"
    assert extern["variadic"] is True
    assert extern["decorators"] == ["stdcall"]

    records = {row["name"]: row for row in payload["records"]}
    assert records["NativePacket"]["decorators"] == ["packed"]

    unions = {row["name"]: row for row in payload["unions"]}
    assert unions["NativeWord"]["fields"][1] == {"name": "bytes", "type": "[u8;8]"}

    exports = {row["name"]: row for row in payload["exports"]}
    assert exports["exported_answer"]["c_symbol"] == "exported_answer"
    assert exports["malloc"]["c_symbol"] == "ailang_malloc"
    generated = {row["name"]: row for row in payload["generated_c_symbols"]}
    assert generated["malloc"]["c_symbol"] == "ailang_malloc"

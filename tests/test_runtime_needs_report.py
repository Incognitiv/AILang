from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser  # noqa: E402

from lexer.scan import tokenize  # noqa: E402
from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402

AILANG = REPO_ROOT / "ailang.py"


def _run_runtime_needs(src: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "case.ail"
        src_path.write_text(src, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(AILANG), str(src_path), "--runtime-needs-json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)


def _to_c_with_needs(src: str) -> tuple[str, object]:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    transpiler = CTranspiler()
    c_code = transpiler.transpile(ast, "<inline>")
    return c_code, transpiler.runtime_needs


def test_runtime_needs_json_shape_and_safe_arith_signal() -> None:
    src = """
def add(a, b): int
    return a + b
end

def main(n: int): int
    return add(n, 2)
end
"""
    payload = _run_runtime_needs(src)
    families = payload.get("families", {})
    assert isinstance(families, dict)
    for key in (
        "string",
        "array",
        "dict",
        "file",
        "sqlite",
        "thread",
        "socket",
        "safe_arith",
        "leak_tracker",
    ):
        assert key in families
    assert bool(families.get("safe_arith")) is True
    assert bool(families.get("sqlite")) is False
    assert int(payload.get("generated_c_bytes", 0)) > 0


def test_runtime_needs_detects_sqlite_family() -> None:
    src = """
def main(): int
    db = sql_open("x.db")
    if db == 0 then
        return 1
    end
    sql_close(db)
    return 0
end
"""
    payload = _run_runtime_needs(src)
    families = payload.get("families", {})
    helpers = set(payload.get("helpers", []))
    assert bool(families.get("sqlite")) is True
    assert "sqlite" in helpers


def test_numeric_program_prunes_unneeded_helper_families_in_c_output() -> None:
    src = """
def main(): int
    int x = 0
    while x < 10 then
        x = x + 1
    end
    return x
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "/* SQLite FFI helpers */" not in c_code
    assert "/* TCP socket runtime helpers */" not in c_code
    assert "dict_create_fn" not in c_code
    assert "sqlite" not in needs.helpers
    assert "sockets" not in needs.helpers
    assert "dict" not in needs.helpers


def test_tcp_connect_emits_socket_runtime_family() -> None:
    src = """
def main(): int
    sock = tcp_connect("127.0.0.1", 9)
    if sock != 0 then
        tcp_close(sock)
    end
    return 0
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "/* TCP socket runtime helpers */" in c_code
    assert "ailang_tcp_connect" in c_code
    assert "sockets" in needs.helpers


def test_llvm_tcp_connect_lowers_to_socket_connect_path() -> None:
    src = """
def main(): int
    sock = tcp_connect("127.0.0.1", 9)
    if sock != 0 then
        tcp_close(sock)
    end
    return 0
end
"""
    ir_text = compile_to_ir_fast(src, source_file="tcp_connect_probe.ail")
    assert '@"socket"' in ir_text
    assert '@"getaddrinfo"' in ir_text
    assert '@"freeaddrinfo"' in ir_text
    assert '@"connect"' in ir_text
    assert '@"setsockopt"' in ir_text
    assert "tcp_connect_handle" in ir_text


def test_win32_dynamic_binding_emits_pointer_sized_c_runtime() -> None:
    src = """
def main(): int
    h = win32_load_library("kernel32.dll")
    p = win32_get_proc_address(h, "GetTickCount")
    wide_ptr = win32_utf16_from_utf8("RVS-FreeBSD")
    win32_local_free(0)
    if wide_ptr != 0 then
        dealloc(wide_ptr)
    end
    if h != 0 then
        win32_free_library(h)
    end
    return (p != 0) + (wide_ptr >= 0)
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "ailang_win32_load_library" in c_code
    assert "LoadLibraryA" in c_code
    assert "GetProcAddress((HMODULE)(uintptr_t)module, name)" in c_code
    assert "FreeLibrary((HMODULE)(uintptr_t)module)" in c_code
    assert "MultiByteToWideChar" in c_code
    assert "LocalFree((HLOCAL)(uintptr_t)ptr)" in c_code
    assert "ailang_win32_call_" not in c_code
    assert "win32" in needs.helpers


def test_win32_typed_shell_execute_runas_replaces_dynamic_call_surface() -> None:
    src = """
def main(): int
    rc = win32_shell_execute_runas("rvs.exe", "--json")
    return rc
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "ailang_win32_shell_execute_runas" in c_code
    assert "ShellExecuteW" in c_code
    assert "ailang_win32_call_" not in c_code
    assert "win32" in needs.helpers

    ir_text = compile_to_ir_fast(src, source_file="win32_shell_execute_probe.ail")
    if "windows" in ir_text.lower():
        assert '@"LoadLibraryA"' in ir_text
        assert "ShellExecuteW" in ir_text


def test_win32_full_path_is_owned_string_builtin_on_c_and_llvm() -> None:
    src = """
def main(): int
    p = win32_full_path("out/rvs/root.vhdx")
    n = strlen(p)
    dealloc(p)
    return n
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "ailang_win32_full_path" in c_code
    assert "GetFullPathNameA" in c_code
    assert "win32" in needs.helpers

    ir_text = compile_to_ir_fast(src, source_file="win32_full_path_probe.ail")
    if "windows" in ir_text.lower():
        assert '@"GetFullPathNameA"' in ir_text


def test_comptime_if_body_still_registers_runtime_helpers() -> None:
    src = """
def main(): int
    int h = 0
    comptime if target_os() == "windows" then
        h = win32_load_library("kernel32.dll")
    end
    if h != 0 then
        win32_free_library(h)
    end
    return 0
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "win32" in needs.helpers
    assert "ailang_win32_load_library" in c_code
    assert "ailang_win32_free_library" in c_code


def test_llvm_win32_dynamic_binding_uses_pointer_handles_or_stubs() -> None:
    src = """
def main(): int
    h = win32_load_library("kernel32.dll")
    p = win32_get_proc_address(h, "GetTickCount")
    wide_ptr = win32_utf16_from_utf8("RVS-FreeBSD")
    e = win32_get_last_error()
    if wide_ptr != 0 then
        dealloc(wide_ptr)
    end
    if h != 0 then
        win32_free_library(h)
    end
    return p + e
end
"""
    ir_text = compile_to_ir_fast(src, source_file="win32_handle_probe.ail")
    if "windows" in ir_text.lower():
        assert '@"LoadLibraryA"' in ir_text
        assert '@"GetProcAddress"' in ir_text
        assert '@"FreeLibrary"' in ir_text
        assert '@"GetLastError"' in ir_text
        assert '@"MultiByteToWideChar"' in ir_text
        assert "ptrtoint" in ir_text


def test_win32_typed_hcs_runtime_replaces_numbered_dynamic_calls() -> None:
    src = """
def main(): int
    slot = alloc(8)
    memset(slot, 0, 8)
    op = win32_hcs_create_operation()
    hr = win32_hcs_open_compute_system("rvs-home", 268435456, slot)
    if op != 0 then
        win32_hcs_close_operation(op)
    end
    handle = peek64(slot, 0)
    if handle != 0 then
        win32_hcs_close_compute_system(handle)
    end
    dealloc(slot)
    return hr
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "ailang_win32_hcs_open_compute_system" in c_code
    assert "HcsOpenComputeSystem" in c_code
    assert "HcsCreateOperation" in c_code
    assert "ailang_win32_call_" not in c_code
    assert "win32" in needs.helpers

    ir_text = compile_to_ir_fast(src, source_file="win32_hcs_typed_probe.ail")
    if "windows" in ir_text.lower():
        assert "HcsOpenComputeSystem" in ir_text
        assert "HcsCreateOperation" in ir_text
        assert "win32_dyn_fn" not in ir_text


def test_c_runtime_allocation_tracking_has_release_gate() -> None:
    src = """
def main(): int
    s = "pkt_" + str(7)
    return strlen(s)
end
"""
    c_code, _needs = _to_c_with_needs(src)
    assert "#define AILANG_TRACK_ALLOCATIONS 1" in c_code
    assert "#if AILANG_TRACK_ALLOCATIONS" in c_code
    assert "AILANG_UNUSED static size_t __ailang_total_allocated" in c_code


def test_c_runtime_allocation_tracking_uses_freebsd_malloc_np() -> None:
    src = """
def main(): int
    s = "hello" + "world"
    dealloc(s)
    return 0
end
"""
    c_code, _needs = _to_c_with_needs(src)
    assert "#elif defined(__FreeBSD__)" in c_code
    assert "#include <malloc_np.h>" in c_code
    assert "#define AILANG_MALLOC_USABLE_SIZE(p) malloc_usable_size(p)" in c_code

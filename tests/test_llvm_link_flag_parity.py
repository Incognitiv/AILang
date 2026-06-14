from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cli.compilation import _detect_llvm_link_flags  # noqa: E402


def test_windows_winsock_flag_detected_from_multiple_symbols() -> None:
    ll_text = """
declare i32 @"WSAStartup"(i16 %".1", ptr %".2")
declare i32 @"closesocket"(i64 %".1")
define i64 @"main"() {
entry:
  %rc = call i32 @"WSAStartup"(i16 514, ptr null)
  %c = call i32 @"closesocket"(i64 0)
  ret i64 0
}
"""
    flags = _detect_llvm_link_flags(ll_text, platform="win32")
    assert "-lws2_32" in flags


def test_windows_winsock_flag_detected_from_socket_symbol() -> None:
    ll_text = """
declare i64 @"socket"(i32 %".1", i32 %".2", i32 %".3")
"""
    flags = _detect_llvm_link_flags(ll_text, platform="win32")
    assert "-lws2_32" in flags


def test_windows_kernel32_flag_detected_from_win32_dynamic_binding() -> None:
    ll_text = """
declare ptr @"LoadLibraryA"(ptr %".1")
declare ptr @"GetProcAddress"(ptr %".1", ptr %".2")
declare i32 @"FreeLibrary"(ptr %".1")
declare i32 @"GetLastError"()
declare i32 @"MultiByteToWideChar"(i32 %".1", i32 %".2", ptr %".3", i32 %".4", ptr %".5", i32 %".6")
declare ptr @"LocalFree"(ptr %".1")
"""
    flags = _detect_llvm_link_flags(ll_text, platform="win32")
    assert "-lkernel32" in flags


def test_pthread_flag_detected_for_non_create_symbols_on_linux() -> None:
    ll_text = """
declare i32 @"pthread_mutex_lock"(ptr %".1")
define i64 @"lock_once"(ptr %"m") {
entry:
  %rc = call i32 @"pthread_mutex_lock"(ptr %"m")
  %rc64 = sext i32 %rc to i64
  ret i64 %rc64
}
"""
    flags = _detect_llvm_link_flags(ll_text, platform="linux")
    assert "-lpthread" in flags


def test_pthread_not_linked_on_windows_path() -> None:
    ll_text = """
declare i32 @"pthread_create"(ptr %".1", ptr %".2", ptr %".3", ptr %".4")
"""
    flags = _detect_llvm_link_flags(ll_text, platform="win32")
    assert "-lpthread" not in flags

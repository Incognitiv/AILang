#!/usr/bin/env python3
"""Compare Win32 UI backend draw-loop speed and AILang leak reports."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
SOURCE_UI_ROOT = SOURCE_ROOT / "ui"
OUT_DIR = REPO_ROOT / "out" / "generated" / "ui_bench"
WIN32_C_BACKEND = REPO_ROOT / "examples" / "ui" / "backends" / "ail_ui_win32_min.c"
WIN32_PURE = SOURCE_UI_ROOT / "win32_pure.ail"
WIN32_PURE_BINDING = SOURCE_UI_ROOT / "win32_pure.cbind.json"
AILANG = REPO_ROOT / "ailang.py"


NATIVE_TRACKER_C = r"""
#ifdef calloc
#undef calloc
#endif
#ifdef malloc
#undef malloc
#endif
#ifdef realloc
#undef realloc
#endif
#ifdef free
#undef free
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32) || defined(_WIN64)
#include <windows.h>
#include <psapi.h>
#endif

typedef struct AilBenchAllocNode {
    void *ptr;
    size_t size;
    struct AilBenchAllocNode *next;
} AilBenchAllocNode;

typedef struct AilBenchHandleNode {
    void *handle;
    int kind;
    struct AilBenchHandleNode *next;
} AilBenchHandleNode;

enum {
    AIL_BENCH_HANDLE_WINDOW = 1,
    AIL_BENCH_HANDLE_GET_DC = 2,
    AIL_BENCH_HANDLE_MEMORY_DC = 3,
    AIL_BENCH_HANDLE_GDI_OBJECT = 4
};

static AilBenchAllocNode *ail_bench_allocs = NULL;
static AilBenchHandleNode *ail_bench_handles = NULL;
static size_t ail_bench_live_bytes = 0;
static size_t ail_bench_alloc_count = 0;
static size_t ail_bench_free_count = 0;
static size_t ail_bench_window_live = 0;
static size_t ail_bench_get_dc_live = 0;
static size_t ail_bench_memory_dc_live = 0;
static size_t ail_bench_gdi_object_live = 0;
static unsigned long ail_bench_gdi_start = 0;
static unsigned long ail_bench_user_start = 0;
static size_t ail_bench_private_start = 0;
static size_t ail_bench_working_set_start = 0;

static void ail_bench_query_memory(size_t *private_bytes, size_t *working_set) {
    *private_bytes = 0;
    *working_set = 0;
#if defined(_WIN32) || defined(_WIN64)
    PROCESS_MEMORY_COUNTERS_EX counters;
    memset(&counters, 0, sizeof(counters));
    counters.cb = sizeof(counters);
    if (GetProcessMemoryInfo(
            GetCurrentProcess(),
            (PROCESS_MEMORY_COUNTERS *)&counters,
            sizeof(counters))) {
        *private_bytes = (size_t)counters.PrivateUsage;
        *working_set = (size_t)counters.WorkingSetSize;
    }
#endif
}

static void ail_bench_native_report(void) {
    long gdi_delta = 0;
    long user_delta = 0;
    long long private_delta = 0;
    long long working_set_delta = 0;
#if defined(_WIN32) || defined(_WIN64)
    unsigned long gdi_end = GetGuiResources(GetCurrentProcess(), 0);
    unsigned long user_end = GetGuiResources(GetCurrentProcess(), 1);
    size_t private_end = 0;
    size_t working_set_end = 0;
    ail_bench_query_memory(&private_end, &working_set_end);
    gdi_delta = (long)gdi_end - (long)ail_bench_gdi_start;
    user_delta = (long)user_end - (long)ail_bench_user_start;
    private_delta = (long long)private_end - (long long)ail_bench_private_start;
    working_set_delta =
        (long long)working_set_end - (long long)ail_bench_working_set_start;
#endif
    printf("AIL_UI_NATIVE_LEAK_BEGIN\n");
    printf("%zu\n", ail_bench_live_bytes);
    printf("%zu\n", ail_bench_alloc_count);
    printf("%zu\n", ail_bench_free_count);
    printf("%ld\n", gdi_delta);
    printf("%ld\n", user_delta);
    printf("%lld\n", private_delta);
    printf("%lld\n", working_set_delta);
    printf("AIL_UI_NATIVE_LEAK_END\n");
}

static void ail_bench_native_start(void) __attribute__((constructor));
static void ail_bench_native_start(void) {
#if defined(_WIN32) || defined(_WIN64)
    ail_bench_gdi_start = GetGuiResources(GetCurrentProcess(), 0);
    ail_bench_user_start = GetGuiResources(GetCurrentProcess(), 1);
    ail_bench_query_memory(&ail_bench_private_start, &ail_bench_working_set_start);
#endif
    atexit(ail_bench_native_report);
}

static void ail_bench_track_alloc(void *ptr, size_t size) {
    if (ptr == NULL) {
        return;
    }

    AilBenchAllocNode *node = (AilBenchAllocNode *)malloc(sizeof(AilBenchAllocNode));
    if (node == NULL) {
        return;
    }

    node->ptr = ptr;
    node->size = size;
    node->next = ail_bench_allocs;
    ail_bench_allocs = node;
    ail_bench_live_bytes += node->size;
    ail_bench_alloc_count += 1;
}

void *ail_bench_malloc(size_t size) {
    void *ptr = malloc(size);
    ail_bench_track_alloc(ptr, size);
    return ptr;
}

void *ail_bench_calloc(size_t count, size_t size) {
    void *ptr = calloc(count, size);
    ail_bench_track_alloc(ptr, count * size);
    return ptr;
}

void *ail_bench_realloc(void *ptr, size_t size) {
    if (ptr == NULL) {
        return ail_bench_malloc(size);
    }

    AilBenchAllocNode *cursor = ail_bench_allocs;
    while (cursor != NULL) {
        if (cursor->ptr == ptr) {
            void *new_ptr = realloc(ptr, size);
            if (new_ptr == NULL) {
                return NULL;
            }
            ail_bench_live_bytes -= cursor->size;
            cursor->ptr = new_ptr;
            cursor->size = size;
            ail_bench_live_bytes += cursor->size;
            return new_ptr;
        }
        cursor = cursor->next;
    }

    void *new_ptr = realloc(ptr, size);
    ail_bench_track_alloc(new_ptr, size);
    return new_ptr;
}

void ail_bench_free(void *ptr) {
    if (ptr == NULL) {
        return;
    }

    AilBenchAllocNode **cursor = &ail_bench_allocs;
    while (*cursor != NULL) {
        AilBenchAllocNode *node = *cursor;
        if (node->ptr == ptr) {
            *cursor = node->next;
            ail_bench_live_bytes -= node->size;
            ail_bench_free_count += 1;
            free(node);
            free(ptr);
            return;
        }
        cursor = &node->next;
    }

    free(ptr);
}

static void ail_bench_inc_handle_kind(int kind) {
    if (kind == AIL_BENCH_HANDLE_WINDOW) {
        ail_bench_window_live += 1;
    } else if (kind == AIL_BENCH_HANDLE_GET_DC) {
        ail_bench_get_dc_live += 1;
    } else if (kind == AIL_BENCH_HANDLE_MEMORY_DC) {
        ail_bench_memory_dc_live += 1;
    } else if (kind == AIL_BENCH_HANDLE_GDI_OBJECT) {
        ail_bench_gdi_object_live += 1;
    }
}

static void ail_bench_dec_handle_kind(int kind) {
    if (kind == AIL_BENCH_HANDLE_WINDOW && ail_bench_window_live > 0) {
        ail_bench_window_live -= 1;
    } else if (kind == AIL_BENCH_HANDLE_GET_DC && ail_bench_get_dc_live > 0) {
        ail_bench_get_dc_live -= 1;
    } else if (kind == AIL_BENCH_HANDLE_MEMORY_DC && ail_bench_memory_dc_live > 0) {
        ail_bench_memory_dc_live -= 1;
    } else if (kind == AIL_BENCH_HANDLE_GDI_OBJECT && ail_bench_gdi_object_live > 0) {
        ail_bench_gdi_object_live -= 1;
    }
}

static void ail_bench_track_handle(void *handle, int kind) {
    if (handle == NULL) {
        return;
    }

    AilBenchHandleNode *node =
        (AilBenchHandleNode *)malloc(sizeof(AilBenchHandleNode));
    if (node == NULL) {
        return;
    }

    node->handle = handle;
    node->kind = kind;
    node->next = ail_bench_handles;
    ail_bench_handles = node;
    ail_bench_inc_handle_kind(kind);
}

static int ail_bench_untrack_handle(void *handle, int expected_kind) {
    if (handle == NULL) {
        return 0;
    }

    AilBenchHandleNode **cursor = &ail_bench_handles;
    while (*cursor != NULL) {
        AilBenchHandleNode *node = *cursor;
        if (node->handle == handle &&
            (expected_kind == 0 || expected_kind == node->kind)) {
            *cursor = node->next;
            ail_bench_dec_handle_kind(node->kind);
            free(node);
            return 1;
        }
        cursor = &node->next;
    }
    return 0;
}

#if defined(_WIN32) || defined(_WIN64)
HWND WINAPI ail_bench_CreateWindowExA(
    DWORD ex_style,
    LPCSTR class_name,
    LPCSTR window_name,
    DWORD style,
    int x,
    int y,
    int width,
    int height,
    HWND parent,
    HMENU menu,
    HINSTANCE instance,
    LPVOID param) {
    HWND hwnd = CreateWindowExA(
        ex_style,
        class_name,
        window_name,
        style,
        x,
        y,
        width,
        height,
        parent,
        menu,
        instance,
        param);
    ail_bench_track_handle(hwnd, AIL_BENCH_HANDLE_WINDOW);
    return hwnd;
}

BOOL WINAPI ail_bench_DestroyWindow(HWND hwnd) {
    BOOL ok = DestroyWindow(hwnd);
    if (ok) {
        ail_bench_untrack_handle(hwnd, AIL_BENCH_HANDLE_WINDOW);
    }
    return ok;
}

HDC WINAPI ail_bench_GetDC(HWND hwnd) {
    HDC dc = GetDC(hwnd);
    ail_bench_track_handle(dc, AIL_BENCH_HANDLE_GET_DC);
    return dc;
}

int WINAPI ail_bench_ReleaseDC(HWND hwnd, HDC dc) {
    int ok = ReleaseDC(hwnd, dc);
    if (ok) {
        ail_bench_untrack_handle(dc, AIL_BENCH_HANDLE_GET_DC);
    }
    return ok;
}

HDC WINAPI ail_bench_CreateCompatibleDC(HDC dc) {
    HDC memory_dc = CreateCompatibleDC(dc);
    ail_bench_track_handle(memory_dc, AIL_BENCH_HANDLE_MEMORY_DC);
    return memory_dc;
}

BOOL WINAPI ail_bench_DeleteDC(HDC dc) {
    BOOL ok = DeleteDC(dc);
    if (ok) {
        ail_bench_untrack_handle(dc, AIL_BENCH_HANDLE_MEMORY_DC);
    }
    return ok;
}

HBITMAP WINAPI ail_bench_CreateDIBSection(
    HDC dc,
    const BITMAPINFO *bmi,
    UINT usage,
    void **bits,
    HANDLE section,
    DWORD offset) {
    HBITMAP bitmap = CreateDIBSection(dc, bmi, usage, bits, section, offset);
    ail_bench_track_handle(bitmap, AIL_BENCH_HANDLE_GDI_OBJECT);
    return bitmap;
}

HFONT WINAPI ail_bench_CreateFontA(
    int height,
    int width,
    int escapement,
    int orientation,
    int weight,
    DWORD italic,
    DWORD underline,
    DWORD strike_out,
    DWORD charset,
    DWORD out_precision,
    DWORD clip_precision,
    DWORD quality,
    DWORD pitch_and_family,
    LPCSTR face_name) {
    HFONT font = CreateFontA(
        height,
        width,
        escapement,
        orientation,
        weight,
        italic,
        underline,
        strike_out,
        charset,
        out_precision,
        clip_precision,
        quality,
        pitch_and_family,
        face_name);
    ail_bench_track_handle(font, AIL_BENCH_HANDLE_GDI_OBJECT);
    return font;
}

HBRUSH WINAPI ail_bench_CreateSolidBrush(COLORREF color) {
    HBRUSH brush = CreateSolidBrush(color);
    ail_bench_track_handle(brush, AIL_BENCH_HANDLE_GDI_OBJECT);
    return brush;
}

BOOL WINAPI ail_bench_DeleteObject(HGDIOBJ obj) {
    BOOL ok = DeleteObject(obj);
    if (ok) {
        ail_bench_untrack_handle(obj, AIL_BENCH_HANDLE_GDI_OBJECT);
    }
    return ok;
}
#endif
"""


BENCH_BODY = """
def bench_draw_workload(window: int, frame: int): void
    ail_ui_begin_frame(window, 0x202225)

    i = 0
    while i < 512 then
        x = (i * 37 + frame * 3) % 760
        y = (i * 19 + frame * 5) % 420
        w = 10 + ((i * 7) % 40)
        h = 8 + ((i * 11) % 28)
        color = 0x202020 + ((i * 123457 + frame * 257) % 0xd0d0d0)
        ail_ui_draw_rect(window, x, y, w, h, color)
        i = i + 1
    end

    t = 0
    while t < 32 then
        ail_ui_draw_text(window, 16, 18 + t * 14, 1, 0xe8eef5, "AILang UI bench")
        t = t + 1
    end

    ail_ui_end_frame(window)
end

def main(): int
    frames_per_window = __FRAMES__
    cycles = __CYCLES__

    if ail_ui_init() == 0 then
        print("AIL_UI_BENCH_INIT_FAILED")
        return 1
    end

    total_frames = 0
    cycle = 0
    while cycle < cycles then
        window = ail_ui_open_window("AILang UI backend bench", 800, 480)
        if window == 0 then
            print("AIL_UI_BENCH_WINDOW_FAILED")
            ail_ui_shutdown()
            return 2
        end

        frame = 0
        while frame < frames_per_window and ail_ui_window_alive(window) == 1 then
            ail_ui_poll_event(window)
            bench_draw_workload(window, frame)
            frame = frame + 1
            total_frames = total_frames + 1
        end

        ail_ui_close_window(window)
        cycle = cycle + 1
    end
    ail_ui_shutdown()

    print("AIL_UI_BENCH_BEGIN")
    print(total_frames)
    print("AIL_UI_BENCH_END")
    return 0
end
"""


def _run(cmd: list[str], *, timeout: int = 180, env: dict[str, str] | None = None):
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _write_source(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _bench_body(frames: int, cycles: int) -> str:
    return BENCH_BODY.replace("__FRAMES__", str(frames)).replace(
        "__CYCLES__", str(cycles)
    )


def _build_c_backend(frames: int, cycles: int) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("gcc not found on PATH")

    sys.path.insert(0, str(SOURCE_ROOT))
    from cli.compilation import _extract_ailang_link_flags, _merge_link_flags
    from transpiler.core import transpile_file

    source = OUT_DIR / "c_backend_bench.ail"
    generated_c = OUT_DIR / "c_backend_bench.c"
    generated_obj = OUT_DIR / "c_backend_bench.o"
    backend_obj = OUT_DIR / "ail_ui_win32_min_bench.o"
    tracker_c = OUT_DIR / "native_leak_tracker.c"
    tracker_obj = OUT_DIR / "native_leak_tracker.o"
    output_exe = OUT_DIR / "c_backend_bench.exe"
    _write_source(source, "import stdlib.ui.platform\n\n" + _bench_body(frames, cycles))
    _write_source(tracker_c, NATIVE_TRACKER_C)

    transpile_file(str(source), str(generated_c))
    generated_link_flags = _extract_ailang_link_flags(
        generated_c.read_text(encoding="utf-8")
    )
    platform_libs = (
        ["-luser32", "-lgdi32", "-lpsapi"] if sys.platform.startswith("win") else []
    )
    link_flags = _merge_link_flags(generated_link_flags, ["-lm"], platform_libs)

    compile_steps = [
        [
            gcc,
            "-std=gnu23",
            "-O2",
            "-c",
            str(generated_c),
            "-o",
            str(generated_obj),
        ],
        [
            gcc,
            "-std=gnu23",
            "-O2",
            "-Dmalloc=ail_bench_malloc",
            "-Dcalloc=ail_bench_calloc",
            "-Drealloc=ail_bench_realloc",
            "-Dfree=ail_bench_free",
            "-c",
            str(WIN32_C_BACKEND),
            "-o",
            str(backend_obj),
        ],
        [
            gcc,
            "-std=gnu23",
            "-O2",
            "-c",
            str(tracker_c),
            "-o",
            str(tracker_obj),
        ],
        [
            gcc,
            str(generated_obj),
            str(backend_obj),
            str(tracker_obj),
            "-o",
            str(output_exe),
            *link_flags,
        ],
    ]
    for step in compile_steps:
        proc = _run(step)
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout + proc.stderr)
    return output_exe


def _strip_main(source: str) -> str:
    marker = "\ndef main(): int"
    index = source.rfind(marker)
    if index < 0:
        raise RuntimeError("could not find win32_pure.ail smoke main")
    return source[:index].rstrip() + "\n\n"


def _build_pure_backend(frames: int, cycles: int) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("gcc not found on PATH")

    source = OUT_DIR / "pure_backend_bench.ail"
    tracker_c = OUT_DIR / "native_leak_tracker_pure.c"
    tracker_obj = OUT_DIR / "native_leak_tracker_pure.o"
    output_exe = OUT_DIR / "pure_backend_bench.exe"
    _write_source(tracker_c, NATIVE_TRACKER_C)
    proc = _run(
        [
            gcc,
            "-std=gnu23",
            "-O2",
            "-c",
            str(tracker_c),
            "-o",
            str(tracker_obj),
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)

    pure_text = WIN32_PURE.read_text(encoding="utf-8")
    pure_text = pure_text.replace(
        '#cimport windows "win32_pure.cbind.json"',
        f'#cimport windows "{WIN32_PURE_BINDING.as_posix()}"',
    )
    _write_source(
        source,
        f'#link windows "{tracker_obj.as_posix()}"\n'
        '#link windows "-lpsapi"\n'
        + _strip_main(pure_text)
        + _bench_body(frames, cycles),
    )

    proc = _run(
        [
            sys.executable,
            str(AILANG),
            str(source),
            "--backend=c",
            "--native-toolchain=gcc",
            "-o",
            str(output_exe),
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    return output_exe


BenchmarkResult = tuple[
    int,
    int,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
]


def _parse_result(output: str, elapsed_ns: int) -> BenchmarkResult:
    match = re.search(r"AIL_UI_BENCH_BEGIN\s+(\d+)\s+AIL_UI_BENCH_END", output)
    if not match:
        raise RuntimeError(f"benchmark markers missing:\n{output}")
    frames = int(match.group(1))
    leak_match = re.search(r"live at exit:\s+(\d+) bytes", output)
    live_bytes = int(leak_match.group(1)) if leak_match else None
    native_match = re.search(
        r"AIL_UI_NATIVE_LEAK_BEGIN\s+"
        r"(\d+)\s+\d+\s+\d+\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+"
        r"AIL_UI_NATIVE_LEAK_END",
        output,
    )
    native_live = int(native_match.group(1)) if native_match else None
    gdi_delta = int(native_match.group(2)) if native_match else None
    user_delta = int(native_match.group(3)) if native_match else None
    private_delta = int(native_match.group(4)) if native_match else None
    working_set_delta = int(native_match.group(5)) if native_match else None
    return (
        frames,
        elapsed_ns,
        live_bytes,
        native_live,
        gdi_delta,
        user_delta,
        private_delta,
        working_set_delta,
    )


def _run_benchmark(exe: Path, repeats: int) -> list[BenchmarkResult]:
    env = os.environ.copy()
    env["AILANG_LEAK_REPORT"] = "1"
    results = []
    for _ in range(repeats):
        start_ns = time.perf_counter_ns()
        proc = _run([str(exe)], timeout=120, env=env)
        elapsed_ns = time.perf_counter_ns() - start_ns
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout + proc.stderr)
        results.append(_parse_result(proc.stdout + proc.stderr, elapsed_ns))
    return results


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _summarize(name: str, results: list[BenchmarkResult]) -> dict[str, float]:
    ms = [result[1] / 1_000_000.0 for result in results]
    fps = [(result[0] * 1_000_000_000.0) / result[1] for result in results]
    live_values = [result[2] for result in results if result[2] is not None]
    native_values = [result[3] for result in results if result[3] is not None]
    gdi_values = [result[4] for result in results if result[4] is not None]
    user_values = [result[5] for result in results if result[5] is not None]
    private_values = [result[6] for result in results if result[6] is not None]
    working_set_values = [result[7] for result in results if result[7] is not None]
    live_max = max(live_values) if live_values else -1
    native_max = max(native_values) if native_values else -1
    gdi_max = max(gdi_values) if gdi_values else -1
    user_max = max(user_values) if user_values else -1
    private_max = max(private_values) if private_values else -1
    working_set_max = max(working_set_values) if working_set_values else -1
    print(
        f"{name}: median_ms={_median(ms):.3f} "
        f"median_fps={_median(fps):.1f} "
        f"ailang_live_bytes_max={live_max} "
        f"native_live_bytes_max={native_max} "
        f"gdi_delta_max={gdi_max} "
        f"user_delta_max={user_max} "
        f"private_delta_max={private_max} "
        f"working_set_delta_max={working_set_max}"
    )
    return {"median_ms": _median(ms), "median_fps": _median(fps)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=80)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("This benchmark compares Win32 backends and must run on Windows.")
        return 2

    c_exe = _build_c_backend(args.frames, args.cycles)
    pure_exe = _build_pure_backend(args.frames, args.cycles)

    c_results = _run_benchmark(c_exe, args.repeats)
    pure_results = _run_benchmark(pure_exe, args.repeats)
    c_summary = _summarize("c-win32", c_results)
    pure_summary = _summarize("pure-ailang-win32", pure_results)

    ratio = pure_summary["median_ms"] / c_summary["median_ms"]
    print(f"pure_vs_c_time_ratio={ratio:.3f}")
    print(f"artifacts={OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

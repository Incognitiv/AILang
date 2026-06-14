from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
SOURCE_UI_ROOT = SOURCE_ROOT / "ui"
AILANG = REPO_ROOT / "ailang.py"
PLATFORM_CONTRACT = REPO_ROOT / "stdlib" / "ui" / "platform.ail"
PAINT_LAYER = REPO_ROOT / "stdlib" / "ui" / "paint.ail"
LAYOUT_LAYER = REPO_ROOT / "stdlib" / "ui" / "layout.ail"
LAYOUT_STATE_CHECK = REPO_ROOT / "examples" / "ui" / "layout_state_check.ail"
UI_LOOP = REPO_ROOT / "examples" / "ui" / "ui_platform_loop.ail"
DESKTOP_DEMO = REPO_ROOT / "examples" / "ui" / "ui_desktop.ail"
EXPLORER_CONTENT = REPO_ROOT / "examples" / "ui" / "apps" / "explorer_content.ail"
DEFAULT_DESKTOP_CONTENT = (
    REPO_ROOT / "examples" / "ui" / "apps" / "default_desktop_content.ail"
)
UI_BACKEND_FACADE = SOURCE_UI_ROOT / "ui_backend.ail"
UI_BACKEND_SMOKE = REPO_ROOT / "examples" / "ui" / "ui_backend_smoke.ail"
DESKTOP_PURE_DEMO = REPO_ROOT / "examples" / "ui" / "ui_desktop_pure.ail"
WIN32_BACKEND = REPO_ROOT / "examples" / "ui" / "backends" / "ail_ui_win32_min.c"
WIN32_PURE_BACKEND = SOURCE_UI_ROOT / "win32_pure.ail"
WIN32_PURE_BINDINGS = SOURCE_UI_ROOT / "win32_pure.cbind.json"
X11_PURE_BACKEND = SOURCE_UI_ROOT / "x11_pure.ail"
X11_PURE_BINDINGS = SOURCE_UI_ROOT / "x11_pure.cbind.json"
WAYLAND_PURE_BACKEND = SOURCE_UI_ROOT / "wayland_pure.ail"
WAYLAND_PURE_BINDINGS = SOURCE_UI_ROOT / "wayland_pure.cbind.json"
NULL_BACKEND = REPO_ROOT / "examples" / "ui" / "backends" / "ail_ui_null.c"
BUILD_DEMO = REPO_ROOT / "tools" / "build_ui_demo.py"
UI_PLATFORM_DOC = REPO_ROOT / "docs" / "AILANG_UI_PLATFORM.md"
CANONICAL_UI_DSL = REPO_ROOT / "archived" / "source-cruft" / "Desktop Experiment"


def _run_check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AILANG), str(path), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_ui_platform_contract_passes_check() -> None:
    proc = _run_check(PLATFORM_CONTRACT)
    assert (
        proc.returncode == 0
    ), f"--check failed for platform.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_layout_layer_passes_check() -> None:
    proc = _run_check(LAYOUT_LAYER)
    assert (
        proc.returncode == 0
    ), f"--check failed for layout.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_paint_layer_passes_check() -> None:
    proc = _run_check(PAINT_LAYER)
    assert (
        proc.returncode == 0
    ), f"--check failed for paint.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_layout_state_check_passes_check() -> None:
    proc = _run_check(LAYOUT_STATE_CHECK)
    assert (
        proc.returncode == 0
    ), f"--check failed for layout_state_check.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_platform_loop_passes_check() -> None:
    proc = _run_check(UI_LOOP)
    assert (
        proc.returncode == 0
    ), f"--check failed for ui_platform_loop.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_desktop_passes_check() -> None:
    proc = _run_check(DESKTOP_DEMO)
    assert (
        proc.returncode == 0
    ), f"--check failed for ui_desktop.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_desktop_pure_passes_check() -> None:
    proc = _run_check(DESKTOP_PURE_DEMO)
    assert (
        proc.returncode == 0
    ), f"--check failed for ui_desktop_pure.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_backend_facade_passes_check() -> None:
    proc = _run_check(UI_BACKEND_FACADE)
    assert (
        proc.returncode == 0
    ), f"--check failed for ui_backend.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_backend_smoke_passes_check() -> None:
    proc = _run_check(UI_BACKEND_SMOKE)
    assert (
        proc.returncode == 0
    ), f"--check failed for ui_backend_smoke.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_backend_facade_selects_pure_target_backends() -> None:
    source = UI_BACKEND_FACADE.read_text(encoding="utf-8")
    assert "import windows win32_pure" in source
    assert "import linux wayland_pure" in source
    assert "ail_ui_win32_min.c" not in source


def test_source_ui_is_canonical_ailang_backend_root() -> None:
    assert UI_BACKEND_FACADE.parent == SOURCE_UI_ROOT
    assert WIN32_PURE_BACKEND.exists()
    assert X11_PURE_BACKEND.exists()
    assert WAYLAND_PURE_BACKEND.exists()
    assert "import source.ui.ui_backend" in UI_BACKEND_SMOKE.read_text(encoding="utf-8")
    assert "import source.ui.ui_backend" in DESKTOP_PURE_DEMO.read_text(
        encoding="utf-8"
    )


def test_explorer_content_passes_check() -> None:
    proc = _run_check(EXPLORER_CONTENT)
    assert (
        proc.returncode == 0
    ), f"--check failed for explorer_content.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_default_desktop_content_passes_check() -> None:
    proc = _run_check(DEFAULT_DESKTOP_CONTENT)
    assert (
        proc.returncode == 0
    ), f"--check failed for default_desktop_content.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_archived_desktop_experiment_ui_dsl_passes_check() -> None:
    files = sorted(CANONICAL_UI_DSL.glob("*.ail"))
    if not files:
        pytest.skip("optional archived UI DSL corpus is not included")
    for path in files:
        proc = _run_check(path)
        assert (
            proc.returncode == 0
        ), f"--check failed for canonical UI DSL file {path}\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_ui_platform_doc_points_to_repo_canonical_dsl() -> None:
    text = UI_PLATFORM_DOC.read_text(encoding="utf-8")
    assert r"archived\source-cruft\Desktop Experiment" in text
    assert "Downloads" not in text
    assert "AILang-main" not in text


def test_win32_ui_backend_compiles_as_c_object(tmp_path: Path) -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable")

    out_obj = tmp_path / "ail_ui_win32_min.o"
    proc = subprocess.run(
        [gcc, "-c", str(WIN32_BACKEND), "-o", str(out_obj)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        proc.returncode == 0
    ), f"backend C compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    assert out_obj.exists()


def test_win32_pure_backend_proof_passes_check() -> None:
    if not sys.platform.startswith("win"):
        pytest.skip("pure Win32 backend proof is Windows-only")
    proc = _run_check(WIN32_PURE_BACKEND)
    assert (
        proc.returncode == 0
    ), f"--check failed for win32_pure.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_win32_pure_backend_covers_borderless_dwm_and_dib_surface() -> None:
    source = WIN32_PURE_BACKEND.read_text(encoding="utf-8")
    spec = json.loads(WIN32_PURE_BINDINGS.read_text(encoding="utf-8"))
    functions = {row["name"] for row in spec["functions"]}
    macros = set(spec["macros"])

    assert "DwmSetWindowAttribute" in functions
    assert "UpdateLayeredWindow" in functions
    assert "CreateFontA" in functions
    assert "MsgWaitForMultipleObjects" in functions
    assert "WM_NCHITTEST" in macros
    assert "WS_EX_LAYERED" in macros
    assert "win32_present_layered_borderless" in source
    assert "win32_borderless_hit_test" in source
    assert "win32_handle_dpi_changed" in source


def test_win32_pure_backend_builds_and_exits_cleanly(tmp_path: Path) -> None:
    if not sys.platform.startswith("win"):
        pytest.skip("pure Win32 backend proof is Windows-only")
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable")

    output_path = tmp_path / "win32_pure_smoke.exe"
    build_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(WIN32_PURE_BACKEND),
            "--backend=c",
            "--native-toolchain=gcc",
            "-o",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert (
        build_proc.returncode == 0
    ), f"pure backend build failed\nstdout:\n{build_proc.stdout}\n\nstderr:\n{build_proc.stderr}"
    assert output_path.exists()

    env = dict(**os.environ, AILANG_LEAK_REPORT="1")
    run_proc = subprocess.run(
        [str(output_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined = run_proc.stdout + run_proc.stderr
    assert (
        run_proc.returncode == 0
    ), f"pure backend run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    assert "live at exit:    0 bytes (clean)" in combined


def test_ui_backend_smoke_builds_and_exits_cleanly(tmp_path: Path) -> None:
    if not sys.platform.startswith("win"):
        pytest.skip("pure UI backend smoke is Windows-only in this test environment")
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable")

    output_path = tmp_path / "ui_backend_smoke.exe"
    build_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(UI_BACKEND_SMOKE),
            "--backend=c",
            "--native-toolchain=gcc",
            "-o",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert (
        build_proc.returncode == 0
    ), f"pure UI backend smoke build failed\nstdout:\n{build_proc.stdout}\n\nstderr:\n{build_proc.stderr}"
    assert output_path.exists()

    env = dict(**os.environ, AILANG_LEAK_REPORT="1", AILANG_UI_DEMO_FRAMES="2")
    run_proc = subprocess.run(
        [str(output_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined = run_proc.stdout + run_proc.stderr
    assert (
        run_proc.returncode == 0
    ), f"pure UI backend smoke run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    assert "live at exit:    0 bytes (clean)" in combined


def test_x11_pure_backend_binding_spec_is_present() -> None:
    source = X11_PURE_BACKEND.read_text(encoding="utf-8")
    spec = json.loads(X11_PURE_BINDINGS.read_text(encoding="utf-8"))
    functions = {row["name"] for row in spec["functions"]}
    records = {row["name"] for row in spec["records"]}

    assert '#cimport x11 "x11_pure.cbind.json"' in source
    assert "XOpenDisplay" in functions
    assert "XCreateSimpleWindow" in functions
    assert "XNextEvent" in functions
    assert "XEvent" in records
    assert "XConfigureEvent" in records


def test_x11_pure_backend_passes_check_when_x11_dev_headers_exist() -> None:
    if sys.platform.startswith("win"):
        pytest.skip("pure X11 backend proof is non-Windows only")
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        pytest.skip("C compiler unavailable")
    pkg_config = shutil.which("pkg-config")
    if pkg_config is None:
        pytest.skip("pkg-config unavailable")
    has_x11 = subprocess.run(
        [pkg_config, "--exists", "x11"],
        cwd=REPO_ROOT,
        timeout=30,
        check=False,
    )
    if has_x11.returncode != 0:
        pytest.skip("X11 development headers unavailable")

    proc = _run_check(X11_PURE_BACKEND)
    assert (
        proc.returncode == 0
    ), f"--check failed for x11_pure.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_wayland_pure_backend_binding_spec_is_present() -> None:
    source = WAYLAND_PURE_BACKEND.read_text(encoding="utf-8")
    spec = json.loads(WAYLAND_PURE_BINDINGS.read_text(encoding="utf-8"))
    wrappers = {row["name"] for row in spec["wrappers"]}
    records = {row["name"] for row in spec["records"]}
    macros = {
        row["name"] if isinstance(row, dict) else row for row in spec.get("macros", [])
    }

    assert '#cimport linux "wayland_pure.cbind.json"' in source
    assert "placement is compositor-owned" in source
    assert "generated/wayland/xdg-shell-protocol.c" in "\n".join(
        spec.get("c_prelude", [])
    )
    assert "ail_wl_display_connect_default" in wrappers
    assert "ail_wl_registry_bind_seat" in wrappers
    assert "ail_wl_pointer_add_listener" in wrappers
    assert "ail_wl_keyboard_add_listener" in wrappers
    assert "ail_wl_surface_damage_buffer" in wrappers
    assert "ail_xdg_toplevel_add_listener" in wrappers
    assert "wl_registry_listener" in records
    assert "wl_seat_listener" in records
    assert "wl_pointer_listener" in records
    assert "wl_keyboard_listener" in records
    assert "xdg_toplevel_listener" in records
    assert "AIL_WAYLAND_SHM_FORMAT_XRGB8888" in macros
    assert "AIL_WAYLAND_SEAT_CAP_POINTER" in macros
    assert "wayland_pointer_button" in source
    assert "wayland_keyboard_key" in source


def test_wayland_pure_backend_passes_check_when_wayland_dev_headers_exist() -> None:
    if sys.platform.startswith("win"):
        pytest.skip("pure Wayland backend proof is non-Windows only")
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        pytest.skip("C compiler unavailable")
    pkg_config = shutil.which("pkg-config")
    if pkg_config is None:
        pytest.skip("pkg-config unavailable")
    has_wayland = subprocess.run(
        [pkg_config, "--exists", "wayland-client"],
        cwd=REPO_ROOT,
        timeout=30,
        check=False,
    )
    if has_wayland.returncode != 0:
        pytest.skip("Wayland development headers unavailable")

    proc = _run_check(WAYLAND_PURE_BACKEND)
    assert (
        proc.returncode == 0
    ), f"--check failed for wayland_pure.ail\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


def test_wayland_pure_backend_builds_and_exits_cleanly_when_display_available(
    tmp_path: Path,
) -> None:
    if sys.platform.startswith("win"):
        pytest.skip("pure Wayland backend proof is non-Windows only")
    if shutil.which("gcc") is None:
        pytest.skip("gcc unavailable")
    if not os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("XDG_RUNTIME_DIR"):
        pytest.skip("Wayland display unavailable")
    pkg_config = shutil.which("pkg-config")
    if pkg_config is None:
        pytest.skip("pkg-config unavailable")
    has_wayland = subprocess.run(
        [pkg_config, "--exists", "wayland-client"],
        cwd=REPO_ROOT,
        timeout=30,
        check=False,
    )
    if has_wayland.returncode != 0:
        pytest.skip("Wayland development headers unavailable")

    output_path = tmp_path / "wayland_pure_smoke"
    build_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(WAYLAND_PURE_BACKEND),
            "--backend=c",
            "--native-toolchain=gcc",
            "-o",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert (
        build_proc.returncode == 0
    ), f"Wayland build failed\nstdout:\n{build_proc.stdout}\n\nstderr:\n{build_proc.stderr}"

    env = dict(**os.environ, AILANG_LEAK_REPORT="1")
    run_proc = subprocess.run(
        [str(output_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined = run_proc.stdout + run_proc.stderr
    assert (
        run_proc.returncode == 0
    ), f"Wayland smoke failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    assert "live at exit:    0 bytes (clean)" in combined


def test_ui_layout_state_check_runs_with_null_backend(tmp_path: Path) -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable")

    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    from transpiler.core import transpile_file

    generated_c = tmp_path / "layout_state_check.c"
    output_exe = tmp_path / (
        "layout_state_check.exe"
        if sys.platform.startswith("win")
        else "layout_state_check"
    )
    transpile_file(str(LAYOUT_STATE_CHECK), str(generated_c))

    build_proc = subprocess.run(
        [
            gcc,
            "-std=gnu23",
            str(generated_c),
            str(NULL_BACKEND),
            "-o",
            str(output_exe),
            "-lm",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (
        build_proc.returncode == 0
    ), f"layout state build failed\nstdout:\n{build_proc.stdout}\n\nstderr:\n{build_proc.stderr}"

    env = dict(**os.environ, AILANG_LEAK_REPORT="1")
    run_proc = subprocess.run(
        [str(output_exe)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined = run_proc.stdout + run_proc.stderr
    assert (
        run_proc.returncode == 0
    ), f"layout state run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"
    assert "layout state ok" in run_proc.stdout
    assert "live at exit:    0 bytes (clean)" in combined


def test_ui_demo_builds_executable() -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable")

    proc = subprocess.run(
        [sys.executable, str(BUILD_DEMO)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert (
        proc.returncode == 0
    ), f"demo build failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    output_path = Path(proc.stdout.strip().splitlines()[-1])
    assert output_path.exists()


def test_ui_demo_can_exit_after_frame_limit() -> None:
    if not sys.platform.startswith("win"):
        pytest.skip("live Win32 smoke is Windows-only")
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable")

    build_proc = subprocess.run(
        [sys.executable, str(BUILD_DEMO)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert (
        build_proc.returncode == 0
    ), f"demo build failed\nstdout:\n{build_proc.stdout}\n\nstderr:\n{build_proc.stderr}"
    output_path = Path(build_proc.stdout.strip().splitlines()[-1])

    env = dict(**os.environ, AILANG_UI_DEMO_FRAMES="2")
    run_proc = subprocess.run(
        [str(output_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (
        run_proc.returncode == 0
    ), f"demo run failed\nstdout:\n{run_proc.stdout}\n\nstderr:\n{run_proc.stderr}"

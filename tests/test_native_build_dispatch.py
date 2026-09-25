"""Automatic build selection preserves explicit requests and never masks errors."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))


@pytest.fixture
def dispatch(monkeypatch):
    # External boundaries are mocked; the real dispatcher is executed unchanged.
    replacements = {
        "cli.compilation": {"compile_to_native": Mock(return_value=True)},
        "cli.cinclude_diagnostics": {"emit_cinclude_backend_warning": Mock()},
        "cli.link_flags": {
            "_extract_ailang_link_flags": Mock(return_value=[]),
            "_merge_link_flags": lambda *groups: sum(groups, []),
        },
        "cli.llvm_diagnostics": {"_detect_llvm_link_flags": Mock(return_value=[])},
        "runtime.modes": {
            "CompilationContext": SimpleNamespace(get_mode=lambda: "hosted"),
            "CompilationMode": SimpleNamespace(HOSTED="hosted"),
        },
        "runtime.phases": {"Phase": object},
        "codegen.codegen_errors": {"CodeGenError": type("CodeGenError", (Exception,), {})},
    }
    for name, fields in replacements.items():
        module = ModuleType(name)
        for field, value in fields.items():
            setattr(module, field, value)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "automatic_dispatch_test", ROOT / "source" / "cli" / "native_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.memory_link_supported = lambda: True
    module._memory_build = Mock(return_value=True)
    return module


def test_auto_uses_memory_path_without_user_linker_option(dispatch) -> None:
    assert dispatch.compile_to_native("source.ail", "program")
    dispatch._memory_build.assert_called_once_with("source.ail", "program", 3)
    dispatch._file_compile_to_native.assert_not_called()


@pytest.mark.parametrize("options", [
    {"native_toolchain": "clang"}, {"native_toolchain": "gcc"},
    {"debug_info": True}, {"opt_level": "Os"},
    {"llvm_pgo_generate_dir": "profiles"}, {"pgo_use_dir": "profiles"},
])
def test_specialist_options_remain_supported(dispatch, options) -> None:
    assert dispatch.compile_to_native("source.ail", "program", **options)
    dispatch._memory_build.assert_not_called()
    dispatch._file_compile_to_native.assert_called_once()
    for name, value in options.items():
        if name != "opt_level":
            assert dispatch._file_compile_to_native.call_args.kwargs[name] == value


def test_other_platform_uses_its_existing_automatic_build(dispatch) -> None:
    dispatch.memory_link_supported = lambda: False
    assert dispatch.compile_to_native("source.ail", "program")
    dispatch._memory_build.assert_not_called()
    dispatch._file_compile_to_native.assert_called_once()


def test_unsupported_link_options_select_compatible_path(dispatch) -> None:
    dispatch._memory_build.return_value = None
    assert dispatch.compile_to_native("source.ail", "program")
    dispatch._file_compile_to_native.assert_called_once()


@pytest.mark.parametrize("error", [
    OSError("unreadable input"), ValueError("bad source"),
    RuntimeError("native failure"), SyntaxError("bad syntax"),
    ImportError("missing module"), MemoryError("allocation failed"),
])
def test_compile_errors_are_not_retried_as_another_backend(dispatch, error, capsys) -> None:
    dispatch._memory_build.side_effect = error
    assert not dispatch.compile_to_native("source.ail", "program")
    dispatch._file_compile_to_native.assert_not_called()
    assert str(error) in capsys.readouterr().out


def test_codegen_diagnostic_is_reported_without_traceback(dispatch, capsys) -> None:
    dispatch._memory_build.side_effect = dispatch.CodeGenError("unknown name")
    assert not dispatch.compile_to_native("source.ail", "program")
    assert "unknown name" in capsys.readouterr().out
    dispatch._file_compile_to_native.assert_not_called()

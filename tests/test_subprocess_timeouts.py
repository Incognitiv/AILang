from __future__ import annotations

import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

# ruff: noqa: E402



REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cli import compilation
from codegen.templates import (
    TEMPLATE_COMPILE_TIMEOUT_SECONDS,
    TemplateBlock,
    TemplateCompiler,
    TemplateError,
)


def test_mingw_vararg_symbol_normalization_keeps_jit_ir_unchanged() -> None:
    ir_text = 'target triple = "x86_64-pc-windows-msvc"\ncall i32 @"printf"()\n'
    assert compilation._normalize_mingw_vararg_symbols(ir_text) == ir_text


def test_mingw_vararg_symbol_normalization_uses_mingw_printf() -> None:
    ir_text = (
        'target triple = "x86_64-w64-windows-gnu"\n'
        'declare i32 @"printf"(i8* %".1", ...)\n'
        'call i32 (i8*, ...) @"printf"(i8* null)\n'
    )
    normalized = compilation._normalize_mingw_vararg_symbols(ir_text)
    assert '@"printf"' not in normalized
    assert '@"__mingw_printf"' in normalized


def _install_fake_fast_jit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mod = types.ModuleType("codegen.fast_jit")

    def _compile_to_ir_fast(
        source_code: str,
        source_file: str = "",
        debug: bool = False,
    ) -> str:
        _ = source_code
        _ = source_file
        _ = debug
        return 'define i64 @"main"() {\nentry:\n  ret i64 0\n}\n'

    fake_mod.compile_to_ir_fast = _compile_to_ir_fast  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "codegen.fast_jit", fake_mod)


def test_compile_to_native_release_aot_omits_debug_metadata_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_debug: list[bool] = []
    fake_mod = types.ModuleType("codegen.fast_jit")

    def _compile_to_ir_fast(
        source_code: str,
        source_file: str = "",
        debug: bool = False,
    ) -> str:
        _ = source_code
        _ = source_file
        seen_debug.append(debug)
        return 'define i64 @"main"() {\nentry:\n  ret i64 0\n}\n'

    fake_mod.compile_to_ir_fast = _compile_to_ir_fast  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "codegen.fast_jit", fake_mod)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_resolve(name: str) -> str | None:
        return r"C:\toolchain\clang.exe" if name == "clang" else None

    monkeypatch.setattr(compilation.subprocess, "run", fake_run)
    monkeypatch.setattr(compilation, "_resolve_tool", fake_resolve)
    monkeypatch.setattr(compilation, "resolve_llvm_tool", fake_resolve)
    monkeypatch.setattr(
        compilation, "same_llvm_root_tool", lambda anchor, name: fake_resolve(name)
    )

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.ail"
        out = Path(td) / "main.exe"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        assert compilation.compile_to_native(str(src), str(out), opt_level=2) is True

    assert seen_debug == [False]


def test_compile_to_native_clang_call_has_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fast_jit(monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_resolve(name: str) -> str | None:
        mapping = {
            "opt": None,
            "clang": r"C:\toolchain\clang.exe",
            "llc": None,
            "gcc": None,
        }
        return mapping.get(name)

    monkeypatch.setattr(compilation.subprocess, "run", fake_run)
    monkeypatch.setattr(compilation, "_resolve_tool", fake_resolve)
    monkeypatch.setattr(compilation, "resolve_llvm_tool", fake_resolve)
    monkeypatch.setattr(
        compilation, "same_llvm_root_tool", lambda anchor, name: fake_resolve(name)
    )

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.ail"
        out = Path(td) / "main.exe"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        assert compilation.compile_to_native(str(src), str(out), opt_level=2) is True

    clang_calls = [kwargs for cmd, kwargs in calls if cmd and "clang" in cmd[0].lower()]
    assert clang_calls, "expected clang invocation"
    assert clang_calls[0].get("timeout") == compilation.LLVM_CLANG_TIMEOUT_SECONDS


def test_compile_to_native_llc_and_link_calls_have_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fast_jit(monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_resolve(name: str) -> str | None:
        mapping = {
            "opt": None,
            "clang": None,
            "llc": r"C:\toolchain\llc.exe",
            "gcc": r"C:\toolchain\gcc.exe",
        }
        return mapping.get(name)

    monkeypatch.setattr(compilation.subprocess, "run", fake_run)
    monkeypatch.setattr(compilation, "_resolve_tool", fake_resolve)
    monkeypatch.setattr(compilation, "resolve_llvm_tool", fake_resolve)
    monkeypatch.setattr(
        compilation, "same_llvm_root_tool", lambda anchor, name: fake_resolve(name)
    )

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.ail"
        out = Path(td) / "main.exe"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        assert compilation.compile_to_native(str(src), str(out), opt_level=2) is True

    llc_calls = [kwargs for cmd, kwargs in calls if cmd and "llc" in cmd[0].lower()]
    gcc_calls = [kwargs for cmd, kwargs in calls if cmd and "gcc" in cmd[0].lower()]
    assert llc_calls, "expected llc invocation"
    assert gcc_calls, "expected gcc link invocation"
    assert llc_calls[0].get("timeout") == compilation.LLVM_LLC_TIMEOUT_SECONDS
    assert gcc_calls[0].get("timeout") == compilation.LLVM_LINK_TIMEOUT_SECONDS


def test_template_compile_uses_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        out_idx = cmd.index("-o") + 1
        out_path = Path(cmd[out_idx])
        out_path.write_text(
            'define i64 @"tpl_fn"() {\nentry:\n  ret i64 0\n}\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    compiler = TemplateCompiler()
    block = TemplateBlock("ansi_c", "int64_t tpl_fn(){ return 0; }\n")
    compiled_ir = compiler.compile_template(block)
    assert compiled_ir is not None
    assert calls, "expected subprocess.run call"
    assert calls[0].get("timeout") == TEMPLATE_COMPILE_TIMEOUT_SECONDS


def test_template_compile_timeout_raises_template_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    compiler = TemplateCompiler()
    block = TemplateBlock("ansi_c", "int64_t tpl_fn(){ return 0; }\n")
    with pytest.raises(TemplateError, match="timed out"):
        compiler.compile_template(block)

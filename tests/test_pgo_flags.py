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
from pgo import llvm_toolchain


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


def _install_fake_c_transpiler(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mod = types.ModuleType("transpiler.core")

    def _transpile_file(
        source_file: str,
        output_file: str,
        profile_enabled: bool = False,
    ) -> str:
        _ = source_file
        _ = profile_enabled
        c_text = "int main(void) { return 0; }\n"
        Path(output_file).write_text(c_text, encoding="utf-8")
        return c_text

    fake_mod.transpile_file = _transpile_file  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transpiler.core", fake_mod)


def test_pgo_compile_flags_create_generate_dir() -> None:
    with tempfile.TemporaryDirectory() as td:
        profile_dir = Path(td) / "profiles"
        flags = compilation._pgo_compile_flags(pgo_generate_dir=str(profile_dir))
        assert profile_dir.exists()
        assert flags == [f"-fprofile-generate={profile_dir.resolve()}"]


def test_pgo_compile_flags_reject_generate_and_use() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        compilation._pgo_compile_flags(pgo_generate_dir="gen", pgo_use_dir="use")


def test_same_llvm_root_tool_does_not_fallback_across_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llvm_toolchain,
        "resolve_llvm_tool",
        lambda name: str(Path(r"C:\other\bin") / f"{name}.exe"),
    )
    suffix = ".exe" if sys.platform.startswith("win") else ""
    with tempfile.TemporaryDirectory() as td:
        clang = Path(td) / f"clang{suffix}"
        clang.touch()
        assert llvm_toolchain.same_llvm_root_tool(str(clang), "llvm-profdata") is None


def test_same_llvm_root_tool_uses_anchor_directory() -> None:
    suffix = ".exe" if sys.platform.startswith("win") else ""
    with tempfile.TemporaryDirectory() as td:
        clang = Path(td) / f"clang{suffix}"
        profdata = Path(td) / f"llvm-profdata{suffix}"
        clang.touch()
        profdata.touch()
        assert llvm_toolchain.same_llvm_root_tool(str(clang), "llvm-profdata") == str(
            profdata
        )


def test_compile_to_native_clang_receives_llvm_pgo_generate_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fast_jit(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        calls.append(cmd)
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
        pgo_dir = Path(td) / "pgo"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        ok = compilation.compile_to_native(
            str(src),
            str(out),
            opt_level=3,
            llvm_pgo_generate_dir=str(pgo_dir),
        )
        assert ok is True

    clang_cmds = [cmd for cmd in calls if cmd and "clang" in cmd[0].lower()]
    assert clang_cmds
    assert f"-fprofile-generate={pgo_dir.resolve()}" in clang_cmds[0]


def test_compile_to_native_clang_receives_llvm_pgo_use_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fast_jit(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        calls.append(cmd)
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
        pgo_dir = Path(td) / "pgo"
        pgo_dir.mkdir()
        profdata = pgo_dir / "default.profdata"
        profdata.write_bytes(b"profile")
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        ok = compilation.compile_to_native(
            str(src),
            str(out),
            opt_level=3,
            llvm_pgo_use_dir=str(pgo_dir),
        )
        assert ok is True

    clang_cmds = [cmd for cmd in calls if cmd and "clang" in cmd[0].lower()]
    assert clang_cmds
    assert f"-fprofile-use={profdata.resolve()}" in clang_cmds[0]


def test_compile_to_native_can_force_llc_gcc_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fast_jit(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_resolve(name: str) -> str | None:
        mapping = {
            "clang": r"C:\toolchain\clang.exe",
            "llc": r"C:\toolchain\llc.exe",
            "opt": None,
            "gcc": r"C:\toolchain\gcc.exe",
        }
        return mapping.get(name)

    monkeypatch.setattr(compilation.subprocess, "run", fake_run)
    monkeypatch.setattr(compilation, "_resolve_tool", fake_resolve)
    monkeypatch.setattr(compilation, "resolve_llvm_tool", fake_resolve)
    monkeypatch.setattr(compilation, "same_llvm_root_tool", lambda anchor, name: None)

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.ail"
        out = Path(td) / "main.exe"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        ok = compilation.compile_to_native(
            str(src),
            str(out),
            native_toolchain="gnu",
        )
        assert ok is True

    assert not [cmd for cmd in calls if cmd and "clang" in cmd[0].lower()]
    assert [cmd for cmd in calls if cmd and "llc" in cmd[0].lower()]
    assert [cmd for cmd in calls if cmd and "gcc" in cmd[0].lower()]


def test_compile_via_c_can_force_gcc_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_c_transpiler(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_resolve(name: str) -> str | None:
        mapping = {
            "ccache": None,
            "gcc": r"C:\toolchain\gcc.exe",
            "clang": r"C:\toolchain\clang.exe",
        }
        return mapping.get(name)

    monkeypatch.setattr(compilation.subprocess, "run", fake_run)
    monkeypatch.setattr(compilation, "_resolve_tool", fake_resolve)
    monkeypatch.setattr(compilation, "resolve_llvm_tool", fake_resolve)

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.ail"
        out = Path(td) / "main.exe"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        ok = compilation.compile_via_c(
            str(src),
            str(out),
            native_toolchain="gnu",
        )
        assert ok is True

    assert [cmd for cmd in calls if cmd and "gcc" in cmd[0].lower()]
    assert not [cmd for cmd in calls if cmd and "clang" in cmd[0].lower()]


def test_compile_via_c_reports_compiler_failure_not_missing_toolchain(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_c_transpiler(monkeypatch)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr=(
                "warning: overriding module target triple\n"
                "clang: error: unable to open output file '/missing/app': No such file\n"
            ),
        )

    def fake_resolve(name: str) -> str | None:
        mapping = {
            "ccache": None,
            "gcc": r"C:\toolchain\gcc.exe",
            "clang": None,
        }
        return mapping.get(name)

    monkeypatch.setattr(compilation.subprocess, "run", fake_run)
    monkeypatch.setattr(compilation, "_resolve_tool", fake_resolve)
    monkeypatch.setattr(compilation, "resolve_llvm_tool", fake_resolve)

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "main.ail"
        out = Path(td) / "missing" / "main.exe"
        src.write_text("def main(): int\n    return 0\nend\n", encoding="utf-8")
        ok = compilation.compile_via_c(
            str(src),
            str(out),
            native_toolchain="gnu",
        )

    output = capsys.readouterr().out
    assert ok is False
    assert "C backend compilation failed" in output
    assert "unable to open output file" in output
    assert "No C compiler found" not in output

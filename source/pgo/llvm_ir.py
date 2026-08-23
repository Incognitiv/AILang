"""Hosted LLVM IR PGO helpers and capability probe."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool

LLVM_PGO_PROFDATA_NAME = "default.profdata"
LLVM_PGO_TIMEOUT_SECONDS = 60


class LLVMProfileMergeError(RuntimeError):
    """Raised when raw LLVM profiles cannot be merged."""


@dataclass(frozen=True)
class LLVMPGOProbeResult:
    """Result of a hosted LLVM IR PGO smoke probe."""

    ok: bool
    platform: str
    clang: str | None
    llvm_profdata: str | None
    work_dir: str
    target: str = ""
    profraw_count: int = 0
    profdata: str = ""
    error: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _resolve_tool(name: str) -> str | None:
    return resolve_llvm_tool(name)


def default_ailang_clang_target() -> str | None:
    """Return the target override used by AILang's LLVM AOT path."""
    if sys.platform.startswith("win"):
        return "x86_64-w64-windows-gnu"
    return None


def _target_args(target: str | None) -> list[str]:
    return ["-target", target] if target else []


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=LLVM_PGO_TIMEOUT_SECONDS,
        check=False,
    )


def merge_llvm_profraw(profile_dir: str | Path) -> Path:
    """Merge `.profraw` files in `profile_dir` into `default.profdata`."""
    profdata_tool = _resolve_tool("llvm-profdata")
    return merge_llvm_profraw_with_tool(profile_dir, llvm_profdata=profdata_tool)


def merge_llvm_profraw_with_tool(
    profile_dir: str | Path,
    *,
    llvm_profdata: str | None,
) -> Path:
    """Merge `.profraw` using the profdata tool paired with a chosen clang."""
    if llvm_profdata is None:
        raise LLVMProfileMergeError("llvm-profdata not found on PATH")
    profile_path = Path(profile_dir).resolve()
    profraw_files = sorted(profile_path.glob("*.profraw"))
    if not profraw_files:
        raise LLVMProfileMergeError(f"no .profraw files found in {profile_path}")
    output = profile_path / LLVM_PGO_PROFDATA_NAME
    result = _run(
        [llvm_profdata, "merge", *map(str, profraw_files), "-o", str(output)],
        cwd=profile_path,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise LLVMProfileMergeError(f"llvm-profdata merge failed: {details}")
    return output


def llvm_pgo_generate_flags(profile_dir: str | Path) -> list[str]:
    """Return clang flags for hosted LLVM IR PGO instrumentation."""
    profile_path = Path(profile_dir).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    return [f"-fprofile-generate={profile_path}"]


def llvm_pgo_use_flags(profile_dir: str | Path) -> list[str]:
    """Return clang flags for consuming hosted LLVM IR PGO data."""
    return llvm_pgo_use_flags_with_tool(
        profile_dir, llvm_profdata=_resolve_tool("llvm-profdata")
    )


def llvm_pgo_use_flags_with_tool(
    profile_dir: str | Path,
    *,
    llvm_profdata: str | None,
) -> list[str]:
    """Return profile-use flags, merging raw profiles with a paired tool."""
    profile_path = Path(profile_dir).resolve()
    profdata = profile_path / LLVM_PGO_PROFDATA_NAME
    if not profdata.exists():
        profdata = merge_llvm_profraw_with_tool(
            profile_path,
            llvm_profdata=llvm_profdata,
        )
    return [f"-fprofile-use={profdata}"]


def _write_probe_ir(path: Path) -> None:
    path.write_text(
        "define i32 @main() {\n" "entry:\n" "  ret i32 0\n" "}\n",
        encoding="ascii",
    )


def llvm_pgo_probe(
    work_dir: str | Path | None = None,
    *,
    target: str | None = None,
) -> LLVMPGOProbeResult:
    """Probe whether this host can run hosted LLVM IR PGO end-to-end."""
    clang = _resolve_tool("clang")
    profdata_tool = same_llvm_root_tool(clang, "llvm-profdata")
    platform_name = sys.platform
    target = default_ailang_clang_target() if target is None else target
    if clang is None or profdata_tool is None:
        return LLVMPGOProbeResult(
            ok=False,
            platform=platform_name,
            clang=clang,
            llvm_profdata=profdata_tool,
            work_dir=str(work_dir or ""),
            target=target or "",
            error="clang and llvm-profdata are both required",
        )

    temp_ctx = None
    if work_dir is None:
        temp_ctx = tempfile.TemporaryDirectory(prefix="ailang_llvm_pgo_probe_")
        root = Path(temp_ctx.name)
    else:
        root = Path(work_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
    try:
        profiles = root / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        ir_path = root / "tiny.ll"
        gen_exe = root / (
            "tiny_gen.exe" if sys.platform.startswith("win") else "tiny_gen"
        )
        use_exe = root / (
            "tiny_use.exe" if sys.platform.startswith("win") else "tiny_use"
        )
        _write_probe_ir(ir_path)

        gen = _run(
            [
                clang,
                "-O2",
                *_target_args(target),
                *llvm_pgo_generate_flags(profiles),
                str(ir_path),
                "-o",
                str(gen_exe),
            ],
            cwd=root,
        )
        if gen.returncode != 0:
            return LLVMPGOProbeResult(
                ok=False,
                platform=platform_name,
                clang=clang,
                llvm_profdata=profdata_tool,
                work_dir=str(root),
                target=target or "",
                error=(gen.stderr or gen.stdout).strip(),
            )
        run_gen = _run([str(gen_exe)], cwd=root)
        if run_gen.returncode != 0:
            return LLVMPGOProbeResult(
                ok=False,
                platform=platform_name,
                clang=clang,
                llvm_profdata=profdata_tool,
                work_dir=str(root),
                target=target or "",
                error=(run_gen.stderr or run_gen.stdout).strip(),
            )
        profraw_count = len(list(profiles.glob("*.profraw")))
        profdata = merge_llvm_profraw_with_tool(profiles, llvm_profdata=profdata_tool)
        use = _run(
            [
                clang,
                "-O2",
                *_target_args(target),
                *llvm_pgo_use_flags_with_tool(
                    profiles,
                    llvm_profdata=profdata_tool,
                ),
                str(ir_path),
                "-o",
                str(use_exe),
            ],
            cwd=root,
        )
        if use.returncode != 0:
            return LLVMPGOProbeResult(
                ok=False,
                platform=platform_name,
                clang=clang,
                llvm_profdata=profdata_tool,
                work_dir=str(root),
                target=target or "",
                profraw_count=profraw_count,
                profdata=str(profdata),
                error=(use.stderr or use.stdout).strip(),
            )
        run_use = _run([str(use_exe)], cwd=root)
        return LLVMPGOProbeResult(
            ok=run_use.returncode == 0,
            platform=platform_name,
            clang=clang,
            llvm_profdata=profdata_tool,
            work_dir=str(root),
            target=target or "",
            profraw_count=profraw_count,
            profdata=str(profdata),
            error=(
                ""
                if run_use.returncode == 0
                else (run_use.stderr or run_use.stdout).strip()
            ),
        )
    except (OSError, subprocess.TimeoutExpired, LLVMProfileMergeError) as exc:
        return LLVMPGOProbeResult(
            ok=False,
            platform=platform_name,
            clang=clang,
            llvm_profdata=profdata_tool,
            work_dir=str(root),
            target=target or "",
            error=str(exc),
        )
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()

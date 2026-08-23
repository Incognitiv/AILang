#!/usr/bin/env python3
"""Build distributable AILang artifacts (wheel/sdist and optional executables)."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYINSTALLER_ENTRYPOINT = REPO_ROOT / "source" / "cli" / "main.py"
NUITKA_ENTRYPOINT = REPO_ROOT / "ailang.py"
EXE_PACKAGES = [
    "cli",
    "lexer",
    "parser",
    "diagnostics",
    "compiler",
    "transpiler",
    "codegen",
    "runtime",
    "tools",
    "llvmlite",
]


def _run_capture(
    cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None
) -> tuple[int, str]:
    print("$ " + " ".join(cmd))
    if dry_run:
        return 0, ""
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="")
    return int(proc.returncode), stdout


def _module_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _run(cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None) -> int:
    print("$ " + " ".join(cmd))
    if dry_run:
        return 0
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False)
    return int(proc.returncode)


def _clean_dir(path: Path, *, dry_run: bool) -> None:
    if not path.exists():
        return
    print(f"clean: {path}")
    if not dry_run:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return


def _build_python_package(output_dir: Path, *, dry_run: bool) -> int:
    dist_dir = output_dir / "python_dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "build",
        "--sdist",
        "--wheel",
        "--outdir",
        str(dist_dir),
    ]
    if dry_run:
        return _run(cmd, dry_run=True)
    if not _module_installed("build"):
        print("error: python module 'build' is not installed.")
        print("install with: python -m pip install build")
        return 2
    rc = _run(cmd, dry_run=dry_run)
    if rc == 0:
        print(f"package artifacts: {dist_dir}")
    return rc


def _build_nuitka(output_dir: Path, *, onefile: bool, dry_run: bool) -> int:
    build_dir = output_dir / "nuitka"
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        str(NUITKA_ENTRYPOINT),
        "--output-dir=" + str(build_dir),
        "--assume-yes-for-downloads",
        "--remove-output",
    ]
    cmd.extend(f"--include-package={name}" for name in EXE_PACKAGES)
    cmd.extend(
        [
            "--include-module=llvmlite",
            "--include-module=llvmlite.binding",
            "--include-package-data=llvmlite",
        ]
    )
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--standalone")
    env = os.environ.copy()
    source_root = str(REPO_ROOT / "source")
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        source_root if not existing_path else source_root + os.pathsep + existing_path
    )
    if dry_run:
        return _run(cmd, dry_run=True, env=env)
    if not _module_installed("nuitka"):
        print("error: python module 'nuitka' is not installed.")
        print("install with: python -m pip install nuitka")
        return 2
    if sys.platform.startswith("linux") and shutil.which("patchelf") is None:
        print("error: 'patchelf' is required for Nuitka standalone/onefile on Linux.")
        print("install with: sudo apt install patchelf")
        return 2
    rc = _run(cmd, dry_run=dry_run, env=env)
    if rc == 0:
        print(f"nuitka artifacts: {build_dir}")
    return rc


def _build_pyinstaller(output_dir: Path, *, onefile: bool, dry_run: bool) -> int:
    dist_dir = output_dir / "pyinstaller" / "dist"
    work_dir = output_dir / "pyinstaller" / "build"
    spec_dir = output_dir / "pyinstaller" / "spec"
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "ailangc",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(REPO_ROOT / "source"),
        "--hidden-import",
        "llvmlite",
        "--hidden-import",
        "llvmlite.binding",
        "--collect-submodules",
        "llvmlite",
        "--collect-data",
        "llvmlite",
        "--collect-binaries",
        "llvmlite",
    ]
    cmd.append("--onefile" if onefile else "--onedir")
    cmd.append(str(PYINSTALLER_ENTRYPOINT))
    if dry_run:
        return _run(cmd, dry_run=True)
    if not _module_installed("PyInstaller"):
        print("error: python module 'pyinstaller' is not installed.")
        print("install with: python -m pip install pyinstaller")
        return 2
    rc = _run(cmd, dry_run=dry_run)
    if rc == 0:
        print(f"pyinstaller artifacts: {dist_dir}")
    return rc


def _build_cython(output_dir: Path, *, onefile: bool, dry_run: bool) -> int:
    del onefile  # Cython build here is always a single executable.
    build_dir = output_dir / "cython"
    build_dir.mkdir(parents=True, exist_ok=True)
    c_out = build_dir / "ailang.c"
    exe_out = build_dir / "ailangc"
    if sys.platform.startswith("win"):
        exe_out = build_dir / "ailangc.exe"
    cython_cmd = [
        sys.executable,
        "-m",
        "cython",
        "--embed",
        "-3",
        str(NUITKA_ENTRYPOINT),
        "-o",
        str(c_out),
    ]
    if dry_run:
        return _run(cython_cmd, dry_run=True)
    if not _module_installed("Cython"):
        print("error: python module 'Cython' is not installed.")
        print("install with: python -m pip install cython")
        return 2
    rc = _run(cython_cmd, dry_run=dry_run)
    if rc != 0:
        return rc
    if sys.platform.startswith("win"):
        print("error: cython executable build is not implemented on Windows yet.")
        return 2
    cfg_tool_candidates = [
        sys.executable + "-config",
        "python3-config",
        "python-config",
    ]
    cfg_text = ""
    rc_cfg = 1
    cfg_cmd_used: list[str] | None = None
    for cfg_tool in cfg_tool_candidates:
        if Path(cfg_tool).is_absolute() and not Path(cfg_tool).exists():
            continue
        if not Path(cfg_tool).is_absolute() and shutil.which(cfg_tool) is None:
            continue
        cmd = [cfg_tool, "--embed", "--cflags", "--ldflags"]
        rc_cfg, cfg_text = _run_capture(cmd, dry_run=dry_run)
        cfg_cmd_used = cmd
        if rc_cfg == 0 and cfg_text.strip():
            break
    if rc_cfg != 0 or not cfg_text.strip():
        print("error: failed to obtain Python embed flags.")
        print("tried: " + ", ".join(cfg_tool_candidates))
        return rc_cfg if rc_cfg != 0 else 2
    if cfg_cmd_used is not None:
        print("using: " + " ".join(cfg_cmd_used))
    cfg_args = cfg_text.split()
    if not cfg_args:
        print("error: failed to obtain python embed flags from python-config.")
        return 2
    gcc_cmd = ["gcc", "-O3", str(c_out), "-o", str(exe_out), *cfg_args]
    rc = _run(gcc_cmd, dry_run=dry_run)
    if rc == 0:
        print(f"cython artifacts: {build_dir}")
    return rc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        choices=["package", "nuitka", "pyinstaller", "cython", "all"],
        default="package",
        help="Build mode (default: package wheel+sdist).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "out" / "package",
        help="Artifact output root.",
    )
    p.add_argument(
        "--onefile",
        action="store_true",
        help="Use onefile mode for executable builders.",
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Delete output-dir before building.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.output_dir.resolve()
    if args.clean:
        _clean_dir(out_dir, dry_run=args.dry_run)
    out_dir.mkdir(parents=True, exist_ok=True)

    modes = (
        ["package", "nuitka", "pyinstaller", "cython"]
        if args.mode == "all"
        else [str(args.mode)]
    )
    status = 0
    for mode in modes:
        print(f"[build] mode={mode}")
        if mode == "package":
            rc = _build_python_package(out_dir, dry_run=args.dry_run)
        elif mode == "nuitka":
            rc = _build_nuitka(out_dir, onefile=args.onefile, dry_run=args.dry_run)
        elif mode == "cython":
            rc = _build_cython(out_dir, onefile=args.onefile, dry_run=args.dry_run)
        else:
            rc = _build_pyinstaller(out_dir, onefile=args.onefile, dry_run=args.dry_run)
        if rc != 0:
            status = rc
            print(f"[build] mode={mode} failed (exit={rc})")
            break
        else:
            print(f"[build] mode={mode} ok")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

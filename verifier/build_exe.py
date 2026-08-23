#!/usr/bin/env python3
"""Build single-file executable for the verifier.

Use PyInstaller in onefile mode to create a native, self-contained binary.
Run on Windows to produce verifier.exe; run on Linux/WSL to produce verifier.
No extra folders or wheel caches are emitted—just the single binary in dist/.

Usage:
    python build_exe.py              # One-file binary for current platform
    python build_exe.py --onedir     # Optional folder build (faster startup)
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any, cast


def check_pyinstaller() -> bool:
    """Check if PyInstaller is available."""
    spec = importlib.util.find_spec("PyInstaller")
    return spec is not None


def verify_no_compiled_extensions(pkg_name: str) -> bool:
    """Verify a package has no compiled extensions (.so/.pyd files)."""
    import glob

    try:
        spec = importlib.util.find_spec(pkg_name)
        if spec is None or spec.origin is None:
            return True
        pkg_dir = Path(spec.origin).parent
        so_files = list(glob.glob(str(pkg_dir / "**/*.so"), recursive=True))
        pyd_files = list(glob.glob(str(pkg_dir / "**/*.pyd"), recursive=True))
        compiled = so_files + pyd_files
        if compiled:
            print(f"  WARNING: {pkg_name} still has compiled extensions:")
            for f in compiled[:5]:
                print(f"    - {f}")
            return False
        return True
    except (ImportError, AttributeError):
        return True


def _run_pip_install(pkg: str) -> tuple:
    """Run pip install for a package. Returns (success, error_msg).

    Build script intentionally invokes pip. Args are constructed from
    sys.executable + literal flags + the validated package name -- no
    shell, no untrusted input.
    """
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-binary",
            pkg,
            pkg,
        ],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        return False, result.stderr
    return True, ""


def ensure_pure_python_packages() -> bool:
    """Reinstall mypy, black, and isort as pure Python (no mypyc binaries).

    This is required for PyInstaller bundling because mypyc-compiled
    extensions have hashed module names that can't be bundled properly.
    """
    packages = ["mypy", "black", "isort"]
    print("Ensuring pure-Python versions of mypyc-compiled packages...")

    for pkg in packages:
        print(f"  Reinstalling {pkg} (pure Python, no binaries)...")
        success, error = _run_pip_install(pkg)
        if not success:
            print(f"  WARNING: Failed to reinstall {pkg}: {error}")
            return False
        print(f"  {pkg} reinstalled successfully")
        if not verify_no_compiled_extensions(pkg):
            print(f"  WARNING: {pkg} may still have compiled extensions")

    print()
    return True


def get_hidden_imports() -> list[str]:
    """Return list of hidden imports for PyInstaller."""
    return [
        "pyflakes",
        "pyflakes.api",
        "pyflakes.checker",
        "pylint",
        "pylint.lint",
        "pylint.reporters",
        "pylint.reporters.text",
        "astroid",
        "mypy",
        "mypy.api",
        "bandit",
        "bandit.core",
        "bandit.core.node_visitor",
        "radon",
        "radon.complexity",
        "radon.metrics",
        "black",
        "isort",
        "isort.main",
        "ruff",
        "vulture",
        "vulture.core",
        "cohesion",
        "cohesion.parser",
        "pip_audit",
        "detect_secrets",
        "detect_secrets.core",
        "detect_secrets.core.scan",
        "click",
        "pathspec",
        "tomlkit",
        "platformdirs",
        "dill",
        "mccabe",
        "stevedore",
        "typing_extensions",
        "tools",
        "tools.common",
        "tools.complexity",
        "tools.formatters",
        "tools.linters",
        "tools.quality",
        "tools.security",
    ]


def find_mypy_typeshed() -> Path | None:
    """Find mypy's typeshed directory."""
    try:
        import mypy

        mypy_dir = Path(mypy.__file__).parent
        typeshed = mypy_dir / "typeshed"
        if typeshed.exists() and typeshed.is_dir():
            return typeshed
    except ImportError:
        pass
    return None


def build_args(
    source_dir: Path, verifier_main: Path, exe_base: str, onefile: bool
) -> list[str]:
    """Build PyInstaller command arguments."""
    args = [
        str(verifier_main),
        f"--name={exe_base}",
        "--noconfirm",
        "--clean",
    ]

    if onefile:
        args.append("--onefile")
    else:
        args.append("--onedir")

    for imp in get_hidden_imports():
        args.extend(["--hidden-import", imp])

    # Use custom hooks directory to properly bundle mypy's mypyc binaries
    hooks_dir = source_dir / "pyinstaller_hooks"
    if hooks_dir.exists():
        args.extend(["--additional-hooks-dir", str(hooks_dir)])

    # Collect mypy submodules
    args.extend(
        [
            "--collect-submodules",
            "mypy",
            "--collect-submodules",
            "mypy_extensions",
        ]
    )

    # Explicitly add mypy's typeshed directory - critical for type checking
    typeshed_path = find_mypy_typeshed()
    if typeshed_path:
        print(f"  Found typeshed: {typeshed_path}")
        args.extend(["--add-data", f"{typeshed_path}{os.pathsep}mypy/typeshed"])
    else:
        print("  WARNING: Could not find mypy typeshed directory")

    tools_dir = source_dir / "tools"
    if tools_dir.exists():
        args.extend(["--add-data", f"{tools_dir}{os.pathsep}tools"])

    pylintrc = source_dir / ".pylintrc"
    if pylintrc.exists():
        args.extend(["--add-data", f"{pylintrc}{os.pathsep}."])

    pyproject = source_dir / "pyproject.toml"
    if pyproject.exists():
        args.extend(["--add-data", f"{pyproject}{os.pathsep}."])

    return args


def clean_build_dirs(source_dir: Path) -> None:
    """Remove previous build artifacts (keep dist to allow multiple platforms)."""
    build_folder = source_dir / "build"
    if build_folder.exists():
        shutil.rmtree(build_folder)

    spec_file = source_dir / "verifier.spec"
    if spec_file.exists():
        spec_file.unlink()


def report_result(source_dir: Path, exe_base: str, onefile: bool) -> bool:
    """Report build result and return success status."""
    if onefile:
        exe_name = f"{exe_base}.exe" if sys.platform == "win32" else exe_base
        exe_path = source_dir / "dist" / exe_name
    else:
        exe_path = source_dir / "dist" / exe_base

    if not exe_path.exists():
        print("ERROR: Executable not found after build")
        return False

    if exe_path.is_file():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
    else:
        total = sum(f.stat().st_size for f in exe_path.rglob("*") if f.is_file())
        size_mb = total / (1024 * 1024)

    print()
    print("=" * 60)
    print("BUILD SUCCESSFUL")
    print("=" * 60)
    print(f"  Output: {exe_path}")
    print(f"  Size: {size_mb:.1f} MB")
    print()
    print("Usage:")
    if onefile:
        print(f"  {exe_path.name} <file.py>")
        print(f"  {exe_path.name} -d <directory>")
    else:
        exe_file = f"{exe_base}.exe" if sys.platform == "win32" else exe_base
        print(f"  {exe_path / exe_file} <file.py>")
    return True


def build_executable(exe_base: str, onefile: bool = True) -> bool:
    """Build the executable using PyInstaller."""
    try:
        pyinstaller_main = cast(Any, importlib.import_module("PyInstaller.__main__"))
    except ImportError:
        print("ERROR: PyInstaller not installed")
        print("Install with: python -m pip install --user pyinstaller")
        return False

    source_dir = Path(__file__).parent
    verifier_main = source_dir / "core.py"

    if not verifier_main.exists():
        print(f"ERROR: core.py not found in {source_dir}")
        return False

    clean_build_dirs(source_dir)

    args = build_args(source_dir, verifier_main, exe_base, onefile)

    print("Building executable...")
    print(f"  Mode: {'single file' if onefile else 'folder'}")
    print(f"  Source: {verifier_main}")
    print()

    try:
        pyinstaller_main.run(args)
    except SystemExit as exc:
        if exc.code != 0:
            print(f"\nBuild failed with code {exc.code}")
            return False

    # Clean intermediate build directory to keep tree tidy
    build_folder = source_dir / "build"
    if build_folder.exists():
        shutil.rmtree(build_folder)

    return report_result(source_dir, exe_base, onefile)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Build verifier executable")
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build as folder instead of single file (faster startup)",
    )
    parser.add_argument(
        "--name",
        help="Executable base name (default: verifier_<platform>)",
    )
    parser.add_argument(
        "--skip-reinstall",
        action="store_true",
        help="Skip reinstalling mypy/black as pure Python (use if already done)",
    )
    args = parser.parse_args()

    if not check_pyinstaller():
        print("PyInstaller not found. Please install it:")
        print("  python -m pip install --user pyinstaller")
        return 1

    # Ensure pure-Python versions of mypyc-compiled packages
    if not args.skip_reinstall:
        if not ensure_pure_python_packages():
            print("WARNING: Could not ensure pure-Python packages.")
            print("Build may fail if mypyc binaries are present.")
            print()
    else:
        print("Skipping package reinstall (--skip-reinstall flag used)")
        print()

    exe_base = args.name or (
        "verifier_windows" if sys.platform == "win32" else "verifier_linux"
    )

    success = build_executable(exe_base=exe_base, onefile=not args.onedir)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build offline packages for Windows and Linux.

Creates self-contained verifier distributions with all dependencies bundled.
Copies installed packages from current environment - no subprocess needed.

Usage:
    python build_offline.py          # Build for current platform
    python build_offline.py --all    # Build for both platforms
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import shutil
import sys
from pathlib import Path

# Package names to copy (top-level import names)
PACKAGE_NAMES = [
    "pyflakes",
    "pylint",
    "astroid",
    "dill",
    "isort",
    "mccabe",
    "platformdirs",
    "tomlkit",
    "mypy",
    "mypy_extensions",
    "typing_extensions",
    "bandit",
    "pbr",
    "stevedore",
    "pyyaml",
    "radon",
    "black",
    "click",
    "pathspec",
    "ruff",
    "vulture",
    "cohesion",
    "pip_audit",
    "detect_secrets",
]

VERIFIER_FILES = [
    "__init__.py",
    "cache.py",
    "tool_runners.py",
    "verifier.py",
    "verifier_report.py",
    ".pylintrc",
    "pyproject.toml",
]


def find_package_path(package_name: str) -> Path | None:
    """Find the installation path of a package."""
    try:
        spec = importlib.util.find_spec(package_name)
        if spec and spec.origin:
            origin = Path(spec.origin)
            if origin.name == "__init__.py":
                return origin.parent
            return origin
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
    return None


def copy_packages(dest: Path) -> int:
    """Copy installed packages to destination."""
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0

    for pkg in PACKAGE_NAMES:
        pkg_path = find_package_path(pkg)
        if not pkg_path:
            print(f"  Skipped (not found): {pkg}")
            continue

        if pkg_path.is_dir():
            dest_path = dest / pkg_path.name
            if not dest_path.exists():
                shutil.copytree(pkg_path, dest_path, dirs_exist_ok=True)
                print(f"  Copied: {pkg_path.name}/")
                copied += 1
        elif pkg_path.is_file():
            dest_path = dest / pkg_path.name
            if not dest_path.exists():
                shutil.copy2(pkg_path, dest_path)
                print(f"  Copied: {pkg_path.name}")
                copied += 1

    return copied


def copy_verifier_files(source: Path, dest: Path) -> None:
    """Copy verifier source files."""
    dest.mkdir(parents=True, exist_ok=True)

    for filename in VERIFIER_FILES:
        src_file = source / filename
        if src_file.exists():
            shutil.copy2(src_file, dest / filename)
            print(f"  Copied: {filename}")

    # Copy tools package
    tools_src = source / "tools"
    tools_dest = dest / "tools"
    if tools_src.exists():
        shutil.copytree(tools_src, tools_dest, dirs_exist_ok=True)
        print("  Copied: tools/")


def create_windows_launcher(dest: Path) -> None:
    """Create Windows batch launcher."""
    launcher = dest / "run_verifier.bat"
    launcher.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        'set "SCRIPT_DIR=%~dp0"\r\n'
        'set "PYTHONPATH=%SCRIPT_DIR%vendor;%SCRIPT_DIR%verifier;%PYTHONPATH%"\r\n'
        'python "%SCRIPT_DIR%verifier\\verifier.py" %*\r\n',
        encoding="utf-8",
    )
    print("  Created: run_verifier.bat")


def create_linux_launcher(dest: Path) -> None:
    """Create Linux shell launcher."""
    launcher = dest / "run_verifier.sh"
    launcher.write_text(
        "#!/bin/bash\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'export PYTHONPATH="$SCRIPT_DIR/vendor:$SCRIPT_DIR/verifier:$PYTHONPATH"\n'
        "\n"
        "if command -v python3 &> /dev/null; then\n"
        '    exec python3 "$SCRIPT_DIR/verifier/verifier.py" "$@"\n'
        "elif command -v python &> /dev/null; then\n"
        '    exec python "$SCRIPT_DIR/verifier/verifier.py" "$@"\n'
        "else\n"
        '    echo "ERROR: Python not found"\n'
        "    exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    print("  Created: run_verifier.sh")


def create_readme(dest: Path, platform_name: str) -> None:
    """Create README for the offline package."""
    readme = dest / "README.txt"
    launcher = "run_verifier.bat" if "Windows" in platform_name else "./run_verifier.sh"
    content = f"""AILang Code Verifier - {platform_name} Offline Package
==================================================

Self-contained verifier with all dependencies bundled.
No internet connection required.

Requirements:
    Python 3.8+ (must be installed on the system)

Usage:
    {launcher} <file.py> [options]
    {launcher} -d <directory> [options]

Options:
    -d, --directory DIR   Verify all Python files in directory
    --preset PRESET       Use 'strict' or 'normal' preset
    --json                Output results as JSON
    --no-cache            Disable result caching

Examples:
    {launcher} mycode.py
    {launcher} -d src/
"""
    readme.write_text(content, encoding="utf-8")
    print("  Created: README.txt")


def build_package(source: Path, output_name: str, platform_name: str) -> bool:
    """Build offline package for current platform."""
    print(f"\n{'=' * 60}")
    print(f"Building {platform_name} offline package...")
    print("=" * 60)

    output_dir = source / output_name
    if output_dir.exists():
        print(f"  Removing existing {output_name}...")
        shutil.rmtree(output_dir)

    output_dir.mkdir()
    vendor_dir = output_dir / "vendor"
    verifier_dir = output_dir / "verifier"

    # Copy installed packages
    print("  Copying installed packages...")
    copied = copy_packages(vendor_dir)
    print(f"  Copied {copied} packages")

    # Copy verifier files
    copy_verifier_files(source, verifier_dir)

    # Create launcher
    if "Windows" in platform_name:
        create_windows_launcher(output_dir)
    else:
        create_linux_launcher(output_dir)

    # Create README
    create_readme(output_dir, platform_name)

    # Calculate size
    total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)

    print(f"\n  Package created: {output_dir}")
    print(f"  Total size: {size_mb:.1f} MB")
    return True


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Build offline verifier packages")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Build for both Windows and Linux",
    )
    args = parser.parse_args()

    source_dir = Path(__file__).parent
    current_platform = platform.system()

    if args.all:
        success = build_package(source_dir, "verifier_offline_windows", "Windows")
        success = (
            build_package(source_dir, "verifier_offline_linux", "Linux") and success
        )
    else:
        output_name = f"verifier_offline_{current_platform.lower()}"
        success = build_package(source_dir, output_name, current_platform)

    if success:
        print("\n" + "=" * 60)
        print("BUILD COMPLETE")
        print("=" * 60)
        print("\nTo distribute:")
        print("  1. Zip the verifier_offline_* folder")
        print("  2. Transfer to target machine")
        print("  3. Unzip and run the launcher script")
    else:
        print("\nBuild completed with warnings")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

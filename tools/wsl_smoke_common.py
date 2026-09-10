#!/usr/bin/env python3
"""Shared WSL hop used by sanitizer and Valgrind smoke tools."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path


def run_in_wsl(
    *,
    repo_root: Path,
    tool_path: str,
    forwarded_args: list[str],
    label: str,
) -> int:
    """Run a repository smoke tool inside WSL and propagate its exit code."""
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if wsl is None:
        print(f"{label}: wsl not found")
        return 2

    path_proc = subprocess.run(
        [wsl, "wslpath", "-a", repo_root.as_posix()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if path_proc.returncode != 0:
        print(path_proc.stderr.strip() or f"{label}: wslpath failed")
        return 2

    command_parts = ["python3", tool_path, *forwarded_args]
    command = "cd " + shlex.quote(path_proc.stdout.strip()) + " && " + " ".join(
        shlex.quote(part) for part in command_parts
    )
    return int(subprocess.run([wsl, "bash", "-lc", command], check=False).returncode)

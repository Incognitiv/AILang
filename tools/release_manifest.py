#!/usr/bin/env python3
"""Emit reproducible release manifest (hashes + toolchain metadata)."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool

DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _first_line(text: str) -> str:
    s = (text or "").strip()
    return s.splitlines()[0] if s else ""


def _tool_version(tool: str, args: list[str] | None = None) -> dict[str, Any]:
    exe = shutil.which(tool)
    return _tool_version_from_path(exe, args)


def _tool_version_from_path(
    exe: str | None, args: list[str] | None = None
) -> dict[str, Any]:
    if exe is None:
        return {"available": False, "path": None, "version": None}
    cmd = [exe] + (args or ["--version"])
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return {
            "available": True,
            "path": exe,
            "version": _first_line(out),
            "returncode": int(proc.returncode),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "path": exe, "version": f"error: {exc}"}


def _collect_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    files: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file():
            files.append(p)
    files.sort(key=lambda v: str(v).lower())
    return files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--artifact-root",
        action="append",
        default=[],
        help="Artifact directory to hash (repeatable).",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "release_manifest.json",
        help="Manifest JSON output path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "release_manifest.md",
        help="Manifest markdown output path.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    roots = [Path(p).resolve() for p in args.artifact_root]
    if not roots:
        roots = [
            REPO_ROOT / "out" / "package",
            REPO_ROOT / "out" / "package_wsl",
            REPO_ROOT / "out" / "releases",
        ]

    files_rows: list[dict[str, Any]] = []
    for root in roots:
        for file_path in _collect_files(root):
            rel = str(file_path.relative_to(REPO_ROOT)).replace("\\", "/")
            st = file_path.stat()
            files_rows.append(
                {
                    "path": rel,
                    "size_bytes": int(st.st_size),
                    "mtime_unix": int(st.st_mtime),
                    "sha256": _sha256(file_path),
                }
            )

    tools = {
        "python": {
            "available": True,
            "path": sys.executable,
            "version": platform.python_version(),
        },
        "gcc": _tool_version("gcc"),
        "clang": _tool_version("clang"),
        "llc": _tool_version("llc"),
        "ailang_llvm_clang": _tool_version_from_path(resolve_llvm_tool("clang")),
        "ailang_llvm_profdata": _tool_version_from_path(
            same_llvm_root_tool(resolve_llvm_tool("clang"), "llvm-profdata")
        ),
        "rustc": _tool_version("rustc"),
        "nuitka": _tool_version("nuitka", ["--version"]),
        "pyinstaller": _tool_version("pyinstaller", ["--version"]),
        "cython": _tool_version("cython", ["--version"]),
    }

    git_meta: dict[str, Any] = {"available": False}
    git = shutil.which("git")
    if git is not None:
        try:
            proc = subprocess.run(
                [git, "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if proc.returncode == 0:
                git_meta = {
                    "available": True,
                    "commit_short": (proc.stdout or "").strip(),
                }
        except (OSError, subprocess.TimeoutExpired):
            pass

    payload: dict[str, Any] = {
        "generated_human": time.strftime(DATE_HUMAN_FMT),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": platform.platform(),
        "artifact_roots": [str(r) for r in roots],
        "artifact_count": len(files_rows),
        "artifacts": files_rows,
        "toolchain": tools,
        "git": git_meta,
    }

    out_json = args.output_json.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Release Manifest",
        "",
        f"- Date: {payload['generated_human']}",
        f"- Platform: `{payload['platform']}`",
        f"- Artifact roots: `{', '.join(str(r) for r in roots)}`",
        f"- Artifact count: `{len(files_rows)}`",
        "",
        "## Toolchain",
        "",
    ]
    for name in (
        "python",
        "gcc",
        "clang",
        "llc",
        "ailang_llvm_clang",
        "ailang_llvm_profdata",
        "rustc",
        "nuitka",
        "pyinstaller",
        "cython",
    ):
        row = tools[name]
        lines.append(
            f"- `{name}`: available=`{row.get('available')}`, "
            f"version=`{row.get('version')}`"
        )

    lines.extend(
        [
            "",
            "## Artifact Hashes",
            "",
            "| Path | Size (B) | SHA256 |",
            "| --- | ---: | --- |",
        ]
    )
    for row in files_rows:
        lines.append(f"| `{row['path']}` | `{row['size_bytes']}` | `{row['sha256']}` |")

    lines.append("")
    lines.append(f"- JSON: `{out_json}`")

    out_md = args.output_md.resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"json: {out_json}")
    print(f"md: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

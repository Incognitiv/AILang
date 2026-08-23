#!/usr/bin/env python3
"""Build split release artifacts (source/windows/linux) + checksum file."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


def _read_version() -> str:
    version_py = REPO_ROOT / "source" / "version.py"
    spec = importlib.util.spec_from_file_location("ailang_version", version_py)
    if spec is None or spec.loader is None:
        return "1.0.0"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = getattr(module, "__version__", "1.0.0")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "1.0.0"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _looks_executable(path: Path, st_mode: int) -> bool:
    if st_mode & stat.S_IXUSR:
        return True
    name = path.name.lower()
    return name in {"ailangc", "ailang.bin", "main.bin", "ailangc.exe"}


def _iter_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        if not p.exists():
            continue
        if p.is_file():
            rp = p.resolve()
            if rp not in seen:
                out.append(rp)
                seen.add(rp)
            continue
        for sub in p.rglob("*"):
            if sub.is_file():
                rp = sub.resolve()
                if rp not in seen:
                    out.append(rp)
                    seen.add(rp)
    out.sort(key=lambda v: str(v).lower())
    return out


def _write_zip(
    archive_path: Path, files: list[Path], *, base_paths: list[Path], prefix: str
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        for file_path in files:
            arc_rel = None
            for base in base_paths:
                base_resolved = base.resolve()
                try:
                    rel = file_path.relative_to(base_resolved)
                    arc_rel = Path(base_resolved.name) / rel
                    break
                except ValueError:
                    continue
            if arc_rel is None:
                arc_rel = Path(file_path.name)
            arc_name = str(Path(prefix) / arc_rel).replace("\\", "/")
            st = file_path.stat()
            mode = stat.S_IFREG | (stat.S_IMODE(st.st_mode) or 0o644)
            if _looks_executable(file_path, st.st_mode):
                mode = stat.S_IFREG | 0o755
            zi = zipfile.ZipInfo(arc_name)
            zi.create_system = 3
            zi.external_attr = mode << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.date_time = time.localtime(st.st_mtime)[:6]
            data = file_path.read_bytes()
            zf.writestr(zi, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _write_targz(
    archive_path: Path, files: list[Path], *, base_paths: list[Path], prefix: str
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tf:
        for file_path in files:
            arc_rel = None
            for base in base_paths:
                base_resolved = base.resolve()
                try:
                    rel = file_path.relative_to(base_resolved)
                    arc_rel = Path(base_resolved.name) / rel
                    break
                except ValueError:
                    continue
            if arc_rel is None:
                arc_rel = Path(file_path.name)
            arc_name = str(Path(prefix) / arc_rel).replace("\\", "/")
            info = tf.gettarinfo(str(file_path), arcname=arc_name)
            if info is None:
                continue
            if _looks_executable(file_path, file_path.stat().st_mode):
                info.mode = 0o755
            with file_path.open("rb") as f:
                tf.addfile(info, f)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "out" / "releases",
        help="Release output directory.",
    )
    p.add_argument(
        "--version",
        type=str,
        default=None,
        help="Release version (default: source/version.py).",
    )
    p.add_argument(
        "--windows-root",
        type=Path,
        default=REPO_ROOT / "out" / "package",
        help="Windows package root.",
    )
    p.add_argument(
        "--linux-root",
        type=Path,
        default=REPO_ROOT / "out" / "package_wsl",
        help="Linux/WSL package root.",
    )
    p.add_argument(
        "--require-targets",
        action="store_true",
        default=False,
        help="Fail if windows/linux package roots are missing.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    version = args.version.strip() if args.version else _read_version()
    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    source_zip = out_dir / f"AILang-{version}-source.zip"
    windows_zip = out_dir / f"AILang-{version}-windows-x64.zip"
    linux_tgz = out_dir / f"AILang-{version}-linux-x64.tar.gz"
    checksums_txt = out_dir / f"AILang-{version}-checksums.sha256"
    summary_md = out_dir / f"AILang-{version}-artifacts.md"

    # 1) Source archive
    make_clean_zip = REPO_ROOT / "tools" / "make_clean_zip.py"
    source_cmd = [
        sys.executable,
        str(make_clean_zip),
        "--exclude-archived",
        "--output",
        str(source_zip),
    ]
    print("$ " + " ".join(source_cmd))
    source_proc = subprocess.run(source_cmd, cwd=REPO_ROOT, check=False)
    if source_proc.returncode != 0:
        print("error: source archive creation failed")
        return int(source_proc.returncode)

    # 2) Windows binary archive
    win_roots = [
        args.windows_root.resolve() / "pyinstaller" / "dist",
        args.windows_root.resolve() / "nuitka",
        args.windows_root.resolve() / "python_dist",
    ]
    win_docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "SUPPORT_MATRIX.md",
        REPO_ROOT / "LANGUAGE_FREEZE.md",
    ]
    win_missing = [p for p in win_roots if not p.exists()]
    if args.require_targets and win_missing:
        print("error: missing windows package roots:")
        for p in win_missing:
            print(f"  - {p}")
        return 2
    win_files = _iter_files([*win_roots, *win_docs])
    if win_files:
        _write_zip(
            windows_zip,
            win_files,
            base_paths=[p for p in [*win_roots, REPO_ROOT] if p.exists()],
            prefix=f"AILang-{version}-windows-x64",
        )
    else:
        print("warning: windows archive skipped (no files found)")

    # 3) Linux/WSL binary archive
    linux_roots = [
        args.linux_root.resolve() / "pyinstaller" / "dist",
        args.linux_root.resolve() / "nuitka",
        args.linux_root.resolve() / "python_dist",
    ]
    linux_missing = [p for p in linux_roots if not p.exists()]
    if args.require_targets and linux_missing:
        print("error: missing linux package roots:")
        for p in linux_missing:
            print(f"  - {p}")
        return 2
    linux_files = _iter_files([*linux_roots, *win_docs])
    if linux_files:
        _write_targz(
            linux_tgz,
            linux_files,
            base_paths=[p for p in [*linux_roots, REPO_ROOT] if p.exists()],
            prefix=f"AILang-{version}-linux-x64",
        )
    else:
        print("warning: linux archive skipped (no files found)")

    created: list[Path] = [source_zip]
    if windows_zip.exists():
        created.append(windows_zip)
    if linux_tgz.exists():
        created.append(linux_tgz)

    checksum_lines: list[str] = []
    for artifact in created:
        checksum_lines.append(f"{_sha256(artifact)}  {artifact.name}")
    checksums_txt.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    lines = [
        f"# AILang {version} Release Artifacts",
        "",
        f"- Date: {time.strftime(DATE_HUMAN_FMT)}",
        f"- Output dir: `{out_dir}`",
        "",
        "## Artifacts",
        "",
        "| Artifact | Size (bytes) |",
        "| --- | ---: |",
    ]
    for artifact in created:
        lines.append(f"| `{artifact.name}` | `{artifact.stat().st_size}` |")
    lines.extend(
        [
            "",
            f"- Checksums: `{checksums_txt.name}`",
        ]
    )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"created: {source_zip}")
    if windows_zip.exists():
        print(f"created: {windows_zip}")
    if linux_tgz.exists():
        print(f"created: {linux_tgz}")
    print(f"created: {checksums_txt}")
    print(f"created: {summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

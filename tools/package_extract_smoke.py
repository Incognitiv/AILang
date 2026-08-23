#!/usr/bin/env python3
"""Validate packaged layout after extraction (Linux/Windows path sets)."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"
DEFAULT_SAMPLE = REPO_ROOT / "benchmarks" / "ailang" / "fib_mix.ail"


def _run(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return int(proc.returncode), out
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def _extract_archive(archive: Path, dst: Path) -> Path:
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive, "r") as zf:
            for member in zf.infolist():
                member_path = (dst / member.filename).resolve()
                if (
                    dst.resolve() not in member_path.parents
                    and member_path != dst.resolve()
                ):
                    raise ValueError(f"unsafe zip entry path: {member.filename}")
            zf.extractall(dst)
        return dst
    if archive.suffix.lower() in {".tgz", ".gz", ".xz"} or archive.name.endswith(
        ".tar.gz"
    ):
        with tarfile.open(archive, "r:*") as tf:
            for member in tf.getmembers():
                member_path = (dst / member.name).resolve()
                if (
                    dst.resolve() not in member_path.parents
                    and member_path != dst.resolve()
                ):
                    raise ValueError(f"unsafe tar entry path: {member.name}")
            # Python 3.14 defaults to `filter="data"`; pass it explicitly
            # when available for older runtimes.
            try:
                tf.extractall(dst, filter="data")
            except TypeError:
                tf.extractall(dst)
        return dst
    raise ValueError(f"unsupported archive format: {archive}")


def _candidate_patterns(target_platform: str) -> dict[str, list[str]]:
    if target_platform == "windows":
        return {
            "pyinstaller": [
                "out/package/pyinstaller/dist/ailangc.exe",
                "out/package/pyinstaller/dist/ailangc/ailangc.exe",
                "out/package_wsl/pyinstaller/dist/ailangc.exe",
                "out/package_wsl/pyinstaller/dist/ailangc/ailangc.exe",
                "packaged_artifacts/pyinstaller/dist/ailangc.exe",
                "packaged_artifacts/pyinstaller/dist/ailangc/ailangc.exe",
                "AILang-*-windows-x64/dist/ailangc.exe",
                "AILang-*-windows-x64/dist/ailangc/ailangc.exe",
                "**/dist/ailangc.exe",
                "**/dist/ailangc/ailangc.exe",
            ],
            "nuitka": [
                "out/package/nuitka/ailang.exe",
                "out/package/nuitka/ailang.dist/ailang.exe",
                "out/package_wsl/nuitka/ailang.exe",
                "out/package_wsl/nuitka/ailang.dist/ailang.exe",
                "packaged_artifacts/nuitka/ailang.exe",
                "packaged_artifacts/nuitka/ailang.dist/ailang.exe",
                "AILang-*-windows-x64/nuitka/ailang.exe",
                "AILang-*-windows-x64/nuitka/ailang.dist/ailang.exe",
                "**/nuitka/ailang.exe",
                "**/nuitka/ailang.dist/ailang.exe",
            ],
            "cython": [
                "out/package/cython/ailangc.exe",
                "out/package_wsl/cython/ailangc.exe",
                "packaged_artifacts/cython/ailangc.exe",
                "AILang-*-windows-x64/cython/ailangc.exe",
                "**/cython/ailangc.exe",
            ],
        }
    return {
        "pyinstaller": [
            "out/package/pyinstaller/dist/ailangc",
            "out/package/pyinstaller/dist/ailangc/ailangc",
            "out/package_wsl/pyinstaller/dist/ailangc",
            "out/package_wsl/pyinstaller/dist/ailangc/ailangc",
            "packaged_artifacts/pyinstaller/dist/ailangc",
            "packaged_artifacts/pyinstaller/dist/ailangc/ailangc",
            "AILang-*-linux-x64/dist/ailangc",
            "AILang-*-linux-x64/dist/ailangc/ailangc",
            "**/dist/ailangc",
            "**/dist/ailangc/ailangc",
        ],
        "nuitka": [
            "out/package/nuitka/ailang.bin",
            "out/package/nuitka/ailang.dist/ailang.bin",
            "out/package_wsl/nuitka/ailang.bin",
            "out/package_wsl/nuitka/ailang.dist/ailang.bin",
            "packaged_artifacts/nuitka/ailang.bin",
            "packaged_artifacts/nuitka/ailang.dist/ailang.bin",
            "AILang-*-linux-x64/nuitka/ailang.bin",
            "AILang-*-linux-x64/nuitka/ailang.dist/ailang.bin",
            "**/nuitka/ailang.bin",
            "**/nuitka/ailang.dist/ailang.bin",
        ],
        "cython": [
            "out/package/cython/ailangc",
            "out/package_wsl/cython/ailangc",
            "packaged_artifacts/cython/ailangc",
            "AILang-*-linux-x64/cython/ailangc",
            "**/cython/ailangc",
        ],
    }


def _find_first_matching_file(root: Path, patterns: list[str]) -> Path | None:
    for pat in patterns:
        candidates = sorted(
            (p for p in root.glob(pat) if p.exists() and p.is_file()),
            key=lambda p: str(p).lower(),
        )
        if candidates:
            return candidates[0]
    return None


def _probe_root(
    root: Path,
    *,
    target_platform: str,
    timeout_s: int,
    sample_file: Path,
) -> tuple[bool, dict[str, Any], list[str]]:
    host_platform = "windows" if os.name == "nt" else "linux"
    can_execute = host_platform == target_platform
    candidates = _candidate_patterns(target_platform)

    log: list[str] = [f"root: {root}"]
    variants: dict[str, Any] = {}
    required = {"pyinstaller", "nuitka"}
    overall_ok = True

    for variant, relpaths in candidates.items():
        found_path = _find_first_matching_file(root, relpaths)
        row: dict[str, Any] = {
            "found": found_path is not None,
            "path": str(found_path) if found_path else None,
            "host_can_execute": can_execute,
            "executable_bit": None,
            "version_rc": None,
            "help_rc": None,
            "check_rc": None,
            "ok": False,
        }
        if found_path is None:
            log.append(f"  {variant}: missing")
            if variant in required:
                overall_ok = False
            variants[variant] = row
            continue

        log.append(f"  {variant}: found at {found_path}")
        if target_platform == "linux":
            # Linux release archives should preserve executable mode.
            mode_exec = bool(found_path.stat().st_mode & stat.S_IXUSR)
            row["executable_bit"] = mode_exec
            log.append(f"    executable_bit: {mode_exec}")
            if not mode_exec:
                overall_ok = False

        if can_execute:
            if target_platform == "linux":
                found_path.chmod(0o755)
            rc_ver, out_ver = _run([str(found_path), "--version"], root, timeout_s)
            rc_help, out_help = _run([str(found_path), "--help"], root, timeout_s)
            row["version_rc"] = rc_ver
            row["help_rc"] = rc_help
            if sample_file.exists():
                sample_copy = root / "fib_mix.ail"
                sample_copy.write_text(
                    sample_file.read_text(encoding="utf-8"), encoding="utf-8"
                )
                rc_check, out_check = _run(
                    [str(found_path), str(sample_copy), "--check"], root, timeout_s
                )
                row["check_rc"] = rc_check
                log.append(f"    check_rc: {rc_check}")
                if out_check.strip():
                    log.append(
                        f"    check_out_tail: {out_check.strip().splitlines()[-1]}"
                    )
            else:
                rc_check = 0
                row["check_rc"] = 0
            if out_ver.strip():
                log.append(f"    version_out_tail: {out_ver.strip().splitlines()[-1]}")
            if out_help.strip():
                log.append(f"    help_out_tail: {out_help.strip().splitlines()[-1]}")
            row["ok"] = rc_ver == 0 and rc_help == 0 and rc_check == 0
        else:
            row["ok"] = True

        if variant in required and not row["ok"]:
            overall_ok = False
        variants[variant] = row

    payload = {
        "root": str(root),
        "target_platform": target_platform,
        "host_platform": host_platform,
        "variants": variants,
        "overall_ok": overall_ok,
    }
    return overall_ok, payload, log


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--archive",
        action="append",
        default=[],
        help="Release archive path to extract and validate (repeatable).",
    )
    p.add_argument(
        "--extract-root",
        type=Path,
        default=REPO_ROOT,
        help="Already extracted root to validate (default: repository root).",
    )
    p.add_argument(
        "--platform",
        choices=["auto", "windows", "linux"],
        default="auto",
        help="Target packaged layout to validate.",
    )
    p.add_argument(
        "--sample",
        type=Path,
        default=DEFAULT_SAMPLE,
        help="Sample file used for --check probe when executable on host.",
    )
    p.add_argument("--timeout", type=int, default=120, help="Per-command timeout (s).")
    p.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "package_extract_smoke.md",
        help="Markdown output path.",
    )
    p.add_argument(
        "--report-json",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "package_extract_smoke.json",
        help="JSON output path.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    target_platform = args.platform
    if target_platform == "auto":
        target_platform = "windows" if os.name == "nt" else "linux"

    roots: list[Path] = []
    temp_dirs: list[tempfile.TemporaryDirectory[str]] = []
    try:
        if args.archive:
            for raw in args.archive:
                arc = Path(raw).resolve()
                if not arc.exists():
                    print(f"error: archive not found: {arc}")
                    return 2
                td = tempfile.TemporaryDirectory(prefix="ailang_extract_smoke_")
                temp_dirs.append(td)
                root = _extract_archive(arc, Path(td.name))
                roots.append(root)
        else:
            roots.append(args.extract_root.resolve())

        sample = args.sample.resolve()
        all_logs: list[str] = [
            "# Package Extract Smoke",
            "",
            f"- Date: {time.strftime(DATE_HUMAN_FMT)}",
            f"- Target platform: `{target_platform}`",
            f"- Host platform: `{'windows' if os.name == 'nt' else 'linux'}`",
            f"- Sample: `{sample}`",
            "",
        ]
        results: list[dict[str, Any]] = []
        overall_ok = True
        for root in roots:
            ok, payload, log = _probe_root(
                root,
                target_platform=target_platform,
                timeout_s=max(10, int(args.timeout)),
                sample_file=sample,
            )
            results.append(payload)
            all_logs.append(f"## {root}")
            all_logs.append("")
            all_logs.extend(log)
            all_logs.append("")
            overall_ok = overall_ok and ok

        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(all_logs) + "\n", encoding="utf-8")

        report_json = args.report_json.resolve()
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(
            json.dumps(
                {
                    "generated_human": time.strftime(DATE_HUMAN_FMT),
                    "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "target_platform": target_platform,
                    "host_platform": "windows" if os.name == "nt" else "linux",
                    "overall_ok": overall_ok,
                    "results": results,
                    "variants": (
                        results[0].get("variants", {}) if len(results) == 1 else {}
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"json: {report_json}")
        print(f"md: {report_path}")
        print("status: " + ("ok" if overall_ok else "fail"))
        return 0 if overall_ok else 1
    finally:
        for td in temp_dirs:
            td.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

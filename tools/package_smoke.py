#!/usr/bin/env python3
"""Smoke-test packaged compiler binaries in isolated temp directories."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool

DEFAULT_SAMPLE = REPO_ROOT / "benchmarks" / "ailang" / "fib_mix.ail"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


@dataclass
class StepResult:
    name: str
    returncode: int
    stdout: str
    stderr: str


def _run(cmd: list[str], cwd: Path, timeout_s: int) -> StepResult:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
        return StepResult(
            name=" ".join(cmd),
            returncode=int(proc.returncode),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return StepResult(
            name=" ".join(cmd),
            returncode=127,
            stdout="",
            stderr=str(exc),
        )


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _detect_default_bins() -> list[Path]:
    # Keep default detection aligned across both package roots.
    # We still prioritize binaries that are runnable on the current host.
    roots = [
        REPO_ROOT / "out" / "package",
        REPO_ROOT / "out" / "package_wsl",
    ]
    candidates: list[Path] = []
    if os.name == "nt":
        for root in roots:
            candidates.extend(
                [
                    root / "pyinstaller" / "dist" / "ailangc.exe",
                    root / "pyinstaller" / "dist" / "ailangc" / "ailangc.exe",
                    root / "nuitka" / "ailang.exe",
                    root / "nuitka" / "ailang.dist" / "ailang.exe",
                ]
            )
    else:
        for root in roots:
            candidates.extend(
                [
                    root / "pyinstaller" / "dist" / "ailangc",
                    root / "pyinstaller" / "dist" / "ailangc" / "ailangc",
                    root / "nuitka" / "ailang.bin",
                    root / "nuitka" / "ailang.dist" / "ailang.bin",
                ]
            )
    return [p for p in candidates if p.is_file()]


def _should_run_llvm_aot() -> bool:
    clang = resolve_llvm_tool("clang")
    llc = same_llvm_root_tool(clang, "llc") if clang else resolve_llvm_tool("llc")
    return clang is not None or (llc is not None and _tool_available("gcc"))


def _run_binary_smoke(
    bin_path: Path,
    sample_file: Path,
    *,
    timeout_s: int,
    run_jit_json: bool,
) -> tuple[bool, list[str], dict[str, object]]:
    log: list[str] = [f"binary: {bin_path}"]
    details: dict[str, object] = {
        "binary": str(bin_path),
        "ok": True,
        "steps": {},
    }
    ok = True
    with tempfile.TemporaryDirectory(prefix="ailang_pkg_smoke_") as td:
        tmp = Path(td)
        copied_bin = _copy_binary_runtime_bundle(bin_path, tmp)
        if os.name != "nt":
            copied_bin.chmod(0o755)
        sample_copy = tmp / "fib_mix.ail"
        shutil.copy2(sample_file, sample_copy)

        def run_step(
            step_name: str, cmd: list[str], must_pass: bool = True
        ) -> StepResult:
            nonlocal ok
            res = _run(cmd, tmp, timeout_s)
            details_steps = details.get("steps")
            if isinstance(details_steps, dict):
                details_steps[step_name] = {
                    "returncode": int(res.returncode),
                    "stdout_tail": res.stdout.strip().splitlines()[-4:],
                    "stderr_tail": res.stderr.strip().splitlines()[-4:],
                }
            status = "ok" if res.returncode == 0 else "fail"
            log.append(f"  {step_name}: {status} rc={res.returncode}")
            if res.stdout.strip():
                tail = res.stdout.strip().splitlines()[-4:]
                for line in tail:
                    log.append(f"    stdout: {line}")
            if res.stderr.strip():
                tail = res.stderr.strip().splitlines()[-4:]
                for line in tail:
                    log.append(f"    stderr: {line}")
            if must_pass and res.returncode != 0:
                ok = False
            return res

        exe = str(copied_bin)
        run_step("version", [exe, "--version"])
        run_step("help", [exe, "--help"])
        run_step("check", [exe, str(sample_copy), "--check"])
        run_step(
            "c_backend_compile", [exe, str(sample_copy), "--backend=c", "-o", "fib_c"]
        )
        run_step("c_backend_run", [str(tmp / "fib_c")])
        run_step("emit_llvm", [exe, str(sample_copy), "--emit-llvm", "-o", "fib.ll"])

        if _should_run_llvm_aot():
            run_step("llvm_aot_compile", [exe, str(sample_copy), "-o", "fib_llvm"])
            run_step("llvm_aot_run", [str(tmp / "fib_llvm")])
        else:
            log.append("  llvm_aot_compile: skipped (clang/llc toolchain unavailable)")

        if run_jit_json:
            res = run_step("jit_json", [exe, str(sample_copy), "--jit-json"])
            payload = None
            for line in res.stdout.splitlines():
                if line.startswith("JIT_WARM_RESULT="):
                    try:
                        payload = json.loads(line.split("=", 1)[1])
                    except json.JSONDecodeError:
                        payload = None
                    break
            if payload is None:
                log.append("    jit_json: missing JIT_WARM_RESULT payload")
                ok = False
                details["jit_json_status"] = "missing_payload"
            elif payload.get("status") != "ok":
                log.append(
                    f"    jit_json: status={payload.get('status')!r} (expected 'ok')"
                )
                ok = False
                details["jit_json_status"] = str(payload.get("status"))
            else:
                details["jit_json_status"] = "ok"
    details["ok"] = ok
    return ok, log, details


def _copy_binary_runtime_bundle(bin_path: Path, dst_root: Path) -> Path:
    """Copy packaged runtime contents required by the selected binary.

    Nuitka/PyInstaller standalone builds may require sibling shared
    libraries. Copying only the executable can make smoke tests fail with
    loader crashes even when the package itself is valid.
    """
    parent = bin_path.parent
    has_internal_dir = (parent / "_internal").is_dir()
    has_shared_libs = any(
        p.is_file() and p.suffix.lower() in {".so", ".dll", ".dylib"}
        for p in parent.rglob("*")
    )
    should_copy_parent = (
        parent.name.endswith(".dist") or has_internal_dir or has_shared_libs
    )
    if should_copy_parent:
        dst_parent = dst_root / parent.name
        shutil.copytree(parent, dst_parent)
        return dst_parent / bin_path.name

    copied_bin = dst_root / bin_path.name
    shutil.copy2(bin_path, copied_bin)
    return copied_bin


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        action="append",
        default=[],
        help="Path to packaged binary to smoke test (repeatable).",
    )
    parser.add_argument(
        "--sample",
        type=Path,
        default=DEFAULT_SAMPLE,
        help="Sample .ail file for compile/run checks.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Per-step timeout in seconds.",
    )
    parser.add_argument(
        "--run-jit-json",
        action="store_true",
        help="Also run --jit-json and require status=ok.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "package_smoke.md",
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "package_smoke.json",
        help="JSON report output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sample = args.sample.resolve()
    if not sample.exists():
        print(f"error: sample file not found: {sample}")
        return 2

    binaries = (
        [Path(p).resolve() for p in args.binary]
        if args.binary
        else _detect_default_bins()
    )
    if not binaries:
        print("error: no binaries supplied and no default packaged binaries found")
        return 2

    report_lines = [
        "# Package Smoke",
        "",
        f"- Date: {time.strftime(DATE_HUMAN_FMT)}",
        f"- Sample: `{sample}`",
        "",
    ]
    json_results: list[dict[str, object]] = []
    overall_ok = True
    for bin_path in binaries:
        if not bin_path.exists():
            report_lines.append(f"- missing: `{bin_path}`")
            overall_ok = False
            continue
        ok, log, details = _run_binary_smoke(
            bin_path,
            sample,
            timeout_s=max(1, int(args.timeout)),
            run_jit_json=bool(args.run_jit_json),
        )
        report_lines.append(f"## {bin_path}")
        report_lines.append("")
        report_lines.extend(log)
        report_lines.append("")
        json_results.append(details)
        overall_ok = overall_ok and ok

    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    if args.report_json is not None:
        json_path = args.report_json.resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_human": time.strftime(DATE_HUMAN_FMT),
            "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sample": str(sample),
            "overall_ok": bool(overall_ok),
            "binary_count": len(json_results),
            "results": json_results,
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"json: {json_path}")
    print(f"report: {report_path}")
    print("status: " + ("ok" if overall_ok else "fail"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

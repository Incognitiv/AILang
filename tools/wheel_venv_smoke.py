#!/usr/bin/env python3
"""Smoke-test wheel install in a clean virtualenv."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
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
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return int(proc.returncode), out


def _detect_default_wheel() -> Path | None:
    roots = [
        REPO_ROOT / "out" / "package" / "python_dist",
        REPO_ROOT / "out" / "package_wsl" / "python_dist",
    ]
    wheels: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        wheels.extend(sorted(root.glob("*.whl"), key=lambda p: str(p).lower()))
    return wheels[0] if wheels else None


def _venv_paths(venv_root: Path) -> tuple[Path, Path]:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe", venv_root / "Scripts" / "ailangc.exe"
    return venv_root / "bin" / "python", venv_root / "bin" / "ailangc"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Wheel path to validate (default: auto-detect under out/package*/python_dist).",
    )
    p.add_argument(
        "--sample",
        type=Path,
        default=DEFAULT_SAMPLE,
        help="Sample .ail file used for --check probe.",
    )
    p.add_argument("--timeout", type=int, default=180, help="Per-command timeout in seconds.")
    p.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "wheel_venv_smoke.md",
        help="Markdown report path.",
    )
    p.add_argument(
        "--report-json",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "wheel_venv_smoke.json",
        help="JSON report path.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    wheel = args.wheel.resolve() if args.wheel else _detect_default_wheel()
    if wheel is None or not wheel.exists():
        print("error: wheel not found")
        return 2
    sample = args.sample.resolve()
    if not sample.exists():
        print(f"error: sample not found: {sample}")
        return 2

    logs: list[str] = [
        "# Wheel Venv Smoke",
        "",
        f"- Date: {time.strftime(DATE_HUMAN_FMT)}",
        f"- Wheel: `{wheel}`",
        f"- Sample: `{sample}`",
        "",
    ]
    steps: dict[str, Any] = {}
    overall_ok = True

    with tempfile.TemporaryDirectory(prefix="ailang_wheel_venv_") as td:
        tmp = Path(td)
        venv_dir = tmp / "venv"
        rc, out = _run([sys.executable, "-m", "venv", str(venv_dir)], REPO_ROOT, int(args.timeout))
        steps["venv_create"] = {"returncode": rc, "tail": out.strip().splitlines()[-6:]}
        logs.append(f"- venv_create rc={rc}")
        if rc != 0:
            overall_ok = False
        else:
            vpy, ailangc = _venv_paths(venv_dir)
            rc_i, out_i = _run([str(vpy), "-m", "pip", "install", str(wheel)], REPO_ROOT, int(args.timeout))
            steps["pip_install"] = {"returncode": rc_i, "tail": out_i.strip().splitlines()[-12:]}
            logs.append(f"- pip_install rc={rc_i}")
            if rc_i != 0:
                overall_ok = False
            else:
                sample_copy = tmp / "fib_mix.ail"
                sample_copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
                rc_v, out_v = _run([str(ailangc), "--version"], tmp, int(args.timeout))
                steps["version"] = {"returncode": rc_v, "tail": out_v.strip().splitlines()[-6:]}
                logs.append(f"- version rc={rc_v}")
                rc_h, out_h = _run([str(ailangc), "--help"], tmp, int(args.timeout))
                steps["help"] = {"returncode": rc_h, "tail": out_h.strip().splitlines()[-6:]}
                logs.append(f"- help rc={rc_h}")
                rc_c, out_c = _run([str(ailangc), str(sample_copy), "--check"], tmp, int(args.timeout))
                steps["check"] = {"returncode": rc_c, "tail": out_c.strip().splitlines()[-10:]}
                logs.append(f"- check rc={rc_c}")
                if rc_v != 0 or rc_h != 0 or rc_c != 0:
                    overall_ok = False

    payload = {
        "generated_human": time.strftime(DATE_HUMAN_FMT),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wheel": str(wheel),
        "sample": str(sample),
        "overall_ok": bool(overall_ok),
        "steps": steps,
    }
    out_json = args.report_json.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logs.append("")
    logs.append(f"- JSON: `{out_json}`")
    logs.append(f"- Overall: `{'ok' if overall_ok else 'fail'}`")
    out_md = args.report.resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(logs) + "\n", encoding="utf-8")
    print(f"json: {out_json}")
    print(f"md: {out_md}")
    print("status: " + ("ok" if overall_ok else "fail"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

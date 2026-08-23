#!/usr/bin/env python3
"""Environment capability check for reproducible AILang routines."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool


@dataclass
class CheckItem:
    name: str
    kind: str
    required: bool
    available: bool
    detail: str


def _module_available(module_name: str) -> tuple[bool, str]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return False, "module not installed"
    return True, "ok"


def _tool_available(binary: str, version_args: list[str]) -> tuple[bool, str]:
    tool = shutil.which(binary)
    return _tool_path_available(tool, binary, version_args)


def _tool_path_available(
    tool: str | None, binary: str, version_args: list[str]
) -> tuple[bool, str]:
    if tool is None:
        return False, f"{binary} not found"
    try:
        proc = subprocess.run(
            [tool, *version_args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    head = output.splitlines()[0].strip() if output else "unknown"
    return True, f"{tool} :: {head}"


def _collect() -> list[CheckItem]:
    checks: list[CheckItem] = []
    checks.append(
        CheckItem(
            name="python",
            kind="runtime",
            required=True,
            available=sys.version_info >= (3, 10),
            detail=f"{platform.python_version()} @ {sys.executable}",
        )
    )

    gcc_ok, gcc_detail = _tool_available("gcc", ["--version"])
    clang_ok, clang_detail = _tool_available("clang", ["--version"])
    cc_ok = gcc_ok or clang_ok
    checks.append(
        CheckItem(
            name="c_compiler",
            kind="toolchain",
            required=True,
            available=cc_ok,
            detail=(gcc_detail if gcc_ok else clang_detail),
        )
    )
    checks.append(
        CheckItem(
            name="gcc",
            kind="toolchain",
            required=False,
            available=gcc_ok,
            detail=gcc_detail,
        )
    )
    checks.append(
        CheckItem(
            name="clang",
            kind="toolchain",
            required=False,
            available=clang_ok,
            detail=clang_detail,
        )
    )
    llc_ok, llc_detail = _tool_available("llc", ["--version"])
    checks.append(
        CheckItem(
            name="llc",
            kind="toolchain",
            required=False,
            available=llc_ok,
            detail=llc_detail,
        )
    )
    ailang_clang = resolve_llvm_tool("clang")
    ailang_clang_ok, ailang_clang_detail = _tool_path_available(
        ailang_clang, "clang", ["--version"]
    )
    checks.append(
        CheckItem(
            name="ailang_llvm_clang",
            kind="toolchain",
            required=False,
            available=ailang_clang_ok,
            detail=ailang_clang_detail,
        )
    )
    ailang_profdata = same_llvm_root_tool(ailang_clang, "llvm-profdata")
    ailang_profdata_ok, ailang_profdata_detail = _tool_path_available(
        ailang_profdata, "llvm-profdata", ["--version"]
    )
    checks.append(
        CheckItem(
            name="ailang_llvm_profdata",
            kind="toolchain",
            required=False,
            available=ailang_profdata_ok,
            detail=ailang_profdata_detail,
        )
    )
    rustc_ok, rustc_detail = _tool_available("rustc", ["--version"])
    checks.append(
        CheckItem(
            name="rustc",
            kind="toolchain",
            required=False,
            available=rustc_ok,
            detail=rustc_detail,
        )
    )

    module_checks = [
        ("pytest", True),
        ("ruff", False),
        ("mypy", False),
        ("psutil", False),
        ("nuitka", False),
        ("bandit", False),
        ("pylint", False),
        ("isort", False),
        ("black", False),
    ]
    for mod, required in module_checks:
        ok, detail = _module_available(mod)
        checks.append(
            CheckItem(
                name=mod,
                kind="python-module",
                required=required,
                available=ok,
                detail=detail,
            )
        )
    return checks


def _status(checks: list[CheckItem]) -> str:
    missing_required = [c for c in checks if c.required and not c.available]
    if missing_required:
        return "fail"
    missing_optional = [c for c in checks if (not c.required) and not c.available]
    if missing_optional:
        return "warn"
    return "pass"


def _to_payload(checks: list[CheckItem]) -> dict[str, Any]:
    status = _status(checks)
    payload: dict[str, Any] = {
        "status": status,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "checks": [
            {
                "name": c.name,
                "kind": c.kind,
                "required": c.required,
                "available": c.available,
                "detail": c.detail,
            }
            for c in checks
        ],
    }
    payload["missing_required"] = [
        c.name for c in checks if c.required and not c.available
    ]
    payload["missing_optional"] = [
        c.name for c in checks if (not c.required) and not c.available
    ]
    return payload


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout.",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional JSON output path.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    checks = _collect()
    payload = _to_payload(checks)

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"environment status: {payload['status'].upper()}")
        print(f"platform: {payload['platform']}")
        print(f"python: {payload['python']}")
        for c in checks:
            mark = "OK" if c.available else ("REQ-MISS" if c.required else "MISS")
            print(f"[{mark:<8}] {c.kind:<14} {c.name:<14} {c.detail}")
        if payload["missing_required"]:
            print("missing required:", ", ".join(payload["missing_required"]))
        elif payload["missing_optional"]:
            print("missing optional:", ", ".join(payload["missing_optional"]))

    return 1 if payload["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Evaluate release-readiness checklist from routine/session artifacts."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bool_flag(v: Any) -> bool:
    return bool(v)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--session-manifest",
        type=Path,
        required=True,
        help="Routine session.json path.",
    )
    p.add_argument(
        "--package-matrix-json",
        type=Path,
        default=None,
        help="Optional package matrix JSON path.",
    )
    p.add_argument(
        "--release-manifest-json",
        type=Path,
        default=None,
        help="Optional release manifest JSON path.",
    )
    p.add_argument(
        "--require-package",
        action="store_true",
        default=False,
        help="Require package-related checks to pass.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "release_checklist.md",
        help="Checklist markdown output.",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "release_checklist.json",
        help="Checklist JSON output.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.session_manifest.resolve()
    if not manifest_path.exists():
        print(f"error: session manifest not found: {manifest_path}")
        return 2
    session = _read_json(manifest_path)
    exit_codes = session.get("exit_codes", {})

    checks: list[dict[str, Any]] = []

    def add_check(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    bench_ok = int(exit_codes.get("benchmark", 1)) == 0
    add_check(
        "benchmark_gate",
        "pass" if bench_ok else "fail",
        f"exit={exit_codes.get('benchmark')}",
    )

    reg_ok = int(exit_codes.get("regression", 1)) == 0
    add_check(
        "regression_gate",
        "pass" if reg_ok else "fail",
        f"exit={exit_codes.get('regression')}",
    )

    ver = session.get("verifier_summary", {})
    ver_ok = int(ver.get("passed", 0)) == int(ver.get("total", -1))
    add_check(
        "strict_verifier",
        "pass" if ver_ok else "fail",
        f"passed={ver.get('passed')} total={ver.get('total')}",
    )

    god = session.get("god_object_summary", {})
    god_ok = int(god.get("candidate_count", 1)) == 0
    add_check(
        "god_object_budget",
        "pass" if god_ok else "fail",
        f"candidates={god.get('candidate_count')} scanned={god.get('scanned_files')}",
    )

    pkg_stage = session.get("package_smoke", {})
    pkg_enabled = _bool_flag(pkg_stage.get("enabled"))
    pkg_ok = int(pkg_stage.get("exit_code", 1)) == 0 if pkg_enabled else None
    if pkg_enabled:
        add_check(
            "package_smoke",
            "pass" if bool(pkg_ok) else "fail",
            f"exit={pkg_stage.get('exit_code')}",
        )
    else:
        add_check("package_smoke", "skip", "stage disabled")

    matrix_ok: bool | None = None
    if args.package_matrix_json is not None:
        matrix_path = args.package_matrix_json.resolve()
        if matrix_path.exists():
            matrix_data = _read_json(matrix_path)
            matrix_ok = bool(matrix_data.get("overall_ready"))
            add_check(
                "package_matrix",
                "pass" if matrix_ok else "fail",
                f"overall_ready={matrix_ok}",
            )
        else:
            add_check("package_matrix", "fail", f"missing: {matrix_path}")
            matrix_ok = False
    else:
        add_check("package_matrix", "skip", "not provided")

    rel_ok: bool | None = None
    if args.release_manifest_json is not None:
        rel_path = args.release_manifest_json.resolve()
        if rel_path.exists():
            rel_data = _read_json(rel_path)
            rel_count = int(rel_data.get("artifact_count", 0))
            rel_ok = rel_count > 0
            add_check(
                "release_manifest",
                "pass" if rel_ok else "fail",
                f"artifact_count={rel_count}",
            )
        else:
            add_check("release_manifest", "fail", f"missing: {rel_path}")
            rel_ok = False
    else:
        add_check("release_manifest", "skip", "not provided")

    required_fails = {
        c["name"]
        for c in checks
        if c["status"] == "fail"
        and c["name"]
        in {
            "benchmark_gate",
            "regression_gate",
            "strict_verifier",
            "god_object_budget",
        }
    }
    if args.require_package:
        if pkg_ok is not True:
            required_fails.add("package_smoke")
        if matrix_ok is False or matrix_ok is None:
            required_fails.add("package_matrix")
        if rel_ok is False or rel_ok is None:
            required_fails.add("release_manifest")

    overall_ok = len(required_fails) == 0
    payload = {
        "generated_human": time.strftime(DATE_HUMAN_FMT),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_manifest": str(manifest_path),
        "require_package": bool(args.require_package),
        "overall_ok": bool(overall_ok),
        "failed_required_checks": sorted(required_fails),
        "checks": checks,
    }

    out_json = args.output_json.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Release Checklist",
        "",
        f"- Date: {payload['generated_human']}",
        f"- Session: `{manifest_path}`",
        f"- Require package: `{bool(args.require_package)}`",
        f"- Overall ready: `{bool(overall_ok)}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Details |",
        "| --- | --- | --- |",
    ]
    for c in checks:
        lines.append(f"| `{c['name']}` | `{c['status']}` | {c['detail']} |")
    lines.append("")
    if required_fails:
        lines.append(
            "- Failed required checks: "
            + ", ".join(f"`{name}`" for name in sorted(required_fails))
        )
    else:
        lines.append("- Failed required checks: `none`")
    lines.append(f"- JSON: `{out_json}`")

    out_md = args.output_md.resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"json: {out_json}")
    print(f"md: {out_md}")
    print("status: " + ("ok" if overall_ok else "fail"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

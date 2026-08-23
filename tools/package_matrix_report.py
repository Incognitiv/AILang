#!/usr/bin/env python3
"""Generate packaging support matrix from smoke/extraction signals."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"
DEFAULT_OUTPUT_MD = REPO_ROOT / "benchmarks" / "results" / "package_matrix.md"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "benchmarks" / "results" / "package_matrix.json"
DEFAULT_EXTRACT_SMOKE_JSON = (
    REPO_ROOT / "benchmarks" / "results" / "package_extract_smoke.json"
)

POSTURE: dict[str, dict[str, str]] = {
    "source": {
        "label": "Source",
        "support": "stable",
        "note": "Primary diagnostics/development path.",
    },
    "pyinstaller": {
        "label": "PyInstaller",
        "support": "release_candidate",
        "note": "Standalone distribution candidate.",
    },
    "nuitka": {
        "label": "Nuitka",
        "support": "release_candidate",
        "note": "Standalone distribution candidate.",
    },
    "cython": {
        "label": "Cython",
        "support": "experimental",
        "note": "Embed experiment; not standalone by default.",
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _variant_from_binary(path_text: str) -> str | None:
    low = path_text.lower().replace("\\", "/")
    if "pyinstaller" in low:
        return "pyinstaller"
    if "nuitka" in low:
        return "nuitka"
    if "cython" in low:
        return "cython"
    return None


def _rollup_package_smoke(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    data = _read_json(path)
    for row in data.get("results", []):
        if not isinstance(row, dict):
            continue
        binary = str(row.get("binary", ""))
        variant = _variant_from_binary(binary)
        if variant is None:
            continue
        slot = out.setdefault(
            variant,
            {
                "found": False,
                "smoke_ok": False,
                "smoke_any": False,
                "entries": [],
            },
        )
        slot["found"] = True
        slot["smoke_any"] = True
        ok = bool(row.get("ok"))
        slot["smoke_ok"] = bool(slot["smoke_ok"] or ok)
        entries = slot.get("entries")
        if isinstance(entries, list):
            entries.append(binary)
    return out


def _rollup_extract_smoke(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    data = _read_json(path)
    for variant, row in data.get("variants", {}).items():
        if not isinstance(row, dict):
            continue
        slot = out.setdefault(
            variant,
            {
                "found": False,
                "extract_ok": False,
                "extract_any": False,
                "entries": [],
            },
        )
        slot["extract_any"] = True
        slot["found"] = bool(row.get("found"))
        slot["extract_ok"] = bool(row.get("ok"))
        found_path = row.get("path")
        if isinstance(found_path, str) and found_path:
            entries = slot.get("entries")
            if isinstance(entries, list):
                entries.append(found_path)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--package-smoke-json",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "package_smoke.json",
        help="Package smoke JSON input.",
    )
    p.add_argument(
        "--extract-smoke-json",
        type=Path,
        default=DEFAULT_EXTRACT_SMOKE_JSON,
        help="Optional extraction smoke JSON input.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUTPUT_MD,
        help="Markdown matrix output.",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help="JSON matrix output.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Return non-zero when release-candidate variants are not green.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    smoke_path = args.package_smoke_json.resolve()
    extract_path = (
        args.extract_smoke_json.resolve() if args.extract_smoke_json else None
    )

    smoke_rollup = _rollup_package_smoke(smoke_path)
    extract_rollup = _rollup_extract_smoke(extract_path) if extract_path else {}

    variants: dict[str, dict[str, Any]] = {}
    for key in POSTURE:
        variants[key] = {
            "variant": key,
            "label": POSTURE[key]["label"],
            "support": POSTURE[key]["support"],
            "note": POSTURE[key]["note"],
            "found": True if key == "source" else False,
            "smoke_ok": True if key == "source" else None,
            "extract_ok": None,
            "overall_ok": True if key == "source" else False,
            "evidence": [],
        }

    for variant, row in smoke_rollup.items():
        if variant not in variants:
            continue
        slot = variants[variant]
        slot["found"] = bool(row.get("found"))
        slot["smoke_ok"] = bool(row.get("smoke_ok"))
        slot["overall_ok"] = bool(row.get("smoke_ok"))
        entries = slot.get("evidence")
        if isinstance(entries, list):
            for binary in row.get("entries", []):
                entries.append(f"smoke:{binary}")

    for variant, row in extract_rollup.items():
        if variant not in variants:
            continue
        slot = variants[variant]
        if bool(row.get("found")):
            slot["found"] = True
        slot["extract_ok"] = bool(row.get("extract_ok"))
        smoke_ok = slot.get("smoke_ok")
        extract_ok = slot.get("extract_ok")
        if isinstance(smoke_ok, bool) and isinstance(extract_ok, bool):
            slot["overall_ok"] = smoke_ok and extract_ok
        elif isinstance(smoke_ok, bool):
            slot["overall_ok"] = smoke_ok
        elif isinstance(extract_ok, bool):
            slot["overall_ok"] = extract_ok
        entries = slot.get("evidence")
        if isinstance(entries, list):
            for found_path in row.get("entries", []):
                entries.append(f"extract:{found_path}")

    must_pass = ["pyinstaller", "nuitka"]
    required_ok = all(bool(variants[v].get("overall_ok")) for v in must_pass)
    payload: dict[str, Any] = {
        "generated_human": time.strftime(DATE_HUMAN_FMT),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": {
            "package_smoke_json": str(smoke_path),
            "extract_smoke_json": str(extract_path) if extract_path else None,
        },
        "overall_ready": bool(required_ok),
        "required_variants": must_pass,
        "variants": variants,
    }

    out_json = args.output_json.resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Package Matrix",
        "",
        f"- Date: {payload['generated_human']}",
        f"- Package smoke JSON: `{smoke_path}`",
        (
            f"- Extract smoke JSON: `{extract_path}`"
            if extract_path is not None
            else "- Extract smoke JSON: `none`"
        ),
        "",
        "## Support Matrix",
        "",
        "| Variant | Support | Found | Smoke | Extract | Overall | Notes |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for key in ("source", "pyinstaller", "nuitka", "cython"):
        row = variants[key]
        smoke_txt = "n/a" if row["smoke_ok"] is None else str(bool(row["smoke_ok"]))
        ext_txt = "n/a" if row["extract_ok"] is None else str(bool(row["extract_ok"]))
        lines.append(
            f"| `{row['label']}` | `{row['support']}` | `{bool(row['found'])}` | "
            f"`{smoke_txt}` | `{ext_txt}` | `{bool(row['overall_ok'])}` | "
            f"{row['note']} |"
        )

    lines.extend(
        [
            "",
            "## Release Candidate Gate",
            "",
            f"- Required variants: `{', '.join(must_pass)}`",
            f"- Overall ready: `{required_ok}`",
            "",
            f"- JSON: `{out_json}`",
        ]
    )

    out_md = args.output_md.resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"json: {out_json}")
    print(f"md: {out_md}")
    if args.strict and not required_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

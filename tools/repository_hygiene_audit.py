#!/usr/bin/env python3
"""Repository-level hygiene gate for structural clones and app-specific hacks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verifier.tools.clone import run_project_clone_audit

AUDIT_ROOTS = ("source", "verifier", "tools")
APP_SPECIFIC_PATTERNS = {
    "adapt_repair_marker": re.compile(r"\bADAPT_REPAIR\b|\bADAPT_REPAIR_"),
    "adapt_feature_flag": re.compile(r"\badapt_hot_shape_helpers\b"),
    "rvs_feature_flag": re.compile(r"\brvs_[a-z0-9_]+\b", re.IGNORECASE),
    "rvs_release_metadata": re.compile(r"RVS Serious App Proof|FreeBSD Homecoming"),
}
ABSOLUTE_LIBRARY_PATH = re.compile(
    r"(?:['\"]/(?:usr|opt|lib|System|Applications)/[^'\"]*\.(?:so(?:\.\d+)*|dylib)['\"])"
    r"|(?:['\"][A-Za-z]:\\[^'\"]*\.dll['\"])",
    re.IGNORECASE,
)


def _python_files(root_name: str) -> list[Path]:
    return sorted((REPO_ROOT / root_name).rglob("*.py"))


def audit_repository() -> dict[str, object]:
    clone_results: dict[str, dict[str, object]] = {}
    issues: list[str] = []

    for root_name in AUDIT_ROOTS:
        files = _python_files(root_name)
        result = run_project_clone_audit(
            [str(path) for path in files], str(REPO_ROOT / root_name)
        )
        clone_results[root_name] = result
        for issue in result.get("issues", []):
            issues.append(f"clone:{root_name}: {issue}")

    scanned_files = 0
    for root_name in AUDIT_ROOTS:
        root = REPO_ROOT / root_name
        for path in root.rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            scanned_files += 1
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                issues.append(f"read:{path.relative_to(REPO_ROOT)}: {exc}")
                continue
            rel = path.relative_to(REPO_ROOT)
            for line_no, line in enumerate(text.splitlines(), 1):
                for name, pattern in APP_SPECIFIC_PATTERNS.items():
                    if pattern.search(line):
                        issues.append(f"{name}:{rel}:{line_no}: {line.strip()}")
                if ABSOLUTE_LIBRARY_PATH.search(line):
                    issues.append(
                        f"absolute_library_path:{rel}:{line_no}: {line.strip()}"
                    )

    return {
        "passed": not issues,
        "scanned_files": scanned_files,
        "clone_roots": {
            name: int(result.get("clone_count", 0))
            for name, result in clone_results.items()
        },
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = audit_repository()
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        "repository hygiene: "
        f"files={payload['scanned_files']} clones={payload['clone_roots']} "
        f"issues={len(payload['issues'])}"
    )
    for issue in payload["issues"]:
        print(f"FAIL {issue}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

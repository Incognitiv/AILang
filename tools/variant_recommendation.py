#!/usr/bin/env python3
"""Generate AILang variant recommendation from comparison and package smoke signals."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPARE_JSON = (
    REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.json"
)
DEFAULT_OUTPUT_MD = REPO_ROOT / "benchmarks" / "results" / "variant_recommendation.md"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return None


def _median(values: Any) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    nums: list[float] = []
    for v in values:
        if isinstance(v, (int, float)):
            nums.append(float(v))
    if not nums:
        return None
    return float(statistics.median(nums))


def _perf_rollup(bench: dict[str, Any]) -> tuple[int, int, float]:
    total = 0
    faster = 0
    delta_sum = 0.0
    current = bench.get("current", {})
    old = bench.get("old", {})
    for case in sorted(set(current) & set(old)):
        cur_case = current.get(case, {})
        old_case = old.get(case, {})
        for impl in sorted(set(cur_case) & set(old_case)):
            cur = cur_case.get(impl, {})
            oldv = old_case.get(impl, {})
            if cur.get("status") != "ok" or oldv.get("status") != "ok":
                continue
            c_med = cur.get("runtime_median_ms")
            o_med = oldv.get("runtime_median_ms")
            if not isinstance(c_med, (int, float)) or not isinstance(
                o_med, (int, float)
            ):
                continue
            total += 1
            if c_med <= o_med:
                faster += 1
            delta_sum += float(o_med) - float(c_med)
    return faster, total, delta_sum


def _package_status(report_paths: list[Path]) -> tuple[bool | None, list[str]]:
    if not report_paths:
        return None, []
    statuses: list[str] = []
    oks: list[bool] = []
    for p in report_paths:
        if not p.exists():
            statuses.append(f"{p.name}: missing")
            continue
        try:
            data = _read_json(p)
            ok = bool(data.get("overall_ok"))
            oks.append(ok)
            statuses.append(f"{p.name}: {'ok' if ok else 'fail'}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            statuses.append(f"{p.name}: invalid")
    if not oks:
        return None, statuses
    return all(oks), statuses


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--compare-json",
        type=Path,
        default=DEFAULT_COMPARE_JSON,
        help="Path to compare_with_ailang_main JSON report.",
    )
    p.add_argument(
        "--package-smoke-json",
        action="append",
        default=[],
        help="Optional package smoke JSON input (repeatable).",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUTPUT_MD,
        help="Recommendation markdown output path.",
    )
    p.add_argument(
        "--allow-missing-compare",
        action="store_true",
        default=False,
        help="Allow missing compare JSON and emit package-only recommendation.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    compare_path = args.compare_json.resolve()
    compare_available = compare_path.exists()
    if not compare_available and not args.allow_missing_compare:
        print(f"error: compare json not found: {compare_path}")
        return 2

    data: dict[str, Any] = {}
    current_ver: dict[str, Any] = {}
    old_ver: dict[str, Any] = {}
    c_passed: int | None = None
    c_total: int | None = None
    o_passed: int | None = None
    o_total: int | None = None
    perf_pass: bool | None = None
    leak_pass: bool | None = None
    fuzz_pass: bool | None = None
    faster = 0
    perf_count = 0
    delta_sum = 0.0
    if compare_available:
        data = _read_json(compare_path)
        current_ver = data.get("coverage", {}).get("current", {}).get("verifier", {})
        old_ver = data.get("coverage", {}).get("old", {}).get("verifier_pkg", {})

        c_passed = _safe_int(current_ver.get("passed"))
        c_total = _safe_int(current_ver.get("total"))
        o_passed = _safe_int(old_ver.get("passed"))
        o_total = _safe_int(old_ver.get("total"))

        gates = data.get("gates", {})
        perf_pass = bool(gates.get("perf_pass"))
        leak_pass = bool(gates.get("leak_budget_pass"))
        fuzz_pass = bool(gates.get("fuzz_pass", True))

        faster, perf_count, delta_sum = _perf_rollup(data.get("benchmarks", {}))

    package_paths = [Path(p).resolve() for p in args.package_smoke_json]
    pkg_ok, pkg_lines = _package_status(package_paths)

    source_reco = "AILang-Pure source"
    reasons: list[str] = []
    if compare_available and (
        c_passed is not None
        and c_total is not None
        and o_passed is not None
        and o_total is not None
    ):
        if c_passed >= o_passed:
            reasons.append(
                "current strict-verifier coverage is equal or better than old-main"
            )
        else:
            reasons.append("old-main strict-verifier coverage is currently higher")
            source_reco = "AILang-main source"
    elif not compare_available:
        reasons.append(
            "old-main comparison unavailable; source recommendation uses local package/safety signals only"
        )

    if compare_available:
        if leak_pass:
            reasons.append("leak budget checks pass")
        else:
            reasons.append("leak budget checks report issues")

    if compare_available and perf_count > 0:
        reasons.append(
            f"current is faster-or-equal on {faster}/{perf_count} shared perf rows "
            f"(aggregate old-current delta {delta_sum:+.2f} ms)"
        )
        if faster * 2 < perf_count and source_reco == "AILang-Pure source":
            reasons.append("performance trend favors old-main on most rows")

    if compare_available and perf_pass is False:
        reasons.append("configured perf-regression gate flagged slowdowns")
    if compare_available and fuzz_pass is False:
        reasons.append("backend differential fuzz gate has mismatches")

    if source_reco == "AILang-Pure source" and compare_available and leak_pass is False:
        source_reco = "AILang-main source"

    package_reco = "PyInstaller/Nuitka packaged binaries"
    if pkg_ok is False:
        package_reco = "Source run (packaged binaries need fixes)"
    elif pkg_ok is None:
        package_reco = "No package-smoke signal; prefer source for diagnostics"

    lines: list[str] = [
        "# AILang Variant Recommendation",
        "",
        f"- Date: {time.strftime(DATE_HUMAN_FMT)}",
        f"- Compare source: `{compare_path}` ({'found' if compare_available else 'missing'})",
    ]
    if package_paths:
        lines.append(
            "- Package smoke inputs: " + ", ".join(f"`{p}`" for p in package_paths)
        )
    lines.extend(
        [
            "",
            "## Signals",
            "",
            (
                f"- Current strict verifier: `{c_passed}/{c_total}`"
                if compare_available
                else "- Current strict verifier: `n/a (compare missing)`"
            ),
            (
                f"- Old strict verifier: `{o_passed}/{o_total}`"
                if compare_available
                else "- Old strict verifier: `n/a (compare missing)`"
            ),
            (
                f"- Perf gate pass: `{perf_pass}`"
                if compare_available
                else "- Perf gate pass: `n/a (compare missing)`"
            ),
            (
                f"- Leak gate pass: `{leak_pass}`"
                if compare_available
                else "- Leak gate pass: `n/a (compare missing)`"
            ),
            (
                f"- Fuzz gate pass: `{fuzz_pass}`"
                if compare_available
                else "- Fuzz gate pass: `n/a (compare missing)`"
            ),
            (
                f"- Shared perf rows faster-or-equal (current): `{faster}/{perf_count}`"
                if compare_available
                else "- Shared perf rows faster-or-equal (current): `n/a (compare missing)`"
            ),
            "",
            "## Package Smoke",
            "",
        ]
    )
    if pkg_lines:
        for row in pkg_lines:
            lines.append(f"- {row}")
    else:
        lines.append("- no package smoke JSON provided")

    lines.extend(["", "## Recommendation", ""])
    lines.append(f"1. Primary source variant: `{source_reco}`")
    lines.append(f"2. Primary delivery variant: `{package_reco}`")
    lines.append("3. Rationale:")
    for reason in reasons:
        lines.append(f"- {reason}")

    out_path = args.output_md.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

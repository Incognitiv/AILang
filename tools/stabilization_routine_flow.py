from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    from .stabilization_routine_common import *
except ImportError:
    from stabilization_routine_common import *

try:
    from .stabilization_routine_args import parse_args
except ImportError:
    from stabilization_routine_args import parse_args

try:
    from .stabilization_routine_doc import _update_session_benchmark_doc
except ImportError:
    from stabilization_routine_doc import _update_session_benchmark_doc

def main() -> int:
    args = parse_args()
    if args.full_routine:
        args.compare_old_main = True
        args.package_smoke = True
        args.variant_recommendation = True
    label = args.label or f"routine_{time.strftime('%Y%m%d_%H%M%S')}"

    if args.env_check:
        preflight_cmd = [sys.executable, str(ENV_CHECK_TOOL)]
        print("[routine] environment preflight")
        preflight_rc = _run(preflight_cmd, timeout=300)
        if preflight_rc != 0:
            print(f"[routine] environment preflight failed (exit={preflight_rc})")
            return preflight_rc

    cap_cmd = [
        sys.executable,
        str(SESSION_TOOL),
        "capture",
        "--label",
        label,
        "--runs",
        str(args.runs),
        "--warmup",
        str(args.warmup),
    ]
    if args.sample_memory:
        cap_cmd.append("--sample-memory")
    if args.check_leaks:
        cap_cmd.extend(["--check-leaks", "--leak-threshold", str(args.leak_threshold)])
    for case in args.case:
        cap_cmd += ["--case", case]
    for impl in args.impl:
        cap_cmd += ["--impl", impl]

    print(f"[routine] capture label={label}")
    rc = _run(cap_cmd, timeout=3600)
    if rc != 0:
        print(f"[routine] capture failed (exit={rc})")
        return rc

    session_manifest = SESSION_ROOT / label / "session.json"
    manifest_payload = _read_json(session_manifest)
    manifest_payload["old_main_compare"] = {"enabled": False, "exit_code": None}
    manifest_payload["env_check"] = {"enabled": False, "exit_code": None}
    manifest_payload["phase_profile"] = {"enabled": False, "exit_code": None}
    manifest_payload["language_surface_profile"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["durability_stress"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["strict_surface_suite"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["adapt_teardown"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["package_smoke"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["package_matrix"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["package_extract_smoke"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["variant_recommendation"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["release_manifest"] = {
        "enabled": False,
        "exit_code": None,
    }
    manifest_payload["release_checklist"] = {
        "enabled": False,
        "exit_code": None,
    }

    if args.compare_old_main:
        old_main_json = SESSION_ROOT / label / "compare_with_old_main.json"
        old_main_md = SESSION_ROOT / label / "compare_with_old_main.md"
        old_main_cmd = [
            sys.executable,
            str(OLD_MAIN_COMPARE_TOOL),
            "--old-prototype-root",
            str(args.old_main_root),
            "--runs",
            str(args.runs),
            "--warmup",
            str(args.warmup),
            "--leak-threshold",
            str(args.leak_threshold),
            "--perf-ratio-threshold",
            str(args.old_main_perf_ratio_threshold),
            "--perf-abs-ms-threshold",
            str(args.old_main_perf_abs_ms_threshold),
            "--fuzz-cases",
            str(args.old_main_fuzz_cases),
            "--fuzz-seed",
            str(args.old_main_fuzz_seed),
            "--output-json",
            str(old_main_json),
            "--output-md",
            str(old_main_md),
        ]
        if not args.check_leaks:
            old_main_cmd.append("--no-check-leaks")
        if args.old_main_fail_on_perf_regression:
            old_main_cmd.append("--fail-on-perf-regression")
        if args.old_main_fail_on_fuzz_mismatch:
            old_main_cmd.append("--fail-on-fuzz-mismatch")
        for case in args.case:
            old_main_cmd += ["--case", case]

        selected_om_impls = [
            impl for impl in args.impl if impl in {"ailang_jit", "ailang_aot"}
        ]
        for impl in selected_om_impls:
            old_main_cmd += ["--impl", impl]

        print("[routine] compare current vs old-main")
        old_main_rc = _run(old_main_cmd, timeout=5400)
        manifest_payload["old_main_compare"] = {
            "enabled": True,
            "exit_code": old_main_rc,
            "report_json": str(old_main_json),
            "report_md": str(old_main_md),
            "command": old_main_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if old_main_rc != 0:
            print(f"[routine] old-main compare failed (exit={old_main_rc})")
            return old_main_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.env_check:
        env_json = SESSION_ROOT / label / "env_check.json"
        env_cmd = [
            sys.executable,
            str(ENV_CHECK_TOOL),
            "--output-json",
            str(env_json),
        ]
        print("[routine] environment check")
        env_rc = _run(env_cmd, timeout=300)
        manifest_payload["env_check"] = {
            "enabled": True,
            "exit_code": env_rc,
            "report_json": str(env_json),
            "command": env_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if env_rc != 0:
            print(f"[routine] environment check failed (exit={env_rc})")
            return env_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.phase_profile:
        phase_json = SESSION_ROOT / label / "compile_phase_profile.json"
        phase_md = SESSION_ROOT / label / "compile_phase_profile.md"
        phase_cmd = [
            sys.executable,
            str(PHASE_PROFILE_TOOL),
            "--backend",
            args.phase_profile_backend,
            "--output-json",
            str(phase_json),
            "--output-md",
            str(phase_md),
        ]
        if args.phase_profile_source:
            for src in args.phase_profile_source:
                phase_cmd += ["--source", str(src)]
        elif args.case:
            for case in args.case:
                phase_cmd += [
                    "--source",
                    str(REPO_ROOT / "benchmarks" / "ailang" / f"{case}.ail"),
                ]
        print("[routine] compiler phase profile capture")
        phase_rc = _run(phase_cmd, timeout=5400)
        manifest_payload["phase_profile"] = {
            "enabled": True,
            "exit_code": phase_rc,
            "report_json": str(phase_json),
            "report_md": str(phase_md),
            "command": phase_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if phase_rc != 0:
            print(f"[routine] compiler phase profile failed (exit={phase_rc})")
            return phase_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.language_surface_profile:
        lang_json = SESSION_ROOT / label / "language_surface_profile.json"
        lang_md = SESSION_ROOT / label / "language_surface_profile.md"
        lang_cmd = [
            sys.executable,
            str(LANGUAGE_PROFILE_TOOL),
            "--output-json",
            str(lang_json),
            "--output-md",
            str(lang_md),
        ]
        if args.language_surface_wsl_perf:
            lang_cmd.append("--wsl-perf")
            lang_cmd.extend(["--wsl-perf-top", str(args.language_surface_wsl_perf_top)])

        print("[routine] language-surface profile capture")
        lang_rc = _run(lang_cmd, timeout=7200)
        manifest_payload["language_surface_profile"] = {
            "enabled": True,
            "exit_code": lang_rc,
            "report_json": str(lang_json),
            "report_md": str(lang_md),
            "command": lang_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if lang_rc != 0:
            print(f"[routine] language-surface profile failed (exit={lang_rc})")
            return lang_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.durability_stress:
        dur_json = SESSION_ROOT / label / "durability_stress.json"
        dur_md = SESSION_ROOT / label / "durability_stress.md"
        dur_cmd = [
            sys.executable,
            str(DURABILITY_TOOL),
            "--duration-ms",
            str(args.durability_ms),
            "--output-json",
            str(dur_json),
            "--output-md",
            str(dur_md),
            "--baseline",
            str(args.durability_baseline),
            "--leak-threshold",
            str(args.durability_leak_threshold),
        ]
        print("[routine] durability stress capture")
        dur_rc = _run(dur_cmd, timeout=7200)
        manifest_payload["durability_stress"] = {
            "enabled": True,
            "exit_code": dur_rc,
            "report_json": str(dur_json),
            "report_md": str(dur_md),
            "command": dur_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if dur_rc != 0:
            print(f"[routine] durability stress failed (exit={dur_rc})")
            return dur_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.strict_surface_suite:
        strict_json = SESSION_ROOT / label / "strict_surface_suite.json"
        strict_md = SESSION_ROOT / label / "strict_surface_suite.md"
        strict_cmd = [
            sys.executable,
            str(STRICT_SURFACE_TOOL),
            "--output-json",
            str(strict_json),
            "--output-md",
            str(strict_md),
        ]
        if isinstance(args.strict_surface_max_builtins, int):
            strict_cmd += [
                "--max-builtins",
                str(args.strict_surface_max_builtins),
            ]
        print("[routine] strict surface suite capture")
        strict_rc = _run(strict_cmd, timeout=7200)
        manifest_payload["strict_surface_suite"] = {
            "enabled": True,
            "exit_code": strict_rc,
            "report_json": str(strict_json),
            "report_md": str(strict_md),
            "command": strict_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if strict_rc != 0:
            print(f"[routine] strict surface suite failed (exit={strict_rc})")
            return strict_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.adapt_teardown:
        adapt_json = SESSION_ROOT / label / "adapt_teardown.json"
        adapt_md = SESSION_ROOT / label / "adapt_teardown.md"
        adapt_cmd = [
            sys.executable,
            str(ADAPT_TEARDOWN_TOOL),
            "--adapt-root",
            str(args.adapt_root),
            "--contract",
            str(args.adapt_contract),
            "--compile-timeout",
            str(max(30, int(args.adapt_compile_timeout))),
            "--run-timeout",
            str(max(10, int(args.adapt_run_timeout))),
            "--output-json",
            str(adapt_json),
            "--output-md",
            str(adapt_md),
            "--allow-missing-adapt-root",
        ]
        for prog in args.adapt_program:
            adapt_cmd += ["--program", str(prog)]
        print("[routine] ADAPT teardown audit")
        adapt_rc = _run(adapt_cmd, timeout=7200)
        adapt_summary: dict[str, int] = {}
        if adapt_json.exists():
            try:
                adapt_payload = _read_json(adapt_json)
            except (OSError, ValueError, TypeError):
                adapt_payload = {}
            summary = adapt_payload.get("summary", {})
            if isinstance(summary, dict):
                for key in (
                    "no_live",
                    "intentional_cache",
                    "true_leak",
                    "harness_artifact",
                ):
                    value = summary.get(key)
                    if isinstance(value, int):
                        adapt_summary[key] = value
        manifest_payload["adapt_teardown"] = {
            "enabled": True,
            "exit_code": adapt_rc,
            "report_json": str(adapt_json),
            "report_md": str(adapt_md),
            "summary": adapt_summary,
            "command": adapt_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if adapt_rc != 0:
            print(f"[routine] ADAPT teardown audit failed (exit={adapt_rc})")
            return adapt_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.package_smoke:
        pkg_json = SESSION_ROOT / label / "package_smoke.json"
        pkg_md = SESSION_ROOT / label / "package_smoke.md"
        pkg_cmd = [
            sys.executable,
            str(PACKAGE_SMOKE_TOOL),
            "--report",
            str(pkg_md),
            "--report-json",
            str(pkg_json),
            "--timeout",
            str(max(30, int(args.package_smoke_timeout))),
        ]
        if args.package_smoke_run_jit_json:
            pkg_cmd.append("--run-jit-json")
        print("[routine] package smoke capture")
        pkg_rc = _run(pkg_cmd, timeout=5400)
        manifest_payload["package_smoke"] = {
            "enabled": True,
            "exit_code": pkg_rc,
            "report_json": str(pkg_json),
            "report_md": str(pkg_md),
            "command": pkg_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if pkg_rc != 0:
            print(f"[routine] package smoke failed (exit={pkg_rc})")
            return pkg_rc

        extract_json = SESSION_ROOT / label / "package_extract_smoke.json"
        if args.package_extract_smoke:
            extract_md = SESSION_ROOT / label / "package_extract_smoke.md"
            extract_cmd = [
                sys.executable,
                str(PACKAGE_EXTRACT_TOOL),
                "--extract-root",
                str(REPO_ROOT),
                "--report",
                str(extract_md),
                "--report-json",
                str(extract_json),
            ]
            print("[routine] package extract smoke")
            extract_rc = _run(extract_cmd, timeout=1800)
            manifest_payload["package_extract_smoke"] = {
                "enabled": True,
                "exit_code": extract_rc,
                "report_json": str(extract_json),
                "report_md": str(extract_md),
                "command": extract_cmd,
            }
            _write_json(session_manifest, manifest_payload)
            if extract_rc != 0:
                print(f"[routine] package extract smoke failed (exit={extract_rc})")
                return extract_rc

        matrix_json = SESSION_ROOT / label / "package_matrix.json"
        matrix_md = SESSION_ROOT / label / "package_matrix.md"
        matrix_cmd = [
            sys.executable,
            str(PACKAGE_MATRIX_TOOL),
            "--package-smoke-json",
            str(pkg_json),
            "--output-json",
            str(matrix_json),
            "--output-md",
            str(matrix_md),
            "--strict",
        ]
        if args.package_extract_smoke:
            matrix_cmd += ["--extract-smoke-json", str(extract_json)]
        print("[routine] package matrix")
        matrix_rc = _run(matrix_cmd, timeout=1200)
        manifest_payload["package_matrix"] = {
            "enabled": True,
            "exit_code": matrix_rc,
            "report_json": str(matrix_json),
            "report_md": str(matrix_md),
            "command": matrix_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if matrix_rc != 0:
            print(f"[routine] package matrix failed (exit={matrix_rc})")
            return matrix_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.variant_recommendation:
        compare_json = SESSION_ROOT / label / "compare_with_old_main.json"
        fallback_compare_json = (
            REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.json"
        )
        chosen_compare_json: Path | None = None
        if compare_json.exists():
            chosen_compare_json = compare_json
        elif fallback_compare_json.exists():
            chosen_compare_json = fallback_compare_json

        variant_md = SESSION_ROOT / label / "variant_recommendation.md"
        variant_cmd = [
            sys.executable,
            str(VARIANT_RECOMMENDATION_TOOL),
            "--output-md",
            str(variant_md),
        ]
        if chosen_compare_json is not None:
            variant_cmd += [
                "--compare-json",
                str(chosen_compare_json),
            ]
        else:
            variant_cmd.append("--allow-missing-compare")
        pkg_json_path = SESSION_ROOT / label / "package_smoke.json"
        if pkg_json_path.exists():
            variant_cmd += ["--package-smoke-json", str(pkg_json_path)]
        print("[routine] variant recommendation")
        variant_rc = _run(variant_cmd, timeout=1200)
        manifest_payload["variant_recommendation"] = {
            "enabled": True,
            "exit_code": variant_rc,
            "report_md": str(variant_md),
            "command": variant_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if variant_rc != 0:
            print(f"[routine] variant recommendation failed (exit={variant_rc})")
            return variant_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.release_manifest:
        release_manifest_json = SESSION_ROOT / label / "release_manifest.json"
        release_manifest_md = SESSION_ROOT / label / "release_manifest.md"
        release_manifest_cmd = [
            sys.executable,
            str(RELEASE_MANIFEST_TOOL),
            "--output-json",
            str(release_manifest_json),
            "--output-md",
            str(release_manifest_md),
        ]
        print("[routine] release manifest")
        release_manifest_rc = _run(release_manifest_cmd, timeout=1200)
        manifest_payload["release_manifest"] = {
            "enabled": True,
            "exit_code": release_manifest_rc,
            "report_json": str(release_manifest_json),
            "report_md": str(release_manifest_md),
            "command": release_manifest_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if release_manifest_rc != 0:
            print(f"[routine] release manifest failed (exit={release_manifest_rc})")
            return release_manifest_rc
    else:
        _write_json(session_manifest, manifest_payload)

    if args.release_checklist:
        release_checklist_json = SESSION_ROOT / label / "release_checklist.json"
        release_checklist_md = SESSION_ROOT / label / "release_checklist.md"
        release_checklist_cmd = [
            sys.executable,
            str(RELEASE_CHECKLIST_TOOL),
            "--session-manifest",
            str(session_manifest),
            "--output-json",
            str(release_checklist_json),
            "--output-md",
            str(release_checklist_md),
        ]
        if args.package_smoke:
            release_checklist_cmd.append("--require-package")
            matrix_json_path = SESSION_ROOT / label / "package_matrix.json"
            if matrix_json_path.exists():
                release_checklist_cmd += [
                    "--package-matrix-json",
                    str(matrix_json_path),
                ]
            manifest_json_path = SESSION_ROOT / label / "release_manifest.json"
            if manifest_json_path.exists():
                release_checklist_cmd += [
                    "--release-manifest-json",
                    str(manifest_json_path),
                ]
        print("[routine] release checklist")
        release_checklist_rc = _run(release_checklist_cmd, timeout=1200)
        manifest_payload["release_checklist"] = {
            "enabled": True,
            "exit_code": release_checklist_rc,
            "report_json": str(release_checklist_json),
            "report_md": str(release_checklist_md),
            "command": release_checklist_cmd,
        }
        _write_json(session_manifest, manifest_payload)
        if release_checklist_rc != 0:
            print(f"[routine] release checklist failed (exit={release_checklist_rc})")
            return release_checklist_rc
    else:
        _write_json(session_manifest, manifest_payload)

    compare_report: Path | None = None
    if args.no_compare_prev:
        _update_session_benchmark_doc(label, compare_report)
        print(f"[routine] docs updated: {SESSION_BENCH_DOC}")
        print("[routine] compare skipped by flag")
        return 0

    previous = _latest_routine_labels(label)
    if not previous:
        _update_session_benchmark_doc(label, compare_report)
        print(f"[routine] docs updated: {SESSION_BENCH_DOC}")
        print("[routine] no previous routine snapshot found; compare skipped")
        return 0

    before = previous[-1]
    compare_output = SESSION_ROOT / f"compare_{before}_vs_{label}.md"
    compare_report = compare_output
    cmp_cmd = [
        sys.executable,
        str(SESSION_TOOL),
        "compare",
        "--before",
        before,
        "--after",
        label,
        "--output",
        str(compare_output),
    ]
    print(f"[routine] compare {before} -> {label}")
    cmp_rc = _run(cmp_cmd, timeout=600)
    if cmp_rc != 0:
        print(f"[routine] compare failed (exit={cmp_rc})")
        return cmp_rc

    _update_session_benchmark_doc(label, compare_report)
    print(f"[routine] docs updated: {SESSION_BENCH_DOC}")
    print(f"[routine] compare report: {compare_output}")
    return 0

__all__ = [name for name in globals() if not name.startswith("__")]

from __future__ import annotations

from pathlib import Path

try:
    from .stabilization_routine_common import *
except ImportError:
    from stabilization_routine_common import *

def _build_auto_section(
    current_label: str,
    compare_report: Path | None,
) -> str:
    labels = _all_routine_labels()
    if current_label not in labels:
        labels.append(current_label)
        labels = sorted(labels)

    current_manifest = _read_json(SESSION_ROOT / current_label / "session.json")
    exit_codes = current_manifest.get("exit_codes", {})
    verifier_summary = current_manifest.get("verifier_summary", {})
    god_summary = current_manifest.get("god_object_summary", {})
    bench_cmd = current_manifest.get("commands", {}).get("benchmark", [])
    check_leaks_on = "--check-leaks" in bench_cmd
    leak_threshold = 0
    if "--leak-threshold" in bench_cmd:
        idx = bench_cmd.index("--leak-threshold")
        if idx + 1 < len(bench_cmd):
            try:
                leak_threshold = int(bench_cmd[idx + 1])
            except ValueError:
                leak_threshold = 0

    recent = labels[-8:]
    lines: list[str] = []
    lines.append("## Latest Routine Snapshot (Auto)")
    lines.append("")
    lines.append(f"- label: `{current_label}`")
    lines.append(f"- timestamp: `{_display_timestamp(current_manifest)}`")
    lines.append(
        f"- benchmark: exit `{exit_codes.get('benchmark')}` "
        f"(performance + checksum parity)"
    )
    lines.append(
        f"- regression: exit `{exit_codes.get('regression')}` "
        f"(compile/runtime + leak baseline check)"
    )
    lines.append(
        f"- strict verifier: `{verifier_summary.get('passed')}/"
        f"{verifier_summary.get('total')}` "
        f"(exit `{exit_codes.get('verifier')}`)"
    )
    lines.append(
        f"- god-object audit: `{god_summary.get('candidate_count')}` candidates "
        f"out of `{god_summary.get('scanned_files')}` files "
        f"(exit `{exit_codes.get('god_object_audit')}`)"
    )
    lines.append(
        f"- memory/leak mode: `sample_memory={bool(current_manifest.get('sample_memory'))}`, "
        f"`check_leaks={check_leaks_on}`, `leak_threshold={leak_threshold}`"
    )
    check_report_exit = current_manifest.get("exit_codes", {}).get("report_checks")
    lines.append(f"- check-report capture exit: `{check_report_exit}`")
    check_summary = current_manifest.get("check_report_summary", {})
    if isinstance(check_summary, dict) and check_summary:
        preferred_keys = (
            "overflow:inserted",
            "overflow:elided",
            "bounds:inserted",
            "bounds:elided",
        )
        parts: list[str] = []
        for key in preferred_keys:
            if key in check_summary:
                parts.append(f"{key}={int(check_summary.get(key, 0) or 0)}")
        if not parts:
            for key in sorted(check_summary)[:6]:
                parts.append(f"{key}={int(check_summary.get(key, 0) or 0)}")
        lines.append(f"- check-report summary: `{', '.join(parts)}`")
    else:
        lines.append("- check-report summary: `none`")
    format_report_exit = current_manifest.get("exit_codes", {}).get("report_format")
    lines.append(f"- format-report capture exit: `{format_report_exit}`")
    format_summary = current_manifest.get("format_report_summary", {})
    if isinstance(format_summary, dict) and format_summary:
        preferred_keys = (
            "print:direct_writer",
            "print:format_fallback",
            "interpolation:direct_writer",
            "interpolation:format_fallback",
            "fallback:printf",
            "fallback:snprintf",
            "fallback:sprintf",
        )
        parts: list[str] = []
        for key in preferred_keys:
            if key in format_summary:
                parts.append(f"{key}={int(format_summary.get(key, 0) or 0)}")
        if not parts:
            for key in sorted(format_summary)[:6]:
                parts.append(f"{key}={int(format_summary.get(key, 0) or 0)}")
        lines.append(f"- format-report summary: `{', '.join(parts)}`")
    else:
        lines.append("- format-report summary: `none`")
    runtime_needs_exit = current_manifest.get("exit_codes", {}).get("runtime_needs")
    lines.append(f"- runtime-needs capture exit: `{runtime_needs_exit}`")
    lines.append(
        f"- runtime-needs totals: "
        f"`helpers={int(current_manifest.get('runtime_needs_total_helper_count', 0) or 0)}`, "
        f"`generated_c_bytes={int(current_manifest.get('runtime_needs_total_c_bytes', 0) or 0)}`"
    )
    effect_policy_exit = current_manifest.get("exit_codes", {}).get("effect_policy")
    lines.append(f"- effect-policy capture exit: `{effect_policy_exit}`")
    lines.append(
        f"- effect-policy totals: "
        f"`violations={int(current_manifest.get('effect_policy_total_violations', 0) or 0)}`"
    )
    env_check = current_manifest.get("env_check", {})
    if env_check.get("enabled"):
        lines.append(f"- environment check exit: `{env_check.get('exit_code')}`")
        env_json = env_check.get("report_json")
        if isinstance(env_json, str):
            try:
                rel_env_json = Path(env_json).relative_to(REPO_ROOT)
                lines.append(f"- environment check report: `{rel_env_json.as_posix()}`")
            except ValueError:
                lines.append(f"- environment check report: `{env_json}`")
    else:
        lines.append("- environment check: `disabled`")
    phase_profile = current_manifest.get("phase_profile", {})
    if phase_profile.get("enabled"):
        lines.append(
            f"- compiler phase profile exit: `{phase_profile.get('exit_code')}`"
        )
        phase_md = phase_profile.get("report_md")
        if isinstance(phase_md, str):
            try:
                rel_phase_md = Path(phase_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- compiler phase profile report: `{rel_phase_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- compiler phase profile report: `{phase_md}`")
    else:
        lines.append("- compiler phase profile: `disabled`")
    language_profile = current_manifest.get("language_surface_profile", {})
    if language_profile.get("enabled"):
        lines.append(
            f"- language-surface profile exit: `{language_profile.get('exit_code')}`"
        )
        lang_md = language_profile.get("report_md")
        if isinstance(lang_md, str):
            try:
                rel_lang_md = Path(lang_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- language-surface profile report: `{rel_lang_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- language-surface profile report: `{lang_md}`")
    else:
        lines.append("- language-surface profile: `disabled`")
    durability = current_manifest.get("durability_stress", {})
    if durability.get("enabled"):
        lines.append(f"- durability stress exit: `{durability.get('exit_code')}`")
        dur_md = durability.get("report_md")
        if isinstance(dur_md, str):
            try:
                rel_dur_md = Path(dur_md).relative_to(REPO_ROOT)
                lines.append(f"- durability stress report: `{rel_dur_md.as_posix()}`")
            except ValueError:
                lines.append(f"- durability stress report: `{dur_md}`")
    else:
        lines.append("- durability stress: `disabled`")
    strict_surface = current_manifest.get("strict_surface_suite", {})
    if strict_surface.get("enabled"):
        lines.append(
            f"- strict surface suite exit: `{strict_surface.get('exit_code')}`"
        )
        strict_md = strict_surface.get("report_md")
        if isinstance(strict_md, str):
            try:
                rel_strict_md = Path(strict_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- strict surface suite report: `{rel_strict_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- strict surface suite report: `{strict_md}`")
    else:
        lines.append("- strict surface suite: `disabled`")
    adapt_teardown = current_manifest.get("adapt_teardown", {})
    if adapt_teardown.get("enabled"):
        lines.append(f"- ADAPT teardown exit: `{adapt_teardown.get('exit_code')}`")
        adapt_md = adapt_teardown.get("report_md")
        if isinstance(adapt_md, str):
            try:
                rel_adapt_md = Path(adapt_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- ADAPT teardown report: `{rel_adapt_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- ADAPT teardown report: `{adapt_md}`")
        summary = adapt_teardown.get("summary", {})
        if isinstance(summary, dict) and summary:
            lines.append(
                "- ADAPT teardown summary: "
                f"`no_live={int(summary.get('no_live', 0) or 0)}, "
                f"intentional_cache={int(summary.get('intentional_cache', 0) or 0)}, "
                f"true_leak={int(summary.get('true_leak', 0) or 0)}, "
                f"harness_artifact={int(summary.get('harness_artifact', 0) or 0)}`"
            )
    else:
        lines.append("- ADAPT teardown: `disabled`")
    if compare_report is not None:
        rel_compare = compare_report.relative_to(REPO_ROOT)
        lines.append(f"- compare report: `{rel_compare.as_posix()}`")
    else:
        lines.append("- compare report: `none (first routine snapshot)`")
    lines.append(
        f"- session manifest: `benchmarks/sessions/{current_label}/session.json`"
    )
    old_main = current_manifest.get("old_main_compare", {})
    if old_main.get("enabled"):
        lines.append(f"- old-main compare exit: `{old_main.get('exit_code')}`")
        old_md = old_main.get("report_md")
        if isinstance(old_md, str):
            try:
                rel_old_md = Path(old_md).relative_to(REPO_ROOT)
                lines.append(f"- old-main compare report: `{rel_old_md.as_posix()}`")
            except ValueError:
                lines.append(f"- old-main compare report: `{old_md}`")
    else:
        lines.append("- old-main compare: `disabled`")
    pkg_smoke = current_manifest.get("package_smoke", {})
    if pkg_smoke.get("enabled"):
        lines.append(f"- package smoke exit: `{pkg_smoke.get('exit_code')}`")
        pkg_md = pkg_smoke.get("report_md")
        if isinstance(pkg_md, str):
            try:
                rel_pkg_md = Path(pkg_md).relative_to(REPO_ROOT)
                lines.append(f"- package smoke report: `{rel_pkg_md.as_posix()}`")
            except ValueError:
                lines.append(f"- package smoke report: `{pkg_md}`")
    else:
        lines.append("- package smoke: `disabled`")
    pkg_matrix = current_manifest.get("package_matrix", {})
    if pkg_matrix.get("enabled"):
        lines.append(f"- package matrix exit: `{pkg_matrix.get('exit_code')}`")
        matrix_md = pkg_matrix.get("report_md")
        if isinstance(matrix_md, str):
            try:
                rel_matrix_md = Path(matrix_md).relative_to(REPO_ROOT)
                lines.append(f"- package matrix report: `{rel_matrix_md.as_posix()}`")
            except ValueError:
                lines.append(f"- package matrix report: `{matrix_md}`")
    else:
        lines.append("- package matrix: `disabled`")
    pkg_extract = current_manifest.get("package_extract_smoke", {})
    if pkg_extract.get("enabled"):
        lines.append(f"- package extract smoke exit: `{pkg_extract.get('exit_code')}`")
        extract_md = pkg_extract.get("report_md")
        if isinstance(extract_md, str):
            try:
                rel_extract_md = Path(extract_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- package extract smoke report: `{rel_extract_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- package extract smoke report: `{extract_md}`")
    else:
        lines.append("- package extract smoke: `disabled`")
    variant = current_manifest.get("variant_recommendation", {})
    if variant.get("enabled"):
        lines.append(f"- variant recommendation exit: `{variant.get('exit_code')}`")
        variant_md = variant.get("report_md")
        if isinstance(variant_md, str):
            try:
                rel_variant_md = Path(variant_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- variant recommendation report: `{rel_variant_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- variant recommendation report: `{variant_md}`")
    else:
        lines.append("- variant recommendation: `disabled`")
    release_manifest = current_manifest.get("release_manifest", {})
    if release_manifest.get("enabled"):
        lines.append(f"- release manifest exit: `{release_manifest.get('exit_code')}`")
        manifest_md = release_manifest.get("report_md")
        if isinstance(manifest_md, str):
            try:
                rel_manifest_md = Path(manifest_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- release manifest report: `{rel_manifest_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- release manifest report: `{manifest_md}`")
    else:
        lines.append("- release manifest: `disabled`")
    release_checklist = current_manifest.get("release_checklist", {})
    if release_checklist.get("enabled"):
        lines.append(
            f"- release checklist exit: `{release_checklist.get('exit_code')}`"
        )
        checklist_md = release_checklist.get("report_md")
        if isinstance(checklist_md, str):
            try:
                rel_checklist_md = Path(checklist_md).relative_to(REPO_ROOT)
                lines.append(
                    f"- release checklist report: `{rel_checklist_md.as_posix()}`"
                )
            except ValueError:
                lines.append(f"- release checklist report: `{checklist_md}`")
    else:
        lines.append("- release checklist: `disabled`")
    lines.append("")
    lines.append("### Recent Routine History")
    lines.append("")
    lines.append(
        "| label | verifier | god candidates | benchmark | regression | check-report | format-report | runtime-needs | effect-policy | env-check | old-main | package-smoke | package-extract | package-matrix | variant | release-manifest | release-checklist | phase-profile | language-profile | strict-surface | durability | sample memory |"
    )
    lines.append(
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    )
    for label in reversed(recent):
        manifest = _read_json(SESSION_ROOT / label / "session.json")
        ex = manifest.get("exit_codes", {})
        vs = manifest.get("verifier_summary", {})
        gs = manifest.get("god_object_summary", {})
        env_exit = manifest.get("env_check", {}).get("exit_code")
        if env_exit is None and manifest.get("env_check", {}).get("enabled"):
            env_exit = "?"
        if env_exit is None:
            env_exit = "-"
        old_main_exit = manifest.get("old_main_compare", {}).get("exit_code")
        if old_main_exit is None and manifest.get("old_main_compare", {}).get(
            "enabled"
        ):
            old_main_exit = "?"
        if old_main_exit is None:
            old_main_exit = "-"
        package_smoke_exit = manifest.get("package_smoke", {}).get("exit_code")
        if package_smoke_exit is None and manifest.get("package_smoke", {}).get(
            "enabled"
        ):
            package_smoke_exit = "?"
        if package_smoke_exit is None:
            package_smoke_exit = "-"
        package_extract_exit = manifest.get("package_extract_smoke", {}).get(
            "exit_code"
        )
        if package_extract_exit is None and manifest.get(
            "package_extract_smoke", {}
        ).get("enabled"):
            package_extract_exit = "?"
        if package_extract_exit is None:
            package_extract_exit = "-"
        package_matrix_exit = manifest.get("package_matrix", {}).get("exit_code")
        if package_matrix_exit is None and manifest.get("package_matrix", {}).get(
            "enabled"
        ):
            package_matrix_exit = "?"
        if package_matrix_exit is None:
            package_matrix_exit = "-"
        variant_exit = manifest.get("variant_recommendation", {}).get("exit_code")
        if variant_exit is None and manifest.get("variant_recommendation", {}).get(
            "enabled"
        ):
            variant_exit = "?"
        if variant_exit is None:
            variant_exit = "-"
        phase_exit = manifest.get("phase_profile", {}).get("exit_code")
        if phase_exit is None and manifest.get("phase_profile", {}).get("enabled"):
            phase_exit = "?"
        if phase_exit is None:
            phase_exit = "-"
        lang_exit = manifest.get("language_surface_profile", {}).get("exit_code")
        if lang_exit is None and manifest.get("language_surface_profile", {}).get(
            "enabled"
        ):
            lang_exit = "?"
        if lang_exit is None:
            lang_exit = "-"
        strict_exit = manifest.get("strict_surface_suite", {}).get("exit_code")
        if strict_exit is None and manifest.get("strict_surface_suite", {}).get(
            "enabled"
        ):
            strict_exit = "?"
        if strict_exit is None:
            strict_exit = "-"
        dur_exit = manifest.get("durability_stress", {}).get("exit_code")
        if dur_exit is None and manifest.get("durability_stress", {}).get("enabled"):
            dur_exit = "?"
        if dur_exit is None:
            dur_exit = "-"
        release_manifest_exit = manifest.get("release_manifest", {}).get("exit_code")
        if release_manifest_exit is None and manifest.get("release_manifest", {}).get(
            "enabled"
        ):
            release_manifest_exit = "?"
        if release_manifest_exit is None:
            release_manifest_exit = "-"
        release_checklist_exit = manifest.get("release_checklist", {}).get("exit_code")
        if release_checklist_exit is None and manifest.get("release_checklist", {}).get(
            "enabled"
        ):
            release_checklist_exit = "?"
        if release_checklist_exit is None:
            release_checklist_exit = "-"
        check_exit = ex.get("report_checks")
        if check_exit is None:
            check_exit = "-"
        format_exit = ex.get("report_format")
        if format_exit is None:
            format_exit = "-"
        runtime_needs_exit = ex.get("runtime_needs")
        if runtime_needs_exit is None:
            runtime_needs_exit = "-"
        effect_policy_exit = ex.get("effect_policy")
        if effect_policy_exit is None:
            effect_policy_exit = "-"
        lines.append(
            f"| `{label}` | `{vs.get('passed')}/{vs.get('total')}` | "
            f"`{gs.get('candidate_count')}` | `{ex.get('benchmark')}` | "
            f"`{ex.get('regression')}` | `{check_exit}` | `{format_exit}` | `{runtime_needs_exit}` | `{effect_policy_exit}` | `{env_exit}` | `{old_main_exit}` | `{package_smoke_exit}` | "
            f"`{package_extract_exit}` | `{package_matrix_exit}` | `{variant_exit}` | `{release_manifest_exit}` | `{release_checklist_exit}` | `{phase_exit}` | "
            f"`{lang_exit}` | `{strict_exit}` | `{dur_exit}` | "
            f"`{bool(manifest.get('sample_memory'))}` |"
        )
    lines.append("")
    return "\n".join(lines)

def _update_session_benchmark_doc(
    current_label: str, compare_report: Path | None
) -> None:
    auto_section = _build_auto_section(current_label, compare_report)
    block = f"{AUTO_SECTION_START}\n" f"{auto_section}\n" f"{AUTO_SECTION_END}"
    if not SESSION_BENCH_DOC.exists():
        SESSION_BENCH_DOC.parent.mkdir(parents=True, exist_ok=True)
        SESSION_BENCH_DOC.write_text(block + "\n", encoding="utf-8")
        return
    text = SESSION_BENCH_DOC.read_text(encoding="utf-8")
    if AUTO_SECTION_START in text and AUTO_SECTION_END in text:
        start_idx = text.index(AUTO_SECTION_START)
        end_idx = text.index(AUTO_SECTION_END) + len(AUTO_SECTION_END)
        new_text = text[:start_idx] + block + text[end_idx:]
    else:
        suffix = "" if text.endswith("\n") else "\n"
        new_text = text + suffix + "\n" + block + "\n"
    SESSION_BENCH_DOC.write_text(new_text, encoding="utf-8")

__all__ = [name for name in globals() if not name.startswith("__")]

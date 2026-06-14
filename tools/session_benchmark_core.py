from __future__ import annotations

try:
    from .session_benchmark_common import *
except ImportError:
    from session_benchmark_common import *

try:
    from .session_benchmark_snapshots import *
except ImportError:
    from session_benchmark_snapshots import *

def _collect_capture(
    label: str,
    runs: int,
    warmup: int,
    cases: list[str],
    impls: list[str],
    sample_memory: bool = False,
    check_leaks: bool = False,
    leak_threshold: int = 0,
) -> int:
    session_dir = SESSION_ROOT / label
    session_dir.mkdir(parents=True, exist_ok=True)

    bench_md = session_dir / "benchmark_results.md"
    bench_json = bench_md.with_suffix(".json")
    reg_json = session_dir / "regression_snapshot.json"
    reg_baseline = _regression_baseline_path()
    god_json = session_dir / "god_object_audit.json"
    god_md = session_dir / "god_object_audit.md"
    verifier_log = session_dir / "verifier.log"
    manifest = session_dir / "session.json"

    bench_cmd = [
        sys.executable,
        str(BENCH_ROOT / "run_benchmarks.py"),
        "--runs",
        str(runs),
        "--warmup",
        str(warmup),
        "--output",
        str(bench_md),
    ]
    # Keep output parity check on for session captures.
    bench_cmd.append("--check-output")
    if sample_memory:
        bench_cmd.append("--sample-memory")
    if check_leaks:
        bench_cmd.extend(["--check-leaks", "--leak-threshold", str(leak_threshold)])
    for case in cases:
        bench_cmd += ["--case", case]
    for impl in impls:
        bench_cmd += ["--impl", impl]

    b_rc, b_out, b_err = _run(bench_cmd, timeout=1800)
    (session_dir / "benchmark.log").write_text(
        b_out + ("\n" + b_err if b_err else ""),
        encoding="utf-8",
    )

    reg_check_cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "regression_check.py"),
        "--baseline",
        str(reg_baseline),
    ]
    r_chk_rc, r_chk_out, r_chk_err = _run(reg_check_cmd, timeout=900)
    (session_dir / "regression.log").write_text(
        r_chk_out + ("\n" + r_chk_err if r_chk_err else ""),
        encoding="utf-8",
    )

    reg_save_cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "regression_check.py"),
        "--baseline",
        str(reg_baseline),
        "--save",
        str(reg_json),
    ]
    r_save_rc, r_save_out, r_save_err = _run(reg_save_cmd, timeout=900)
    (session_dir / "regression_snapshot.log").write_text(
        r_save_out + ("\n" + r_save_err if r_save_err else ""),
        encoding="utf-8",
    )

    ver_cmd = [
        sys.executable,
        "-m",
        "verifier.cli",
        "-d",
        "source",
        "--preset",
        "strict",
    ]
    v_rc, v_out, v_err = _run(ver_cmd, timeout=1200)
    verifier_log.write_text(v_out + ("\n" + v_err if v_err else ""), encoding="utf-8")
    verifier_summary = _parse_verifier_summary(v_out + "\n" + v_err)

    god_cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "god_object_audit.py"),
        str(REPO_ROOT / "source"),
        str(REPO_ROOT / "ailang.py"),
        "--json-output",
        str(god_json),
        "--md-output",
        str(god_md),
    ]
    g_rc, g_out, g_err = _run(god_cmd, timeout=300)
    (session_dir / "god_object_audit.log").write_text(
        g_out + ("\n" + g_err if g_err else ""),
        encoding="utf-8",
    )

    god_summary: dict[str, Any] = {"scanned_files": None, "candidate_count": None}
    if god_json.exists():
        parsed = _read_json(god_json)
        god_summary = {
            "scanned_files": parsed.get("scanned_files"),
            "candidate_count": parsed.get("candidate_count"),
        }

    check_report_rc, check_report_json, check_report_snapshot = (
        _collect_check_report_snapshot(
            session_dir=session_dir,
            program_names=list(CHECK_REPORT_PROGRAMS),
        )
    )
    format_report_rc, format_report_json, format_report_snapshot = (
        _collect_format_report_snapshot(
            session_dir=session_dir,
            program_names=list(CHECK_REPORT_PROGRAMS),
        )
    )
    runtime_needs_rc, runtime_needs_json, runtime_needs_snapshot = (
        _collect_runtime_needs_snapshot(
            session_dir=session_dir,
            program_names=list(CHECK_REPORT_PROGRAMS),
        )
    )
    effect_policy_rc, effect_policy_json, effect_policy_snapshot = (
        _collect_effect_policy_snapshot(
            session_dir=session_dir,
            program_names=list(CHECK_REPORT_PROGRAMS),
        )
    )

    payload = {
        "label": label,
        "timestamp": time.strftime(DATE_ISO_FMT),
        "timestamp_iso": time.strftime(DATE_ISO_FMT),
        "timestamp_human": time.strftime(DATE_HUMAN_FMT),
        "sample_memory": sample_memory,
        "commands": {
            "benchmark": bench_cmd,
            "regression": reg_check_cmd,
            "regression_snapshot": reg_save_cmd,
            "verifier": ver_cmd,
            "god_object_audit": god_cmd,
            "report_checks": "ailang.py <corpus>.ail --report-checks-json",
            "report_format": "ailang.py <corpus>.ail --format-report-json",
            "runtime_needs": "ailang.py <corpus>.ail --runtime-needs-json",
            "effect_policy": "ailang.py <corpus>.ail --effect-policy-json",
        },
        "paths": {
            "benchmark_md": str(bench_md),
            "benchmark_json": str(bench_json),
            "regression_json": str(reg_json),
            "verifier_log": str(verifier_log),
            "god_object_json": str(god_json),
            "god_object_md": str(god_md),
            "check_report_json": str(check_report_json),
            "format_report_json": str(format_report_json),
            "runtime_needs_json": str(runtime_needs_json),
            "effect_policy_json": str(effect_policy_json),
        },
        "exit_codes": {
            "benchmark": b_rc,
            "regression": r_chk_rc,
            "regression_snapshot": r_save_rc,
            "verifier": v_rc,
            "god_object_audit": g_rc,
            "report_checks": check_report_rc,
            "report_format": format_report_rc,
            "runtime_needs": runtime_needs_rc,
            "effect_policy": effect_policy_rc,
        },
        "verifier_summary": {
            "passed": verifier_summary[0] if verifier_summary else None,
            "total": verifier_summary[1] if verifier_summary else None,
        },
        "god_object_summary": god_summary,
        "check_report_summary": check_report_snapshot.get("summary_totals", {}),
        "check_report_decision_count": check_report_snapshot.get(
            "decision_count_total", 0
        ),
        "format_report_summary": format_report_snapshot.get("summary_totals", {}),
        "format_report_decision_count": format_report_snapshot.get(
            "decision_count_total", 0
        ),
        "runtime_needs_total_helper_count": runtime_needs_snapshot.get(
            "total_helper_count", 0
        ),
        "runtime_needs_total_c_bytes": runtime_needs_snapshot.get(
            "total_generated_c_bytes", 0
        ),
        "runtime_needs_family_program_counts": runtime_needs_snapshot.get(
            "family_program_counts", {}
        ),
        "effect_policy_total_violations": effect_policy_snapshot.get(
            "violation_count_total", 0
        ),
        "effect_policy_by_kind": effect_policy_snapshot.get("by_kind_totals", {}),
        "effect_policy_by_effect": effect_policy_snapshot.get("by_effect_totals", {}),
    }
    _save_json(manifest, payload)

    print(f"session: {label}")
    print(f"  benchmark exit={b_rc}")
    print(f"  regression exit={r_chk_rc}")
    print(f"  regression snapshot exit={r_save_rc}")
    print(f"  verifier exit={v_rc}")
    print(f"  god-object audit exit={g_rc}")
    print(f"  report-checks exit={check_report_rc}")
    print(f"  report-format exit={format_report_rc}")
    print(f"  runtime-needs exit={runtime_needs_rc}")
    print(f"  effect-policy exit={effect_policy_rc}")
    print(f"  manifest: {manifest}")
    if (
        b_rc != 0
        or r_chk_rc != 0
        or r_save_rc != 0
        or v_rc != 0
        or g_rc != 0
        or check_report_rc != 0
        or format_report_rc != 0
        or runtime_needs_rc != 0
        or effect_policy_rc != 0
    ):
        return 1
    return 0
def _compare_sessions(before_label: str, after_label: str, output: Path) -> int:
    before_dir = SESSION_ROOT / before_label
    after_dir = SESSION_ROOT / after_label
    before_manifest = _read_json(before_dir / "session.json")
    after_manifest = _read_json(after_dir / "session.json")

    before_bench = _read_json(Path(before_manifest["paths"]["benchmark_json"]))
    after_bench = _read_json(Path(after_manifest["paths"]["benchmark_json"]))
    before_reg = _read_json(Path(before_manifest["paths"]["regression_json"]))
    after_reg = _read_json(Path(after_manifest["paths"]["regression_json"]))
    before_god = _read_json_optional(
        Path(before_manifest.get("paths", {}).get("god_object_json", ""))
    )
    after_god = _read_json_optional(
        Path(after_manifest.get("paths", {}).get("god_object_json", ""))
    )
    before_checks = _read_json_optional(
        Path(before_manifest.get("paths", {}).get("check_report_json", ""))
    )
    after_checks = _read_json_optional(
        Path(after_manifest.get("paths", {}).get("check_report_json", ""))
    )
    before_format = _read_json_optional(
        Path(before_manifest.get("paths", {}).get("format_report_json", ""))
    )
    after_format = _read_json_optional(
        Path(after_manifest.get("paths", {}).get("format_report_json", ""))
    )
    before_runtime = _read_json_optional(
        Path(before_manifest.get("paths", {}).get("runtime_needs_json", ""))
    )
    after_runtime = _read_json_optional(
        Path(after_manifest.get("paths", {}).get("runtime_needs_json", ""))
    )
    before_effect = _read_json_optional(
        Path(before_manifest.get("paths", {}).get("effect_policy_json", ""))
    )
    after_effect = _read_json_optional(
        Path(after_manifest.get("paths", {}).get("effect_policy_json", ""))
    )

    lines: list[str] = []
    lines.append(f"# Session Comparison: `{before_label}` -> `{after_label}`")
    lines.append("")
    lines.append("## Verifier")
    lines.append("")
    b_vs = before_manifest.get("verifier_summary", {})
    a_vs = after_manifest.get("verifier_summary", {})
    lines.append(
        f"- before: `{b_vs.get('passed')}/{b_vs.get('total')}` "
        f"(exit `{before_manifest['exit_codes']['verifier']}`)"
    )
    lines.append(
        f"- after: `{a_vs.get('passed')}/{a_vs.get('total')}` "
        f"(exit `{after_manifest['exit_codes']['verifier']}`)"
    )
    lines.append("")

    lines.append("## Regression Gate")
    lines.append("")
    lines.append(
        f"- before exit: `{before_manifest.get('exit_codes', {}).get('regression')}`"
    )
    lines.append(
        f"- after exit: `{after_manifest.get('exit_codes', {}).get('regression')}`"
    )
    lines.append("")

    lines.append("## God-Object Audit")
    lines.append("")
    b_gs = before_manifest.get("god_object_summary", {})
    a_gs = after_manifest.get("god_object_summary", {})
    lines.append(
        f"- before: `{b_gs.get('candidate_count')}` candidates "
        f"out of `{b_gs.get('scanned_files')}` files "
        f"(exit `{before_manifest.get('exit_codes', {}).get('god_object_audit')}`)"
    )
    lines.append(
        f"- after: `{a_gs.get('candidate_count')}` candidates "
        f"out of `{a_gs.get('scanned_files')}` files "
        f"(exit `{after_manifest.get('exit_codes', {}).get('god_object_audit')}`)"
    )
    lines.append("")
    lines.append("| path | before flagged | after flagged |")
    lines.append("| --- | --- | --- |")
    b_candidates = {
        row["path"]: row for row in before_god.get("candidates", []) if "path" in row
    }
    a_candidates = {
        row["path"]: row for row in after_god.get("candidates", []) if "path" in row
    }
    for path in sorted(set(b_candidates) | set(a_candidates)):
        lines.append(
            f"| {path} | {'yes' if path in b_candidates else 'no'} | "
            f"{'yes' if path in a_candidates else 'no'} |"
        )
    lines.append("")

    lines.append("## Performance")
    lines.append("")
    lines.append(
        "| case | impl | before median ms | after median ms | delta ms | ratio | status |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")

    b_results = before_bench.get("results", {})
    a_results = after_bench.get("results", {})
    for case in sorted(set(b_results) & set(a_results)):
        b_case = b_results[case]
        a_case = a_results[case]
        for impl in sorted(set(b_case) & set(a_case)):
            b_rec = b_case[impl]
            a_rec = a_case[impl]
            b_med = _median(b_rec.get("runs_ms"))
            a_med = _median(a_rec.get("runs_ms"))
            if b_med is None or a_med is None:
                lines.append(
                    f"| {case} | {impl} | n/a | n/a | n/a | n/a | "
                    f"{b_rec.get('status')} -> {a_rec.get('status')} |"
                )
                continue
            delta = a_med - b_med
            ratio = (a_med / b_med) if b_med > 0 else 0.0
            status = f"{b_rec.get('status')} -> {a_rec.get('status')}"
            if b_rec.get("checksum") != a_rec.get("checksum"):
                status += " (checksum changed)"
            lines.append(
                f"| {case} | {impl} | {b_med:.3f} | {a_med:.3f} | "
                f"{delta:+.3f} | {ratio:.2f}x | {status} |"
            )

    lines.append("")
    lines.append("## Safety and Leak Signals")
    lines.append("")
    lines.append(
        "| program | backend | compile_ok | runtime_ok | stdout changed | "
        "live leak delta (B) | peak RSS delta (KiB) |"
    )
    lines.append("| --- | --- | --- | --- | --- | ---: | ---: |")

    b_prog = {p["name"]: p for p in before_reg.get("programs", [])}
    a_prog = {p["name"]: p for p in after_reg.get("programs", [])}
    for name in sorted(set(b_prog) & set(a_prog)):
        for backend in ("llvm", "c"):
            b_back = b_prog[name].get(backend)
            a_back = a_prog[name].get(backend)
            if not b_back or not a_back:
                continue
            stdout_changed = b_back.get("stdout_first_line") != a_back.get(
                "stdout_first_line"
            )
            b_leak = b_back.get("leak_live_bytes")
            a_leak = a_back.get("leak_live_bytes")
            leak_delta = (
                (a_leak - b_leak)
                if isinstance(a_leak, int) and isinstance(b_leak, int)
                else 0
            )
            b_peak = b_back.get("peak_rss_bytes")
            a_peak = a_back.get("peak_rss_bytes")
            peak_delta = (
                (a_peak - b_peak)
                if isinstance(a_peak, int) and isinstance(b_peak, int)
                else 0
            )
            lines.append(
                f"| {name} | {backend} | {b_back.get('compile_ok')} -> {a_back.get('compile_ok')} | "
                f"{b_back.get('runtime_ok')} -> {a_back.get('runtime_ok')} | "
                f"{stdout_changed} | {leak_delta:+d} | {peak_delta / 1024:+.1f} |"
            )

    lines.append("")
    lines.append("## Check Decision Counters")
    lines.append("")
    lines.append(
        f"- before total decisions: `{before_checks.get('decision_count_total', 0)}`"
    )
    lines.append(
        f"- after total decisions: `{after_checks.get('decision_count_total', 0)}`"
    )
    lines.append("")
    lines.append("| counter | before | after | delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    b_summary = before_checks.get("summary_totals", {})
    a_summary = after_checks.get("summary_totals", {})
    for key in sorted(set(b_summary) | set(a_summary)):
        b_val = int(b_summary.get(key, 0) or 0)
        a_val = int(a_summary.get(key, 0) or 0)
        lines.append(f"| {key} | {b_val} | {a_val} | {a_val - b_val:+d} |")

    lines.append("")
    lines.append("| program | before status | after status |")
    lines.append("| --- | --- | --- |")
    b_programs = before_checks.get("programs", {})
    a_programs = after_checks.get("programs", {})
    for name in sorted(set(b_programs) | set(a_programs)):
        b_status = (
            b_programs.get(name, {}).get("status", "n/a")
            if isinstance(b_programs, dict)
            else "n/a"
        )
        a_status = (
            a_programs.get(name, {}).get("status", "n/a")
            if isinstance(a_programs, dict)
            else "n/a"
        )
        lines.append(f"| {name} | {b_status} | {a_status} |")

    lines.append("")
    lines.append("## Format Specialization Counters")
    lines.append("")
    lines.append(
        f"- before total decisions: `{before_format.get('decision_count_total', 0)}`"
    )
    lines.append(
        f"- after total decisions: `{after_format.get('decision_count_total', 0)}`"
    )
    lines.append("")
    lines.append("| counter | before | after | delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    b_fmt_summary = before_format.get("summary_totals", {})
    a_fmt_summary = after_format.get("summary_totals", {})
    for key in sorted(set(b_fmt_summary) | set(a_fmt_summary)):
        b_val = int(b_fmt_summary.get(key, 0) or 0)
        a_val = int(a_fmt_summary.get(key, 0) or 0)
        lines.append(f"| {key} | {b_val} | {a_val} | {a_val - b_val:+d} |")

    lines.append("")
    lines.append("| program | before status | after status |")
    lines.append("| --- | --- | --- |")
    b_fmt_programs = before_format.get("programs", {})
    a_fmt_programs = after_format.get("programs", {})
    for name in sorted(set(b_fmt_programs) | set(a_fmt_programs)):
        b_status = (
            b_fmt_programs.get(name, {}).get("status", "n/a")
            if isinstance(b_fmt_programs, dict)
            else "n/a"
        )
        a_status = (
            a_fmt_programs.get(name, {}).get("status", "n/a")
            if isinstance(a_fmt_programs, dict)
            else "n/a"
        )
        lines.append(f"| {name} | {b_status} | {a_status} |")

    lines.append("")
    lines.append("## Runtime Needs and C Size")
    lines.append("")
    lines.append(
        f"- before total helper count: `{before_runtime.get('total_helper_count', 0)}`"
    )
    lines.append(
        f"- after total helper count: `{after_runtime.get('total_helper_count', 0)}`"
    )
    lines.append(
        f"- before total generated C bytes: `{before_runtime.get('total_generated_c_bytes', 0)}`"
    )
    lines.append(
        f"- after total generated C bytes: `{after_runtime.get('total_generated_c_bytes', 0)}`"
    )
    lines.append("")
    lines.append(
        "| program | helpers before | helpers after | C bytes before | C bytes after |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    b_rt_programs = before_runtime.get("programs", {})
    a_rt_programs = after_runtime.get("programs", {})
    for name in sorted(set(b_rt_programs) | set(a_rt_programs)):
        b_rec = b_rt_programs.get(name, {}) if isinstance(b_rt_programs, dict) else {}
        a_rec = a_rt_programs.get(name, {}) if isinstance(a_rt_programs, dict) else {}
        b_helpers = int(b_rec.get("helper_count", 0) or 0)
        a_helpers = int(a_rec.get("helper_count", 0) or 0)
        b_c_bytes = int(b_rec.get("generated_c_bytes", 0) or 0)
        a_c_bytes = int(a_rec.get("generated_c_bytes", 0) or 0)
        lines.append(
            f"| {name} | {b_helpers} | {a_helpers} | {b_c_bytes} | {a_c_bytes} |"
        )

    lines.append("")
    lines.append(
        "| family | programs before | programs after | helper hits before | helper hits after |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    b_family_programs = before_runtime.get("family_program_counts", {})
    a_family_programs = after_runtime.get("family_program_counts", {})
    b_family_helpers = before_runtime.get("family_helper_totals", {})
    a_family_helpers = after_runtime.get("family_helper_totals", {})
    for name in sorted(
        set(b_family_programs)
        | set(a_family_programs)
        | set(b_family_helpers)
        | set(a_family_helpers)
    ):
        lines.append(
            f"| {name} | "
            f"{int(b_family_programs.get(name, 0) or 0)} | "
            f"{int(a_family_programs.get(name, 0) or 0)} | "
            f"{int(b_family_helpers.get(name, 0) or 0)} | "
            f"{int(a_family_helpers.get(name, 0) or 0)} |"
        )

    lines.append("")
    lines.append("## Effect Policy")
    lines.append("")
    lines.append(
        f"- before total violations: `{before_effect.get('violation_count_total', 0)}`"
    )
    lines.append(
        f"- after total violations: `{after_effect.get('violation_count_total', 0)}`"
    )
    lines.append("")
    lines.append("| kind | before | after | delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    b_kind = before_effect.get("by_kind_totals", {})
    a_kind = after_effect.get("by_kind_totals", {})
    for key in sorted(set(b_kind) | set(a_kind)):
        b_val = int(b_kind.get(key, 0) or 0)
        a_val = int(a_kind.get(key, 0) or 0)
        lines.append(f"| {key} | {b_val} | {a_val} | {a_val - b_val:+d} |")

    lines.append("")
    lines.append("| effect | before | after | delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    b_eff = before_effect.get("by_effect_totals", {})
    a_eff = after_effect.get("by_effect_totals", {})
    for key in sorted(set(b_eff) | set(a_eff)):
        b_val = int(b_eff.get(key, 0) or 0)
        a_val = int(a_eff.get(key, 0) or 0)
        lines.append(f"| {key} | {b_val} | {a_val} | {a_val - b_val:+d} |")

    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append(f"- before snapshot: `{before_dir}`")
    lines.append(f"- after snapshot: `{after_dir}`")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"comparison report: {output}")
    return 0

__all__ = [name for name in globals() if not name.startswith("__")]

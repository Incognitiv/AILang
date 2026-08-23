from __future__ import annotations

try:
    from .session_benchmark_common import *
except ImportError:
    from session_benchmark_common import *

def _collect_check_report_snapshot(
    *,
    session_dir: Path,
    program_names: list[str],
) -> tuple[int, Path, dict[str, Any]]:
    """Capture aggregate --report-checks counters for the routine corpus."""
    report_json = session_dir / "check_report_snapshot.json"
    report_log = session_dir / "check_report.log"

    summary_totals: dict[str, int] = {}
    program_results: dict[str, Any] = {}
    logs: list[str] = []
    exit_code = 0

    for program_name in program_names:
        src = CHECK_REPORT_CORPUS / f"{program_name}.ail"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(src),
            "--report-checks-json",
        ]
        rc, out, err = _run(cmd, timeout=240)
        logs.append(f"$ {' '.join(cmd)}")
        if rc != 0:
            exit_code = 1
            problem = (err.strip() or out.strip() or f"exit={rc}")[:400]
            logs.append(f"[error] {program_name}: {problem}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": rc,
                "error": problem,
            }
            continue

        try:
            payload = json.loads(out)
        except ValueError:
            exit_code = 1
            snippet = out.strip().splitlines()
            preview = snippet[0] if snippet else "<empty stdout>"
            logs.append(f"[error] {program_name}: invalid JSON: {preview[:240]}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": 1,
                "error": "invalid JSON from --report-checks-json",
                "stdout_preview": preview[:240],
            }
            continue

        raw_summary = payload.get("summary", {})
        summary: dict[str, int] = {}
        if isinstance(raw_summary, dict):
            for key, value in raw_summary.items():
                try:
                    ivalue = int(value)
                except (TypeError, ValueError):
                    continue
                summary[str(key)] = ivalue
                summary_totals[str(key)] = int(summary_totals.get(str(key), 0)) + ivalue
        decision_count = int(payload.get("decision_count", 0) or 0)
        logs.append(
            f"[ok] {program_name}: decisions={decision_count}, summary_keys={len(summary)}"
        )
        program_results[program_name] = {
            "status": "ok",
            "exit_code": 0,
            "decision_count": decision_count,
            "summary": summary,
        }

    total_decisions = 0
    for rec in program_results.values():
        if isinstance(rec, dict):
            total_decisions += int(rec.get("decision_count", 0) or 0)

    snapshot = {
        "timestamp_iso": time.strftime(DATE_ISO_FMT),
        "timestamp_human": time.strftime(DATE_HUMAN_FMT),
        "program_names": list(program_names),
        "summary_totals": dict(sorted(summary_totals.items())),
        "decision_count_total": total_decisions,
        "programs": program_results,
    }
    _save_json(report_json, snapshot)
    report_log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    return exit_code, report_json, snapshot
def _collect_format_report_snapshot(
    *,
    session_dir: Path,
    program_names: list[str],
) -> tuple[int, Path, dict[str, Any]]:
    """Capture aggregate --format-report counters for the routine corpus."""
    report_json = session_dir / "format_report_snapshot.json"
    report_log = session_dir / "format_report.log"

    summary_totals: dict[str, int] = {}
    program_results: dict[str, Any] = {}
    logs: list[str] = []
    exit_code = 0

    for program_name in program_names:
        src = CHECK_REPORT_CORPUS / f"{program_name}.ail"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(src),
            "--format-report-json",
        ]
        rc, out, err = _run(cmd, timeout=240)
        logs.append(f"$ {' '.join(cmd)}")
        if rc != 0:
            exit_code = 1
            problem = (err.strip() or out.strip() or f"exit={rc}")[:400]
            logs.append(f"[error] {program_name}: {problem}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": rc,
                "error": problem,
            }
            continue

        try:
            payload = json.loads(out)
        except ValueError:
            exit_code = 1
            snippet = out.strip().splitlines()
            preview = snippet[0] if snippet else "<empty stdout>"
            logs.append(f"[error] {program_name}: invalid JSON: {preview[:240]}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": 1,
                "error": "invalid JSON from --format-report-json",
                "stdout_preview": preview[:240],
            }
            continue

        raw_summary = payload.get("summary", {})
        summary: dict[str, int] = {}
        if isinstance(raw_summary, dict):
            for key, value in raw_summary.items():
                try:
                    ivalue = int(value)
                except (TypeError, ValueError):
                    continue
                summary[str(key)] = ivalue
                summary_totals[str(key)] = int(summary_totals.get(str(key), 0)) + ivalue
        decision_count = int(payload.get("decision_count", 0) or 0)
        logs.append(
            f"[ok] {program_name}: decisions={decision_count}, summary_keys={len(summary)}"
        )
        program_results[program_name] = {
            "status": "ok",
            "exit_code": 0,
            "decision_count": decision_count,
            "summary": summary,
        }

    total_decisions = 0
    for rec in program_results.values():
        if isinstance(rec, dict):
            total_decisions += int(rec.get("decision_count", 0) or 0)

    snapshot = {
        "timestamp_iso": time.strftime(DATE_ISO_FMT),
        "timestamp_human": time.strftime(DATE_HUMAN_FMT),
        "program_names": list(program_names),
        "summary_totals": dict(sorted(summary_totals.items())),
        "decision_count_total": total_decisions,
        "programs": program_results,
    }
    _save_json(report_json, snapshot)
    report_log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    return exit_code, report_json, snapshot
def _collect_runtime_needs_snapshot(
    *,
    session_dir: Path,
    program_names: list[str],
) -> tuple[int, Path, dict[str, Any]]:
    """Capture aggregate --runtime-needs counters for the routine corpus."""
    report_json = session_dir / "runtime_needs_snapshot.json"
    report_log = session_dir / "runtime_needs.log"

    family_program_counts: dict[str, int] = {}
    family_helper_totals: dict[str, int] = {}
    program_results: dict[str, Any] = {}
    logs: list[str] = []
    exit_code = 0

    for program_name in program_names:
        src = CHECK_REPORT_CORPUS / f"{program_name}.ail"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(src),
            "--runtime-needs-json",
        ]
        rc, out, err = _run(cmd, timeout=240)
        logs.append(f"$ {' '.join(cmd)}")
        if rc != 0:
            exit_code = 1
            problem = (err.strip() or out.strip() or f"exit={rc}")[:400]
            logs.append(f"[error] {program_name}: {problem}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": rc,
                "error": problem,
            }
            continue
        try:
            payload = json.loads(out)
        except ValueError:
            exit_code = 1
            snippet = out.strip().splitlines()
            preview = snippet[0] if snippet else "<empty stdout>"
            logs.append(f"[error] {program_name}: invalid JSON: {preview[:240]}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": 1,
                "error": "invalid JSON from --runtime-needs-json",
                "stdout_preview": preview[:240],
            }
            continue

        helpers = int(payload.get("helper_count", 0) or 0)
        c_bytes = int(payload.get("generated_c_bytes", 0) or 0)
        families = payload.get("families", {})
        family_counts = payload.get("family_helper_counts", {})
        if isinstance(families, dict):
            for key, value in families.items():
                if bool(value):
                    family_program_counts[str(key)] = (
                        int(family_program_counts.get(str(key), 0)) + 1
                    )
        if isinstance(family_counts, dict):
            for key, value in family_counts.items():
                try:
                    ivalue = int(value)
                except (TypeError, ValueError):
                    continue
                family_helper_totals[str(key)] = (
                    int(family_helper_totals.get(str(key), 0)) + ivalue
                )
        logs.append(
            f"[ok] {program_name}: helper_count={helpers}, generated_c_bytes={c_bytes}"
        )
        program_results[program_name] = {
            "status": "ok",
            "exit_code": 0,
            "helper_count": helpers,
            "generated_c_bytes": c_bytes,
            "families": families if isinstance(families, dict) else {},
            "family_helper_counts": (
                family_counts if isinstance(family_counts, dict) else {}
            ),
        }

    total_helper_count = 0
    total_generated_c_bytes = 0
    for rec in program_results.values():
        if isinstance(rec, dict):
            total_helper_count += int(rec.get("helper_count", 0) or 0)
            total_generated_c_bytes += int(rec.get("generated_c_bytes", 0) or 0)

    snapshot = {
        "timestamp_iso": time.strftime(DATE_ISO_FMT),
        "timestamp_human": time.strftime(DATE_HUMAN_FMT),
        "program_names": list(program_names),
        "total_helper_count": total_helper_count,
        "total_generated_c_bytes": total_generated_c_bytes,
        "family_program_counts": dict(sorted(family_program_counts.items())),
        "family_helper_totals": dict(sorted(family_helper_totals.items())),
        "programs": program_results,
    }
    _save_json(report_json, snapshot)
    report_log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    return exit_code, report_json, snapshot
def _collect_effect_policy_snapshot(
    *,
    session_dir: Path,
    program_names: list[str],
) -> tuple[int, Path, dict[str, Any]]:
    """Capture aggregate --effect-policy diagnostics for the routine corpus."""
    report_json = session_dir / "effect_policy_snapshot.json"
    report_log = session_dir / "effect_policy.log"

    by_kind_totals: dict[str, int] = {}
    by_effect_totals: dict[str, int] = {}
    program_results: dict[str, Any] = {}
    logs: list[str] = []
    exit_code = 0

    for program_name in program_names:
        src = CHECK_REPORT_CORPUS / f"{program_name}.ail"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "ailang.py"),
            str(src),
            "--effect-policy-json",
        ]
        rc, out, err = _run(cmd, timeout=240)
        logs.append(f"$ {' '.join(cmd)}")
        if rc != 0:
            exit_code = 1
            problem = (err.strip() or out.strip() or f"exit={rc}")[:400]
            logs.append(f"[error] {program_name}: {problem}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": rc,
                "error": problem,
            }
            continue
        try:
            payload = json.loads(out)
        except ValueError:
            exit_code = 1
            snippet = out.strip().splitlines()
            preview = snippet[0] if snippet else "<empty stdout>"
            logs.append(f"[error] {program_name}: invalid JSON: {preview[:240]}")
            program_results[program_name] = {
                "status": "error",
                "exit_code": 1,
                "error": "invalid JSON from --effect-policy-json",
                "stdout_preview": preview[:240],
            }
            continue

        violation_count = int(payload.get("violation_count", 0) or 0)
        by_kind = payload.get("by_kind", {})
        by_effect = payload.get("by_effect", {})
        if isinstance(by_kind, dict):
            for key, value in by_kind.items():
                try:
                    ivalue = int(value)
                except (TypeError, ValueError):
                    continue
                by_kind_totals[str(key)] = int(by_kind_totals.get(str(key), 0)) + ivalue
        if isinstance(by_effect, dict):
            for key, value in by_effect.items():
                try:
                    ivalue = int(value)
                except (TypeError, ValueError):
                    continue
                by_effect_totals[str(key)] = (
                    int(by_effect_totals.get(str(key), 0)) + ivalue
                )
        logs.append(f"[ok] {program_name}: violations={violation_count}")
        program_results[program_name] = {
            "status": "ok",
            "exit_code": 0,
            "violation_count": violation_count,
            "by_kind": by_kind if isinstance(by_kind, dict) else {},
            "by_effect": by_effect if isinstance(by_effect, dict) else {},
        }

    total_violations = 0
    for rec in program_results.values():
        if isinstance(rec, dict):
            total_violations += int(rec.get("violation_count", 0) or 0)

    snapshot = {
        "timestamp_iso": time.strftime(DATE_ISO_FMT),
        "timestamp_human": time.strftime(DATE_HUMAN_FMT),
        "program_names": list(program_names),
        "violation_count_total": total_violations,
        "by_kind_totals": dict(sorted(by_kind_totals.items())),
        "by_effect_totals": dict(sorted(by_effect_totals.items())),
        "programs": program_results,
    }
    _save_json(report_json, snapshot)
    report_log.write_text("\n".join(logs) + "\n", encoding="utf-8")
    return exit_code, report_json, snapshot

__all__ = [name for name in globals() if not name.startswith("__")]

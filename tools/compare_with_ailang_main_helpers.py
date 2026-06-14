#!/usr/bin/env python3
"""Compare current AILang repo against AILang-main (prototype) with gates.

Outputs:
  - JSON summary
  - Markdown report

Optional gates:
  - performance regression thresholds
  - C-backend leak probe thresholds
  - backend differential fuzzing (LLVM/JIT vs C backend per version)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD_PROTOTYPE_ROOT = REPO_ROOT.parent / "AILang-main" / "prototype"
DEFAULT_NEW_ENTRY = REPO_ROOT / "ailang.py"
DEFAULT_OUT_JSON = (
    REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.json"
)
DEFAULT_OUT_MD = REPO_ROOT / "benchmarks" / "results" / "compare_with_ailang_main.md"
LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)
INT_TOKEN_RE = re.compile(r"[-+]?\d+")
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def _run(
    cmd: list[str],
    cwd: Path,
    timeout: int = 900,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str, float]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return proc.returncode, proc.stdout, proc.stderr, elapsed_ms
def _decode_json_bytes(raw: bytes) -> dict[str, Any]:
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "cp1252"):
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            pass
    raise ValueError("Unable to decode JSON output")
def _normalize_verifier_payload(
    payload: dict[str, Any], exit_code: int
) -> dict[str, Any]:
    total = payload.get("total")
    passed = payload.get("passed")
    failed = payload.get("failed")
    # Single-file verifier payload can be shape {"passed": bool, ...}
    if isinstance(passed, bool):
        total = 1
        failed = 0 if passed else 1
        passed = 1 if passed else 0
    return {
        "exit_code": exit_code,
        "total": total,
        "passed": passed,
        "failed": failed,
    }
def _run_verifier_summary(cmd: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    payload = _decode_json_bytes(proc.stdout)
    return _normalize_verifier_payload(payload, proc.returncode)
def _count_py_files(root: Path) -> int:
    return sum(1 for _ in root.rglob("*.py"))
def _check_programs(entry: Path, files: list[Path]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for f in files:
        rc, out, err, elapsed = _run(
            [sys.executable, str(entry), str(f), "--check"], cwd=entry.parent
        )
        results[f.name] = {
            "ok": rc == 0,
            "exit_code": rc,
            "elapsed_ms": round(elapsed, 2),
            "stderr_head": "\n".join(err.strip().splitlines()[:3]),
            "stdout_head": "\n".join(out.strip().splitlines()[:3]),
        }
    return results
def _parse_leak_blob(text: str) -> tuple[int, int, int] | None:
    m = LEAK_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
def _run_c_backend_leak_probe(
    entry: Path, files: list[Path], label: str
) -> dict[str, Any]:
    out_dir = REPO_ROOT / "out" / "compare_leaks" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}
    for f in files:
        exe = out_dir / f"{f.stem}.exe"
        compile_cmd = [
            sys.executable,
            str(entry),
            str(f),
            "--backend=c",
            "-o",
            str(exe),
        ]
        c_rc, _c_out, c_err, c_ms = _run(compile_cmd, cwd=entry.parent, timeout=600)
        rec: dict[str, Any] = {
            "compile_ok": c_rc == 0 and exe.exists(),
            "compile_exit": c_rc,
            "compile_ms": round(c_ms, 2),
            "run_ok": False,
            "run_exit": None,
            "leak_alloc_bytes": None,
            "leak_freed_bytes": None,
            "leak_live_bytes": None,
            "stderr_head": "\n".join(c_err.strip().splitlines()[:4]),
        }
        if not rec["compile_ok"]:
            result[f.name] = rec
            continue
        r_rc, r_out, r_err, _ = _run(
            [str(exe)],
            cwd=entry.parent,
            timeout=180,
            env_extra={"AILANG_LEAK_REPORT": "1"},
        )
        blob = (r_out or "") + "\n" + (r_err or "")
        leak = _parse_leak_blob(blob)
        rec["run_ok"] = r_rc == 0
        rec["run_exit"] = r_rc
        if leak is not None:
            rec["leak_alloc_bytes"], rec["leak_freed_bytes"], rec["leak_live_bytes"] = (
                leak
            )
        rec["runtime_stderr_head"] = "\n".join(r_err.strip().splitlines()[:4])
        result[f.name] = rec
    return result
def _flag_support(entry: Path, sample: Path) -> dict[str, bool]:
    base_cmd = [sys.executable, str(entry), str(sample)]
    rc, out, _err, _ = _run(
        base_cmd + ["--jit-repeat", "2", "--jit-warmup", "1", "--jit-json"],
        cwd=entry.parent,
    )
    txt = (out or "").strip()
    return {"jit_json": rc == 0 and txt.startswith("{")}
def _measurement_dict(measure: Any) -> dict[str, Any]:
    runs = measure.runs_ms if measure.runs_ms is not None else []
    med = statistics.median(runs) if runs else None
    mean = statistics.mean(runs) if runs else None
    return {
        "status": measure.status,
        "compile_ms": measure.compile_ms,
        "runtime_median_ms": med,
        "runtime_mean_ms": mean,
        "checksum": measure.checksum,
        "leak_alloc_bytes": measure.leak_alloc_bytes,
        "leak_freed_bytes": measure.leak_freed_bytes,
        "leak_live_bytes": measure.leak_live_bytes,
        "leak_check_status": measure.leak_check_status,
        "peak_rss_bytes": measure.peak_rss_bytes,
        "note": measure.note,
    }
def _load_run_benchmarks_module() -> Any:
    path = REPO_ROOT / "benchmarks" / "run_benchmarks.py"
    module_name = "compare_run_benchmarks"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load benchmark module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod
def _run_ailang_benchmarks(
    entry: Path,
    label: str,
    runs: int,
    warmup: int,
    selected_cases: set[str],
    selected_impls: list[str],
    check_leaks: bool,
    leak_threshold: int,
) -> dict[str, Any]:
    rb = _load_run_benchmarks_module()
    old_entry = rb.AILEXEC
    old_out = rb.OUT_DIR
    try:
        rb.AILEXEC = entry
        rb.OUT_DIR = REPO_ROOT / "benchmarks" / "out" / f"compare_{label}"
        rb.OUT_DIR.mkdir(parents=True, exist_ok=True)
        all_cases = rb.define_cases()
        cases = (
            [c for c in all_cases if c.name in selected_cases]
            if selected_cases
            else all_cases
        )
        results = rb.run_benchmarks(
            cases=cases,
            run_count=runs,
            warmup_count=warmup,
            implementations=selected_impls,
            check_output=True,
            check_leaks=check_leaks,
            leak_threshold=leak_threshold,
            sample_memory=True,
        )
        serial: dict[str, Any] = {}
        for case in cases:
            case_data = results.get(case.name, {})
            serial[case.name] = {
                impl: _measurement_dict(meas) for impl, meas in case_data.items()
            }
        return serial
    finally:
        rb.AILEXEC = old_entry
        rb.OUT_DIR = old_out
def _check_ok_count(checks: dict[str, Any]) -> tuple[int, int]:
    vals = list(checks.values())
    return sum(1 for v in vals if v.get("ok")), len(vals)
def _summarize_perf_rows(
    bench_current: dict[str, Any], bench_old: dict[str, Any]
) -> list[str]:
    rows = [
        "| case | impl | current status | old status | current median ms | old median ms | delta old-current ms |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for case in sorted(set(bench_current) | set(bench_old)):
        for impl in sorted(
            set(bench_current.get(case, {})) | set(bench_old.get(case, {}))
        ):
            n = bench_current.get(case, {}).get(impl, {})
            o = bench_old.get(case, {}).get(impl, {})
            n_med = n.get("runtime_median_ms")
            o_med = o.get("runtime_median_ms")
            if isinstance(n_med, (int, float)) and isinstance(o_med, (int, float)):
                delta_txt = f"{(o_med - n_med):+.2f}"
            else:
                delta_txt = "n/a"
            rows.append(
                "| "
                + " | ".join(
                    [
                        case,
                        impl,
                        str(n.get("status")),
                        str(o.get("status")),
                        f"{n_med:.2f}" if isinstance(n_med, (int, float)) else "n/a",
                        f"{o_med:.2f}" if isinstance(o_med, (int, float)) else "n/a",
                        delta_txt,
                    ]
                )
                + " |"
            )
    return rows
def _perf_regressions(
    bench_current: dict[str, Any],
    bench_old: dict[str, Any],
    ratio_threshold: float,
    abs_ms_threshold: float,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for case, cur_case in bench_current.items():
        old_case = bench_old.get(case, {})
        for impl, cur in cur_case.items():
            old = old_case.get(impl, {})
            if cur.get("status") != "ok" or old.get("status") != "ok":
                continue
            c_med = cur.get("runtime_median_ms")
            o_med = old.get("runtime_median_ms")
            if not isinstance(c_med, (int, float)) or not isinstance(
                o_med, (int, float)
            ):
                continue
            if o_med <= 0:
                continue
            ratio = c_med / o_med
            delta_ms = c_med - o_med
            if ratio >= ratio_threshold and delta_ms >= abs_ms_threshold:
                issues.append(
                    {
                        "case": case,
                        "impl": impl,
                        "current_median_ms": c_med,
                        "old_median_ms": o_med,
                        "delta_ms": delta_ms,
                        "ratio": ratio,
                    }
                )
    return issues
def _leak_over_budget(
    probe: dict[str, Any], leak_threshold: int
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for program, rec in probe.items():
        live = rec.get("leak_live_bytes")
        if isinstance(live, int) and live > leak_threshold:
            issues.append(
                {
                    "program": program,
                    "live_leak_bytes": live,
                    "threshold": leak_threshold,
                }
            )
    return issues
def _extract_last_int(text: str) -> int | None:
    tokens = INT_TOKEN_RE.findall(text or "")
    if not tokens:
        return None
    try:
        return int(tokens[-1])
    except ValueError:
        return None
def _generate_fuzz_program(idx: int, rng: random.Random) -> str:
    n = rng.randint(10_000, 50_000)
    a = rng.randint(2, 11)
    b = rng.randint(3, 13)
    c = rng.randint(1, 7)
    return (
        f"def main(): int\n"
        f"    acc = 0\n"
        f"    for (i = 0; i < {n}; i = i + 1) then\n"
        f"        if i < {n // 2} then\n"
        f"            acc = acc + (i * {a}) + {c}\n"
        f"        else\n"
        f"            acc = acc + (i * {b}) - {c}\n"
        f"        end\n"
        f"    end\n"
        f"    print(acc)\n"
        f"    return 0\n"
        f"end\n"
    )
def _run_program_output(
    entry: Path, program: Path, backend: str, out_dir: Path
) -> dict[str, Any]:
    if backend == "llvm":
        rc, out, err, elapsed = _run(
            [sys.executable, str(entry), str(program)], cwd=entry.parent
        )
        return {
            "ok": rc == 0,
            "exit_code": rc,
            "elapsed_ms": round(elapsed, 2),
            "out": out,
            "err": err,
            "value": _extract_last_int(out + "\n" + err),
        }
    exe = out_dir / f"{program.stem}_{entry.parent.name}.exe"
    c_rc, c_out, c_err, c_ms = _run(
        [sys.executable, str(entry), str(program), "--backend=c", "-o", str(exe)],
        cwd=entry.parent,
        timeout=600,
    )
    if c_rc != 0 or not exe.exists():
        return {
            "ok": False,
            "exit_code": c_rc,
            "elapsed_ms": round(c_ms, 2),
            "out": c_out,
            "err": c_err,
            "value": None,
        }
    r_rc, r_out, r_err, r_ms = _run([str(exe)], cwd=entry.parent, timeout=180)
    return {
        "ok": r_rc == 0,
        "exit_code": r_rc,
        "elapsed_ms": round(r_ms, 2),
        "out": r_out,
        "err": r_err,
        "value": _extract_last_int(r_out + "\n" + r_err),
    }
def _run_backend_fuzz_diff(
    entry: Path,
    label: str,
    fuzz_cases: int,
    fuzz_seed: int,
) -> dict[str, Any]:
    out_dir = REPO_ROOT / "out" / "compare_fuzz" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(fuzz_seed)
    mismatches: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for idx in range(fuzz_cases):
        program_path = out_dir / f"fuzz_{idx:03d}.ail"
        program_path.write_text(_generate_fuzz_program(idx, rng), encoding="utf-8")
        llvm = _run_program_output(entry, program_path, "llvm", out_dir)
        c = _run_program_output(entry, program_path, "c", out_dir)
        same = llvm.get("ok") and c.get("ok") and llvm.get("value") == c.get("value")
        rec = {
            "program": str(program_path),
            "llvm_ok": llvm.get("ok"),
            "c_ok": c.get("ok"),
            "llvm_value": llvm.get("value"),
            "c_value": c.get("value"),
            "match": bool(same),
        }
        records.append(rec)
        if not same:
            mismatches.append(rec)
    return {
        "cases": fuzz_cases,
        "seed": fuzz_seed,
        "mismatches": mismatches,
        "records": records,
        "pass": len(mismatches) == 0,
    }

__all__ = [name for name in globals() if not name.startswith("__")]

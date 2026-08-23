#!/usr/bin/env python3
"""Profile AILang language-surface usage, runtime cost, and leak signals."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
DEFAULT_OUT_JSON = (
    REPO_ROOT / "benchmarks" / "results" / "language_surface_profile.json"
)
DEFAULT_OUT_MD = REPO_ROOT / "benchmarks" / "results" / "language_surface_profile.md"
DEFAULT_BENCH_DIR = REPO_ROOT / "benchmarks" / "ailang"
DEFAULT_CORPUS_DIR = REPO_ROOT / "tests" / "corpus"

LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)

# Token types that are not language keywords (technical token classes).
NON_KEYWORD_TOKEN_TYPES = {
    "COMMENT_BLOCK",
    "COMMENT",
    "TEMPLATE_START",
    "TEMPLATE_END",
    "CINCLUDE",
    "LINK_DIR",
    "HASH_COMMENT",
    "FLOAT",
    "NUMBER",
    "STRING",
    "INLINE_ASM",
    "IDENT",
    "NEWLINE",
    "SKIP",
    "MISMATCH",
}


@dataclass
class BackendRun:
    compile_ok: bool
    compile_ms: float
    runtime_ok: bool
    runtime_ms: float
    exit_code: int
    stdout_first_line: str
    stderr_head: str
    leak_alloc_bytes: int | None = None
    leak_freed_bytes: int | None = None
    leak_live_bytes: int | None = None


@dataclass
class ProgramProfile:
    path: str
    keywords: dict[str, int]
    builtin_calls: dict[str, int]
    llvm: BackendRun | None = None
    c: BackendRun | None = None


def _run(
    cmd: list[str],
    cwd: Path = REPO_ROOT,
    timeout: int = 900,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, float]:
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return proc.returncode, proc.stdout, proc.stderr, elapsed_ms


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _stderr_head(text: str, limit: int = 4) -> str:
    return "\n".join(text.strip().splitlines()[:limit])


def _parse_leak_blob(blob: str) -> tuple[int, int, int] | None:
    m = LEAK_RE.search(blob)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _compile_and_run(source: Path, backend: str, out_dir: Path) -> BackendRun:
    exe = out_dir / f"{source.stem}_{backend}.exe"
    cmd = [sys.executable, str(REPO_ROOT / "ailang.py"), str(source)]
    if backend == "c":
        cmd.append("--backend=c")
    cmd.extend(["-o", str(exe)])

    cc, cc_out, cc_err, cc_ms = _run(cmd, timeout=1200)
    compile_ok = cc == 0 and exe.exists()
    if not compile_ok:
        return BackendRun(
            compile_ok=False,
            compile_ms=round(cc_ms, 3),
            runtime_ok=False,
            runtime_ms=0.0,
            exit_code=cc,
            stdout_first_line=_first_nonempty_line(cc_out),
            stderr_head=_stderr_head(cc_err),
        )

    run_env: dict[str, str] | None = None
    if backend == "c":
        run_env = os.environ.copy()
        run_env["AILANG_LEAK_REPORT"] = "1"

    rc, run_out, run_err, run_ms = _run(
        [str(exe)], timeout=300, env=run_env if run_env is not None else None
    )
    leak_alloc = leak_freed = leak_live = None
    if backend == "c":
        leak = _parse_leak_blob((run_out or "") + "\n" + (run_err or ""))
        if leak is not None:
            leak_alloc, leak_freed, leak_live = leak

    return BackendRun(
        compile_ok=True,
        compile_ms=round(cc_ms, 3),
        runtime_ok=rc == 0,
        runtime_ms=round(run_ms, 3),
        exit_code=rc,
        stdout_first_line=_first_nonempty_line(run_out),
        stderr_head=_stderr_head(run_err),
        leak_alloc_bytes=leak_alloc,
        leak_freed_bytes=leak_freed,
        leak_live_bytes=leak_live,
    )


def _compile_and_run_safe(source: Path, backend: str, out_dir: Path) -> BackendRun:
    try:
        return _compile_and_run(source, backend, out_dir)
    except subprocess.TimeoutExpired:
        return BackendRun(
            compile_ok=False,
            compile_ms=0.0,
            runtime_ok=False,
            runtime_ms=0.0,
            exit_code=124,
            stdout_first_line="",
            stderr_head="timeout",
        )
    except OSError as exc:
        return BackendRun(
            compile_ok=False,
            compile_ms=0.0,
            runtime_ok=False,
            runtime_ms=0.0,
            exit_code=127,
            stdout_first_line="",
            stderr_head=str(exc),
        )


def _load_token_inventory() -> tuple[set[str], set[str]]:
    source_root = str(REPO_ROOT / "source")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from diagnostics.diagnostics_catalog import CALLABLE_BUILTINS
    from lexer.scan import CONTEXTUAL_KEYWORDS, TOKEN_PATTERNS

    keyword_types: set[str] = set()
    for token_type, pattern in TOKEN_PATTERNS:
        if token_type in NON_KEYWORD_TOKEN_TYPES:
            continue
        if pattern.startswith(r"\b") or pattern.startswith("@"):
            keyword_types.add(token_type)
    keyword_types |= set(CONTEXTUAL_KEYWORDS)
    builtin_names = {name.lower() for name in CALLABLE_BUILTINS}
    return keyword_types, builtin_names


def _scan_program_features(
    source: Path, keyword_types: set[str], builtin_names: set[str]
) -> tuple[dict[str, int], dict[str, int]]:
    source_root = str(REPO_ROOT / "source")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from lexer.scan import tokenize

    text = source.read_text(encoding="utf-8")
    tokens = tokenize(text)
    keyword_counts: dict[str, int] = {}
    builtin_calls: dict[str, int] = {}

    for idx, token in enumerate(tokens):
        token_type, token_val, *_rest = token
        if token_type in keyword_types:
            keyword_counts[token_type] = keyword_counts.get(token_type, 0) + 1

        name = str(token_val).lower()
        if name not in builtin_names:
            continue
        j = idx + 1
        while j < len(tokens):
            next_type, *_next_rest = tokens[j]
            if next_type not in {"NEWLINE"}:
                break
            j += 1
        if j < len(tokens):
            next_type, *_next_rest = tokens[j]
            if next_type != "LPAREN":
                continue
            builtin_calls[name] = builtin_calls.get(name, 0) + 1
    return keyword_counts, builtin_calls


def _collect_sources(explicit: list[str]) -> list[Path]:
    if explicit:
        selected = [Path(p).resolve() for p in explicit]
        return sorted(selected)
    discovered = sorted(DEFAULT_BENCH_DIR.glob("*.ail")) + sorted(
        DEFAULT_CORPUS_DIR.glob("*.ail")
    )
    unique: dict[str, Path] = {}
    for p in discovered:
        unique[str(p.resolve())] = p.resolve()
    return sorted(unique.values())


def _summarize_feature_costs(
    programs: list[ProgramProfile], feature_kind: str
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for prog in programs:
        features = prog.keywords if feature_kind == "keyword" else prog.builtin_calls
        for name, count in features.items():
            row = rows.setdefault(
                name,
                {
                    "feature": name,
                    "kind": feature_kind,
                    "uses": 0,
                    "programs": 0,
                    "c_compile_samples": [],
                    "c_runtime_samples": [],
                    "max_c_leak_live_bytes": 0,
                },
            )
            row["uses"] = int(row["uses"]) + int(count)
            row["programs"] = int(row["programs"]) + 1
            if prog.c is not None and prog.c.compile_ok:
                row["c_compile_samples"].append(prog.c.compile_ms)
            if prog.c is not None and prog.c.runtime_ok:
                row["c_runtime_samples"].append(prog.c.runtime_ms)
            if prog.c is not None and isinstance(prog.c.leak_live_bytes, int):
                row["max_c_leak_live_bytes"] = max(
                    int(row["max_c_leak_live_bytes"]), int(prog.c.leak_live_bytes)
                )

    out: list[dict[str, Any]] = []
    for row in rows.values():
        compile_samples = row.pop("c_compile_samples")
        runtime_samples = row.pop("c_runtime_samples")
        row["c_compile_mean_ms"] = (
            round(float(statistics.mean(compile_samples)), 4)
            if compile_samples
            else None
        )
        row["c_runtime_mean_ms"] = (
            round(float(statistics.mean(runtime_samples)), 4)
            if runtime_samples
            else None
        )
        out.append(row)

    out.sort(
        key=lambda r: (
            (
                float(r["c_runtime_mean_ms"])
                if r["c_runtime_mean_ms"] is not None
                else -1.0
            ),
            int(r["uses"]),
        ),
        reverse=True,
    )
    return out


def _wsl_path(path: Path) -> str:
    raw = str(path.resolve())
    m = re.match(r"^([A-Za-z]):\\(.*)$", raw)
    if m is None:
        return raw.replace("\\", "/")
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def _wsl_available() -> bool:
    rc, out, _err, _ms = _run(
        [
            "wsl",
            "bash",
            "-lc",
            "command -v perf >/dev/null && command -v python3 >/dev/null && echo ok",
        ],
        timeout=20,
    )
    return rc == 0 and "ok" in out


def _parse_perf_stat(stderr_text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in stderr_text.splitlines():
        parts = [p.strip() for p in line.strip().split(",")]
        if len(parts) < 3:
            continue
        value_txt = parts[0].replace(",", "").strip()
        metric_name = parts[2].strip()
        if not metric_name:
            continue
        metric_name = metric_name.removesuffix(":u")
        try:
            value = float(value_txt)
        except ValueError:
            continue
        metrics[metric_name] = value
    return metrics


def _run_wsl_perf_on_program(source: Path, timeout: int = 1800) -> dict[str, Any]:
    repo_wsl = _wsl_path(REPO_ROOT)
    src_wsl = _wsl_path(source)
    out_host_dir = REPO_ROOT / "out" / "language_surface_wsl"
    out_host_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    out_host = out_host_dir / f"{source.stem}_{stamp}.out"
    perf_host = out_host_dir / f"{source.stem}_{stamp}.perf.csv"
    compile_log_host = out_host_dir / f"{source.stem}_{stamp}.compile.log"

    out_wsl = _wsl_path(out_host)
    perf_out_wsl = _wsl_path(perf_host)
    compile_log_wsl = _wsl_path(compile_log_host)
    cmd_script = (
        f"set -e; cd {shlex.quote(repo_wsl)}; "
        f"python3 ailang.py {shlex.quote(src_wsl)} --backend=c -o {shlex.quote(out_wsl)} >{shlex.quote(compile_log_wsl)} 2>&1; "
        f"perf stat -x, -e task-clock,cycles,instructions,cache-misses,branches,branch-misses "
        f"{shlex.quote(out_wsl)} >/dev/null 2>{shlex.quote(perf_out_wsl)}; "
        f"cat {shlex.quote(perf_out_wsl)}"
    )
    rc, out, err, elapsed_ms = _run(["wsl", "bash", "-lc", cmd_script], timeout=timeout)
    metrics = _parse_perf_stat(out + "\n" + err)
    return {
        "program": str(source),
        "exit_code": rc,
        "elapsed_ms": round(elapsed_ms, 3),
        "metrics": metrics,
        "stderr_head": _stderr_head(err),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_md(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# AILang Language Surface Profile")
    lines.append("")
    lines.append(f"- Generated: {payload['timestamp_human']}")
    lines.append(f"- Sources scanned: `{payload['summary']['source_count']}`")
    lines.append(
        f"- Keyword inventory size: `{payload['summary']['keyword_inventory_size']}`"
    )
    lines.append(
        f"- Builtin inventory size: `{payload['summary']['builtin_inventory_size']}`"
    )
    lines.append(f"- Keyword coverage: `{payload['summary']['keyword_covered']}`")
    lines.append(f"- Builtin coverage: `{payload['summary']['builtin_covered']}`")
    lines.append(f"- Missing keywords: `{len(payload.get('missing_keywords', []))}`")
    lines.append(f"- Missing builtins: `{len(payload.get('missing_builtins', []))}`")
    lines.append("")

    lines.append("## Program Safety/Performance")
    lines.append("")
    lines.append(
        "| program | llvm compile/run | c compile/run | c leak live B | keyword kinds | builtin kinds |"
    )
    lines.append("| --- | --- | --- | ---: | ---: | ---: |")
    for p in payload["programs"]:
        llvm = p.get("llvm")
        c = p.get("c")
        llvm_state = (
            f"{llvm.get('compile_ok')}/{llvm.get('runtime_ok')}"
            if isinstance(llvm, dict)
            else "n/a"
        )
        c_state = (
            f"{c.get('compile_ok')}/{c.get('runtime_ok')}"
            if isinstance(c, dict)
            else "n/a"
        )
        leak_live = c.get("leak_live_bytes") if isinstance(c, dict) else None
        lines.append(
            f"| {Path(p['path']).name} | {llvm_state} | {c_state} | "
            f"{leak_live} | {len(p.get('keywords', {}))} | {len(p.get('builtin_calls', {}))} |"
        )
    lines.append("")

    lines.append("## Top Keyword Usage")
    lines.append("")
    lines.append("| keyword token | uses | programs |")
    lines.append("| --- | ---: | ---: |")
    for row in payload["keyword_usage"][:30]:
        lines.append(f"| {row['feature']} | {row['uses']} | {row['programs']} |")
    lines.append("")

    lines.append("## Top Builtin Usage")
    lines.append("")
    lines.append("| builtin | uses | programs |")
    lines.append("| --- | ---: | ---: |")
    for row in payload["builtin_usage"][:30]:
        lines.append(f"| {row['feature']} | {row['uses']} | {row['programs']} |")
    lines.append("")

    lines.append("## Cost Hotspots (C backend heuristic)")
    lines.append("")
    lines.append(
        "| feature | kind | uses | programs | c compile mean ms | c runtime mean ms | max leak live B |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for row in payload["cost_hotspots"][:40]:
        lines.append(
            f"| {row['feature']} | {row['kind']} | {row['uses']} | {row['programs']} | "
            f"{row.get('c_compile_mean_ms')} | {row.get('c_runtime_mean_ms')} | "
            f"{row['max_c_leak_live_bytes']} |"
        )
    lines.append("")

    wsl_perf = payload.get("wsl_perf", {})
    lines.append("## WSL perf")
    lines.append("")
    if not wsl_perf.get("enabled"):
        lines.append("- disabled")
    elif not wsl_perf.get("available"):
        lines.append("- requested but `wsl perf`/`python3` unavailable")
    else:
        lines.append(f"- profiled programs: `{len(wsl_perf.get('runs', []))}`")
        lines.append("")
        lines.append(
            "| program | exit | elapsed ms | task-clock | cycles | instructions | cache-misses |"
        )
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for run in wsl_perf.get("runs", []):
            metrics = run.get("metrics", {})
            lines.append(
                f"| {Path(run['program']).name} | {run['exit_code']} | {run['elapsed_ms']} | "
                f"{metrics.get('task-clock', 'n/a')} | {metrics.get('cycles', 'n/a')} | "
                f"{metrics.get('instructions', 'n/a')} | {metrics.get('cache-misses', 'n/a')} |"
            )

    lines.append("")
    lines.append("## Uncovered Inventory")
    lines.append("")
    lines.append("- Missing keyword tokens (first 40):")
    for token in payload.get("missing_keywords", [])[:40]:
        lines.append(f"  - `{token}`")
    lines.append("")
    lines.append("- Missing builtins (first 80):")
    for builtin in payload.get("missing_builtins", [])[:80]:
        lines.append(f"  - `{builtin}`")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Profile AILang keyword/builtin usage and safety."
    )
    p.add_argument(
        "--source",
        action="append",
        default=[],
        help="Explicit .ail source file to include (repeatable).",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUT_JSON,
        help="JSON output path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUT_MD,
        help="Markdown output path.",
    )
    p.add_argument(
        "--no-run-backends",
        action="store_true",
        help="Skip compile/run profiling and perform static coverage only.",
    )
    p.add_argument(
        "--wsl-perf",
        action="store_true",
        help="Profile top runtime programs with WSL perf stat.",
    )
    p.add_argument(
        "--wsl-perf-top",
        type=int,
        default=3,
        help="Number of slowest C-runtime programs to profile with WSL perf.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    sources = _collect_sources(args.source)
    if not sources:
        print("no source files selected")
        return 1
    for src in sources:
        if not src.exists():
            print(f"source not found: {src}")
            return 1

    keyword_inventory, builtin_inventory = _load_token_inventory()

    tmp_root = Path(tempfile.gettempdir()) / "ailang_language_surface"
    tmp_root.mkdir(parents=True, exist_ok=True)

    program_profiles: list[ProgramProfile] = []
    keyword_seen: set[str] = set()
    builtin_seen: set[str] = set()
    for src in sources:
        kw_counts, builtin_counts = _scan_program_features(
            src, keyword_inventory, builtin_inventory
        )
        keyword_seen |= set(kw_counts)
        builtin_seen |= set(builtin_counts)
        profile = ProgramProfile(
            path=str(src),
            keywords=kw_counts,
            builtin_calls=builtin_counts,
        )
        if not args.no_run_backends:
            profile.llvm = _compile_and_run_safe(src, "llvm", tmp_root)
            profile.c = _compile_and_run_safe(src, "c", tmp_root)
        program_profiles.append(profile)

    keyword_usage = _summarize_feature_costs(program_profiles, "keyword")
    builtin_usage = _summarize_feature_costs(program_profiles, "builtin")
    cost_hotspots = sorted(
        keyword_usage + builtin_usage,
        key=lambda r: (
            (
                float(r["c_runtime_mean_ms"])
                if r["c_runtime_mean_ms"] is not None
                else -1.0
            ),
            int(r["uses"]),
        ),
        reverse=True,
    )

    wsl_section: dict[str, Any] = {
        "enabled": bool(args.wsl_perf),
        "available": False,
        "runs": [],
    }
    if args.wsl_perf:
        wsl_section["available"] = _wsl_available()
        if wsl_section["available"]:
            c_runtime_ranked = [
                p
                for p in program_profiles
                if p.c is not None and p.c.compile_ok and p.c.runtime_ok
            ]
            c_runtime_ranked.sort(
                key=lambda p: float(p.c.runtime_ms if p.c else 0.0), reverse=True
            )
            chosen = c_runtime_ranked[: max(0, args.wsl_perf_top)]
            wsl_runs: list[dict[str, Any]] = []
            for p in chosen:
                wsl_runs.append(_run_wsl_perf_on_program(Path(p.path)))
            wsl_section["runs"] = wsl_runs

    missing_keywords = sorted(keyword_inventory - keyword_seen)
    missing_builtins = sorted(builtin_inventory - builtin_seen)

    payload: dict[str, Any] = {
        "timestamp_human": datetime.now().strftime(DATE_HUMAN_FMT),
        "timestamp_iso": datetime.now().strftime(DATE_ISO_FMT),
        "sources": [str(s) for s in sources],
        "summary": {
            "source_count": len(sources),
            "keyword_inventory_size": len(keyword_inventory),
            "builtin_inventory_size": len(builtin_inventory),
            "keyword_covered": len(keyword_seen),
            "builtin_covered": len(builtin_seen),
        },
        "missing_keywords": missing_keywords,
        "missing_builtins": missing_builtins,
        "programs": [
            {
                "path": p.path,
                "keywords": p.keywords,
                "builtin_calls": p.builtin_calls,
                "llvm": p.llvm.__dict__ if p.llvm is not None else None,
                "c": p.c.__dict__ if p.c is not None else None,
            }
            for p in program_profiles
        ],
        "keyword_usage": keyword_usage,
        "builtin_usage": builtin_usage,
        "cost_hotspots": cost_hotspots,
        "wsl_perf": wsl_section,
    }

    out_json = args.output_json.resolve()
    out_md = args.output_md.resolve()
    _write_json(out_json, payload)
    _write_md(out_md, payload)

    print(f"language surface json: {out_json}")
    print(f"language surface md: {out_md}")
    print(
        "coverage: "
        f"{payload['summary']['keyword_covered']}/{payload['summary']['keyword_inventory_size']} keywords, "
        f"{payload['summary']['builtin_covered']}/{payload['summary']['builtin_inventory_size']} builtins"
    )
    if args.wsl_perf:
        print(f"wsl perf available: {wsl_section['available']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

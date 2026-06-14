#!/usr/bin/env python3
"""Run ADAPT teardown leak audit and classify live bytes at exit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENTRY = REPO_ROOT / "ailang.py"
DEFAULT_ADAPT_ROOT = REPO_ROOT.parent / "ADAPT"
DEFAULT_CONTRACT = REPO_ROOT / "benchmarks" / "adapt_teardown_contract.json"
DEFAULT_JSON = REPO_ROOT / "benchmarks" / "results" / "adapt_teardown_audit.json"
DEFAULT_MD = REPO_ROOT / "benchmarks" / "results" / "adapt_teardown_audit.md"
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"
LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)


@dataclass
class ProgramResult:
    program: str
    compile_ok: bool
    compile_exit: int
    runtime_ok: bool
    runtime_exit: int
    leak_alloc_bytes: int | None
    leak_freed_bytes: int | None
    leak_live_bytes: int | None
    classification: str
    gate_ok: bool
    note: str
    stdout_tail: list[str]
    stderr_tail: list[str]


def _run(
    cmd: list[str], cwd: Path, *, timeout_s: int, env_extra: dict[str, str] | None = None
) -> tuple[int, str, str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout_s}s: {exc}"
    except OSError as exc:
        return 127, "", str(exc)
    return int(proc.returncode), proc.stdout or "", proc.stderr or ""


def _tail_lines(text: str, count: int = 6) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-count:]


def _parse_leaks(blob: str) -> tuple[int, int, int] | None:
    match = LEAK_RE.search(blob)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _default_programs(adapt_root: Path) -> list[str]:
    candidates = [
        "tests/test_sql_cursor.ail",
        "tests/test_persistence.ail",
        "tests/test_repl.ail",
    ]
    return [p for p in candidates if (adapt_root / p).exists()]


def _load_contract(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "default_max_live_bytes": 0, "programs": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"version": 1, "default_max_live_bytes": 0, "programs": {}}
    payload.setdefault("default_max_live_bytes", 0)
    payload.setdefault("programs", {})
    return payload


def _classify_result(
    rel_program: str,
    *,
    compile_ok: bool,
    runtime_ok: bool,
    leak_live: int | None,
    contract: dict[str, Any],
) -> tuple[str, bool, str]:
    if not compile_ok:
        return "harness_artifact", False, "compile_failed"
    if not runtime_ok:
        return "harness_artifact", False, "runtime_failed"
    if leak_live is None:
        return "harness_artifact", False, "missing_leak_report"

    prog_policy = contract.get("programs", {}).get(rel_program, {})
    expected_class = str(prog_policy.get("classification", "")).strip()
    max_live = prog_policy.get("max_live_bytes")
    if not isinstance(max_live, int):
        max_live = int(contract.get("default_max_live_bytes", 0) or 0)

    if leak_live <= 0:
        actual = "no_live"
    elif expected_class == "intentional_cache" and leak_live <= max_live:
        actual = "intentional_cache"
    elif expected_class == "harness_artifact":
        actual = "harness_artifact"
    else:
        actual = "true_leak"

    gate_ok = actual in {"no_live", "intentional_cache"}
    reason = f"live={leak_live}, expected={expected_class or 'none'}, max_live={max_live}"
    return actual, gate_ok, reason


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--adapt-root",
        type=Path,
        default=DEFAULT_ADAPT_ROOT,
        help="ADAPT repository root.",
    )
    p.add_argument(
        "--entry",
        type=Path,
        default=DEFAULT_ENTRY,
        help="AILang compiler entrypoint (current repo).",
    )
    p.add_argument(
        "--program",
        action="append",
        default=[],
        help="ADAPT .ail path relative to adapt-root (repeatable).",
    )
    p.add_argument(
        "--contract",
        type=Path,
        default=DEFAULT_CONTRACT,
        help="Leak classification contract JSON.",
    )
    p.add_argument(
        "--compile-timeout",
        type=int,
        default=900,
        help="Compile timeout per program (seconds).",
    )
    p.add_argument(
        "--run-timeout",
        type=int,
        default=300,
        help="Runtime timeout per program (seconds).",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_JSON,
        help="JSON report path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_MD,
        help="Markdown report path.",
    )
    p.add_argument(
        "--allow-missing-adapt-root",
        action="store_true",
        help="Exit 0 with skipped report when adapt-root does not exist.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    adapt_root = args.adapt_root.resolve()
    entry = args.entry.resolve()
    contract_path = args.contract.resolve()
    contract = _load_contract(contract_path)

    if not entry.exists():
        print(f"error: compiler entrypoint not found: {entry}")
        return 2
    if not adapt_root.exists():
        if args.allow_missing_adapt_root:
            payload = {
                "generated_human": time.strftime(DATE_HUMAN_FMT),
                "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "skipped",
                "reason": f"missing adapt root: {adapt_root}",
                "overall_ok": True,
                "results": [],
            }
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            args.output_md.parent.mkdir(parents=True, exist_ok=True)
            args.output_md.write_text(
                "# ADAPT Teardown Audit\n\n"
                f"- Date: {payload['generated_human']}\n"
                f"- Status: `skipped`\n"
                f"- Reason: `{payload['reason']}`\n",
                encoding="utf-8",
            )
            print("status: skipped")
            print(f"json: {args.output_json}")
            print(f"md: {args.output_md}")
            return 0
        print(f"error: adapt root not found: {adapt_root}")
        return 2

    rel_programs = args.program[:] if args.program else _default_programs(adapt_root)
    if not rel_programs:
        print("error: no ADAPT programs selected")
        return 2

    out_dir = REPO_ROOT / "out" / "adapt_teardown"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[ProgramResult] = []
    overall_ok = True

    for rel in rel_programs:
        src = adapt_root / rel
        if not src.exists():
            results.append(
                ProgramResult(
                    program=rel,
                    compile_ok=False,
                    compile_exit=2,
                    runtime_ok=False,
                    runtime_exit=2,
                    leak_alloc_bytes=None,
                    leak_freed_bytes=None,
                    leak_live_bytes=None,
                    classification="harness_artifact",
                    gate_ok=False,
                    note="missing_program",
                    stdout_tail=[],
                    stderr_tail=[f"missing: {src}"],
                )
            )
            overall_ok = False
            continue

        out_stem = out_dir / rel.replace("/", "_").replace("\\", "_").replace(".ail", "")
        compile_cmd = [sys.executable, str(entry), str(src), "--backend=c", "-o", str(out_stem)]
        c_rc, c_out, c_err = _run(
            compile_cmd, REPO_ROOT, timeout_s=max(30, int(args.compile_timeout))
        )
        compile_ok = c_rc == 0
        exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
        runtime_ok = False
        runtime_exit = -1
        leak_alloc = None
        leak_freed = None
        leak_live = None
        run_out = ""
        run_err = ""

        if compile_ok and exe.exists():
            runtime_cmd = [str(exe)]
            r_rc, run_out, run_err = _run(
                runtime_cmd,
                REPO_ROOT,
                timeout_s=max(10, int(args.run_timeout)),
                env_extra={"AILANG_LEAK_REPORT": "1"},
            )
            runtime_ok = r_rc == 0
            runtime_exit = r_rc
            leak = _parse_leaks((run_out or "") + "\n" + (run_err or ""))
            if leak is not None:
                leak_alloc, leak_freed, leak_live = leak

        classification, gate_ok, note = _classify_result(
            rel,
            compile_ok=compile_ok,
            runtime_ok=runtime_ok,
            leak_live=leak_live,
            contract=contract,
        )
        if not gate_ok:
            overall_ok = False

        results.append(
            ProgramResult(
                program=rel,
                compile_ok=compile_ok,
                compile_exit=c_rc,
                runtime_ok=runtime_ok,
                runtime_exit=runtime_exit,
                leak_alloc_bytes=leak_alloc,
                leak_freed_bytes=leak_freed,
                leak_live_bytes=leak_live,
                classification=classification,
                gate_ok=gate_ok,
                note=note,
                stdout_tail=_tail_lines(run_out or c_out),
                stderr_tail=_tail_lines(run_err or c_err),
            )
        )

    summary = {
        "no_live": sum(1 for r in results if r.classification == "no_live"),
        "intentional_cache": sum(
            1 for r in results if r.classification == "intentional_cache"
        ),
        "true_leak": sum(1 for r in results if r.classification == "true_leak"),
        "harness_artifact": sum(
            1 for r in results if r.classification == "harness_artifact"
        ),
    }

    payload = {
        "generated_human": time.strftime(DATE_HUMAN_FMT),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "adapt_root": str(adapt_root),
        "entry": str(entry),
        "contract": str(contract_path),
        "overall_ok": overall_ok,
        "summary": summary,
        "results": [r.__dict__ for r in results],
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# ADAPT Teardown Audit",
        "",
        f"- Date: {payload['generated_human']}",
        f"- ADAPT root: `{adapt_root}`",
        f"- Contract: `{contract_path}`",
        f"- Overall gate: `{'pass' if overall_ok else 'fail'}`",
        "",
        "## Summary",
        "",
        f"- no_live: `{summary['no_live']}`",
        f"- intentional_cache: `{summary['intentional_cache']}`",
        f"- true_leak: `{summary['true_leak']}`",
        f"- harness_artifact: `{summary['harness_artifact']}`",
        "",
        "## Results",
        "",
        "| program | compile | runtime | live bytes | classification | gate | note |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for r in results:
        lines.append(
            f"| `{r.program}` | `{r.compile_ok}/{r.compile_exit}` | "
            f"`{r.runtime_ok}/{r.runtime_exit}` | "
            f"`{r.leak_live_bytes}` | `{r.classification}` | "
            f"`{r.gate_ok}` | `{r.note}` |"
        )

    lines.extend(["", "## Tails", ""])
    for r in results:
        lines.append(f"### `{r.program}`")
        lines.append("")
        if r.stdout_tail:
            lines.append("stdout:")
            lines.append("```text")
            lines.extend(r.stdout_tail)
            lines.append("```")
        if r.stderr_tail:
            lines.append("stderr:")
            lines.append("```text")
            lines.extend(r.stderr_tail)
            lines.append("```")
        lines.append("")

    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"json: {args.output_json}")
    print(f"md: {args.output_md}")
    print("status: " + ("ok" if overall_ok else "fail"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

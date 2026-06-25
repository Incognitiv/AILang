#!/usr/bin/env python3
"""Bounded hard gate for AILang source, verifier, C23, and freestanding output."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "out" / "codex_audit"

GATE_STEP_IDS = [
    "source_strict",
    "verifier_strict",
    "c23_hosted_compile",
    "c23_freestanding_compile",
    "focused_pytest",
    "c23_workload_compare",
]


@dataclass(frozen=True)
class GateStep:
    step_id: str
    title: str
    command: list[str]
    timeout_seconds: int


@dataclass
class GateResult:
    step_id: str
    title: str
    command: list[str]
    returncode: int
    elapsed_seconds: float
    timed_out: bool
    stdout_tail: str
    stderr_tail: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _python(*args: str) -> list[str]:
    return [sys.executable, *args]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_step(step: GateStep) -> GateResult:
    print(f"\n== {step.step_id}: {step.title}")
    print(" ".join(step.command))
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            step.command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=step.timeout_seconds,
            check=False,
            shell=False,
            env=_env(),
        )
        elapsed = time.perf_counter() - start
        if proc.stdout:
            print(_tail(proc.stdout, 1200))
        if proc.stderr:
            print(_tail(proc.stderr, 1200), file=sys.stderr)
        return GateResult(
            step.step_id,
            step.title,
            step.command,
            proc.returncode,
            elapsed,
            False,
            _tail(proc.stdout),
            _tail(proc.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        print(f"timeout after {step.timeout_seconds}s", file=sys.stderr)
        return GateResult(
            step.step_id,
            step.title,
            step.command,
            124,
            elapsed,
            True,
            _tail(stdout),
            _tail(stderr),
        )


def _strict_steps(timeout: int) -> list[GateStep]:
    return [
        GateStep(
            "source_strict",
            "Source tree strict verifier",
            _python(
                "-m",
                "verifier.cli",
                "-d",
                "source",
                "--preset",
                "strict",
                "--check-imports",
                "--fail-on-debt",
                "-j",
                "0",
            ),
            timeout,
        ),
        GateStep(
            "verifier_strict",
            "Verifier tree strict verifier",
            _python(
                "-m",
                "verifier.cli",
                "-d",
                "verifier",
                "--preset",
                "strict",
                "--check-imports",
                "--fail-on-debt",
                "-j",
                "0",
            ),
            timeout,
        ),
    ]


def _compile_steps(timeout: int, generated: int) -> list[GateStep]:
    return [
        GateStep(
            "c23_hosted_compile",
            "Hosted C23 warning-clean object compile",
            _python(
                "tools/c_strict_compile.py",
                "--std",
                "c2x",
                "--surface-runtime",
                "--generated",
                str(generated),
            ),
            timeout,
        ),
        GateStep(
            "c23_freestanding_compile",
            "Freestanding C23 warning-clean object compile",
            _python(
                "tools/c_strict_compile.py",
                "--std",
                "c2x",
                "--freestanding",
                "--generated",
                str(generated),
            ),
            timeout,
        ),
    ]


def _pytest_step(timeout: int) -> GateStep:
    tests = [
        "tests/test_self_host_version_seed.py",
        "tests/test_self_host_gate_seed.py",
        "tests/test_freestanding_c_compile.py",
        "tests/test_c_format_specialization.py",
        "tests/test_process_run_argv_builtin.py",
    ]
    return GateStep(
        "focused_pytest",
        "Focused strict regression tests",
        _python("-m", "pytest", "-q", *tests),
        timeout,
    )


def _workload_step(timeout: int, quick: bool) -> GateStep:
    cmd = _python("tools/c23_workload_compare.py")
    if quick:
        cmd.append("--quick")
    return GateStep(
        "c23_workload_compare",
        "C23 parser/string/file/process workload comparison",
        cmd,
        timeout,
    )


def build_steps(args: argparse.Namespace) -> list[GateStep]:
    generated = 1 if args.quick else args.generated
    timeout = args.timeout
    steps: list[GateStep] = []
    if not args.skip_verifier:
        steps.extend(_strict_steps(timeout))
    if not args.skip_compile:
        steps.extend(_compile_steps(timeout, generated))
    if not args.skip_pytest:
        steps.append(_pytest_step(timeout))
    if not args.skip_workloads:
        steps.append(_workload_step(timeout, args.quick))
    requested = [step.step_id for step in steps]
    missing = sorted(set(requested) - set(GATE_STEP_IDS))
    if missing:
        raise RuntimeError(f"hard gate metadata missing step ids: {missing}")
    return steps


def write_reports(results: list[GateResult], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "hard_gate_current.json"
    md_path = out_dir / "hard_gate_current.md"
    payload = {
        "passed": all(row.passed for row in results),
        "results": [
            {
                "step_id": row.step_id,
                "title": row.title,
                "command": row.command,
                "returncode": row.returncode,
                "elapsed_seconds": row.elapsed_seconds,
                "timed_out": row.timed_out,
                "stdout_tail": row.stdout_tail,
                "stderr_tail": row.stderr_tail,
            }
            for row in results
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = ["# AILang Hard Gate", ""]
    lines.append(f"Overall: `{'pass' if payload['passed'] else 'fail'}`")
    lines.append("")
    lines.append("| Step | Status | Seconds | Exit |")
    lines.append("|---|---:|---:|---:|")
    for row in results:
        status = "pass" if row.passed else "fail"
        lines.append(
            f"| `{row.step_id}` | {status} | {row.elapsed_seconds:.2f} | "
            f"{row.returncode} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--skip-verifier", action="store_true")
    parser.add_argument("--skip-compile", action="store_true")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-workloads", action="store_true")
    parser.add_argument("--generated", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results: list[GateResult] = []
    for step in build_steps(args):
        result = _run_step(step)
        results.append(result)
        print(f"{step.step_id}: {'PASS' if result.passed else 'FAIL'}")
    json_path, md_path = write_reports(results, args.output_dir)
    print(f"\nHard gate reports: {json_path} and {md_path}")
    return 0 if all(row.passed for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

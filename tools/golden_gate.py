#!/usr/bin/env python3
"""AILang Golden Gate: release-grade correctness, backend, and workload gate.

A green process exit is not sufficient.  The gate requires source/verifier
quality, the regression suite, strict hosted/freestanding C emission, backend
semantic parity, AILang's primary execution modes, and equivalent-workload
parity.  Deliberately erased baselines are excluded because they do not perform
the same semantic work as the language implementations.
"""

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
DEFAULT_OUT_DIR = REPO_ROOT / "out" / "golden_gate"

GATE_STEP_IDS = [
    "source_strict",
    "verifier_strict",
    "full_pytest",
    "c23_hosted_compile",
    "c23_freestanding_compile",
    "backend_differential",
    "ailang_execution_matrix",
    "equivalent_workload_parity",
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
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _tail(text: str, limit: int = 5000) -> str:
    return text if len(text) <= limit else text[-limit:]


def _run_step(step: GateStep) -> GateResult:
    print(f"\n== {step.step_id}: {step.title}", flush=True)
    print(" ".join(step.command), flush=True)
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
            print(_tail(proc.stdout, 1600))
        if proc.stderr:
            print(_tail(proc.stderr, 1600), file=sys.stderr)
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
    common = ["--preset", "strict", "--check-imports", "--fail-on-debt", "-j", "0"]
    return [
        GateStep(
            "source_strict",
            "Source tree strict verifier",
            _python("-m", "verifier.cli", "-d", "source", *common),
            timeout,
        ),
        GateStep(
            "verifier_strict",
            "Verifier tree strict verifier",
            _python("-m", "verifier.cli", "-d", "verifier", *common),
            timeout,
        ),
    ]


def _pytest_step(timeout: int, quick: bool) -> GateStep:
    if quick:
        tests = [
            "tests/test_self_host_version_seed.py",
            "tests/test_self_host_gate_seed.py",
            "tests/test_freestanding_c_compile.py",
            "tests/test_c_format_specialization.py",
            "tests/test_bounds_and_recursion_codegen.py",
            "tests/test_c_compat_abi.py",
            "tests/test_benchmark_result_parsing.py",
        ]
        command = _python("-m", "pytest", "-q", *tests)
        title = "Focused Golden Gate regression suite"
    else:
        command = _python("-m", "pytest", "-q")
        title = "Full regression suite"
    return GateStep("full_pytest", title, command, timeout)


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


def _backend_step(timeout: int, generated: int, out_dir: Path) -> GateStep:
    return GateStep(
        "backend_differential",
        "C/LLVM semantic parity, expected output, and C live-leak differential",
        _python(
            "tools/backend_differential.py",
            "--surface-runtime",
            "--generated",
            str(generated),
            "--out-json",
            str(out_dir / "backend_differential.json"),
            "--out-md",
            str(out_dir / "backend_differential.md"),
        ),
        timeout,
    )


def _execution_matrix_step(timeout: int, quick: bool, out_dir: Path) -> GateStep:
    cases = ["loop_hash", "dict_ops", "fixed_array_sum"]
    if not quick:
        cases.extend(["file_io", "format_interp"])
    cmd = _python(
        "benchmarks/run_benchmarks.py",
        "--runs",
        "2" if quick else "3",
        "--warmup",
        "1",
        "--check-output",
        "--check-leaks",
        "--fail-on-error",
        "--output",
        str(out_dir / "execution_matrix.md"),
    )
    for case in cases:
        cmd.extend(["--case", case])
    for impl in ("ailang_aot", "ailang_jit_warm", "ailang_c_aot", "c23"):
        cmd.extend(["--impl", impl])
    return GateStep(
        "ailang_execution_matrix",
        "AILang AOT/JIT/C-AOT execution parity against C23",
        cmd,
        timeout,
    )


def _equivalent_workload_step(timeout: int, quick: bool) -> GateStep:
    cmd = _python(
        "tools/tri_language_benchmark.py",
        "--runs",
        "2" if quick else "3",
        "--warmup",
        "1",
        "--enforce-relative-c",
        "--max-relative-c-ratio",
        "1.5",
    )
    for case in ("numeric", "protocol", "ownership"):
        cmd.extend(["--case", case])
    # Only semantically equivalent implementations belong in the gate.
    # *_erased lanes intentionally perform less work and remain opt-in analysis.
    for impl in ("ailang_c", "ailang_llvm", "c"):
        cmd.extend(["--impl", impl])
    return GateStep(
        "equivalent_workload_parity",
        "Equivalent-workload output/leak parity (erased baselines excluded)",
        cmd,
        timeout,
    )


def build_steps(args: argparse.Namespace) -> list[GateStep]:
    generated = 1 if args.quick else args.generated
    timeout = args.timeout
    args.output_dir.mkdir(parents=True, exist_ok=True)
    steps: list[GateStep] = []
    if not args.skip_verifier:
        steps.extend(_strict_steps(timeout))
    if not args.skip_pytest:
        steps.append(_pytest_step(timeout, args.quick))
    if not args.skip_compile:
        steps.extend(_compile_steps(timeout, generated))
    if not args.skip_backend_differential:
        steps.append(_backend_step(timeout, generated, args.output_dir))
    if not args.skip_execution_matrix:
        steps.append(_execution_matrix_step(timeout, args.quick, args.output_dir))
    if not args.skip_equivalent_workloads:
        steps.append(_equivalent_workload_step(timeout, args.quick))
    requested = [step.step_id for step in steps]
    missing = sorted(set(requested) - set(GATE_STEP_IDS))
    if missing:
        raise RuntimeError(f"Golden Gate metadata missing step ids: {missing}")
    return steps


def write_reports(results: list[GateResult], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "golden_gate_current.json"
    md_path = out_dir / "golden_gate_current.md"
    payload = {
        "name": "AILang Golden Gate",
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

    lines = ["# AILang Golden Gate", ""]
    lines.append(f"Overall: `{'GOLDEN' if payload['passed'] else 'NOT GOLDEN'}`")
    lines.extend(["", "| Step | Status | Seconds | Exit |", "|---|---:|---:|---:|"])
    for row in results:
        status = "golden" if row.passed else "fail"
        lines.append(
            f"| `{row.step_id}` | {status} | {row.elapsed_seconds:.2f} | {row.returncode} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--skip-verifier", action="store_true")
    parser.add_argument("--skip-compile", action="store_true")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-backend-differential", action="store_true")
    parser.add_argument("--skip-execution-matrix", action="store_true")
    parser.add_argument("--skip-equivalent-workloads", action="store_true")
    parser.add_argument("--generated", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results: list[GateResult] = []
    for step in build_steps(args):
        result = _run_step(step)
        results.append(result)
        print(f"{step.step_id}: {'GOLDEN' if result.passed else 'FAIL'}", flush=True)
    json_path, md_path = write_reports(results, args.output_dir)
    golden = all(row.passed for row in results)
    print(f"\nGolden Gate reports: {json_path} and {md_path}")
    print(f"AILang Golden Gate: {'GOLDEN' if golden else 'NOT GOLDEN'}")
    return 0 if golden else 1


if __name__ == "__main__":
    raise SystemExit(main())

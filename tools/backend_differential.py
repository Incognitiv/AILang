#!/usr/bin/env python3
"""C-vs-LLVM differential runner for corpus and generated AILang programs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from validation_programs import (
    ProgramCase,
    generated_cases,
    materialize_case,
    runtime_surface_cases,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
AILANG = REPO_ROOT / "ailang.py"
CORPUS_DIR = REPO_ROOT / "tests" / "corpus"
DEFAULT_CORPUS = [
    "01_hello",
    "02_factorial",
    "03_fibonacci",
    "04_string_concat",
    "05_arena_routed",
]
LEAK_RE = re.compile(
    r"total allocated:\s*(\d+)\s*bytes\s+"
    r"total freed:\s*(\d+)\s*bytes\s+"
    r"live at exit:\s*(\d+)\s*bytes",
    re.DOTALL,
)


@dataclass
class BackendRun:
    backend: str
    compile_exit: int
    run_exit: int | None
    stdout_lines: list[str]
    stderr_preview: str
    leak_live_bytes: int | None = None


@dataclass
class DifferentialResult:
    name: str
    expected_lines: list[str]
    c: BackendRun
    llvm: BackendRun
    ok: bool
    reason: str


def _result_counts(results: list[DifferentialResult]) -> dict[str, int]:
    counts: dict[str, int] = {"ok": 0}
    for row in results:
        key = "ok" if row.ok else row.reason
        counts[key] = counts.get(key, 0) + 1
    return counts


def render_markdown_report(
    results: list[DifferentialResult], *, seed: int, generated: int
) -> str:
    failures = [row for row in results if not row.ok]
    counts = _result_counts(results)
    lines = [
        "# Backend Differential Report",
        "",
        f"- Seed: `{seed}`",
        f"- Generated cases: `{generated}`",
        f"- Total cases: `{len(results)}`",
        f"- Failures: `{len(failures)}`",
        "",
        "## Result Counts",
        "",
        "| Reason | Count |",
        "| --- | ---: |",
    ]
    for reason, count in sorted(counts.items()):
        lines.append(f"| `{reason}` | {count} |")

    lines.extend(["", "## Cases", "", "| Case | Status | Reason | C leak live B |"])
    lines.append("| --- | --- | --- | ---: |")
    for row in results:
        status = "pass" if row.ok else "fail"
        leak = "n/a" if row.c.leak_live_bytes is None else str(row.c.leak_live_bytes)
        lines.append(f"| `{row.name}` | {status} | `{row.reason}` | {leak} |")

    if failures:
        lines.extend(["", "## Failure Details", ""])
        for row in failures:
            lines.extend(
                [
                    f"### {row.name}",
                    "",
                    f"- Reason: `{row.reason}`",
                    f"- C compile/run: `{row.c.compile_exit}` / `{row.c.run_exit}`",
                    f"- LLVM compile/run: `{row.llvm.compile_exit}` / `{row.llvm.run_exit}`",
                    f"- C stdout: `{row.c.stdout_lines}`",
                    f"- LLVM stdout: `{row.llvm.stdout_lines}`",
                    "",
                ]
            )
            if row.c.stderr_preview:
                lines.extend(["```text", row.c.stderr_preview.strip(), "```", ""])
            if row.llvm.stderr_preview:
                lines.extend(["```text", row.llvm.stderr_preview.strip(), "```", ""])

    return "\n".join(lines) + "\n"


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_leak(text: str) -> int | None:
    match = LEAK_RE.search(text)
    if not match:
        return None
    return int(match.group(3))


def _run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 300):
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _helper_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def _compile_and_run(src: Path, out_stem: Path, backend: str) -> BackendRun:
    compile_cmd = [
        sys.executable,
        str(AILANG),
        str(src),
        f"--backend={backend}",
        "-o",
        str(out_stem),
    ]
    compile_proc = _run(compile_cmd, timeout=420)
    if compile_proc.returncode != 0:
        return BackendRun(
            backend=backend,
            compile_exit=compile_proc.returncode,
            run_exit=None,
            stdout_lines=[],
            stderr_preview=(compile_proc.stdout + compile_proc.stderr)[-1200:],
        )

    exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
    env = dict(os.environ)
    if backend == "c":
        env["AILANG_LEAK_REPORT"] = "1"
    run_proc = _run([str(exe)], env=env, timeout=240)
    combined = run_proc.stdout + "\n" + run_proc.stderr
    return BackendRun(
        backend=backend,
        compile_exit=compile_proc.returncode,
        run_exit=run_proc.returncode,
        stdout_lines=_non_empty_lines(run_proc.stdout),
        stderr_preview=run_proc.stderr[-1200:],
        leak_live_bytes=_parse_leak(combined) if backend == "c" else None,
    )


def _corpus_cases(names: list[str]) -> list[ProgramCase]:
    cases: list[ProgramCase] = []
    for name in names:
        path = CORPUS_DIR / f"{name}.ail"
        cases.append(ProgramCase(name, path.read_text(encoding="utf-8"), []))
    return cases


def _evaluate(case: ProgramCase, tmp: Path) -> DifferentialResult:
    try:
        src = materialize_case(
            case,
            tmp,
            helper_compiler=_helper_compiler(),
            helper_flags=("-std=gnu23",),
        )
    except Exception as exc:  # noqa: BLE001 - differential runner reports all cases.
        failed = BackendRun(
            backend="materialize",
            compile_exit=1,
            run_exit=None,
            stdout_lines=[],
            stderr_preview=str(exc)[-1200:],
        )
        return DifferentialResult(
            name=case.name,
            expected_lines=case.expected_lines,
            c=failed,
            llvm=failed,
            ok=False,
            reason="materialize_failed",
        )
    c_run = _compile_and_run(src, tmp / f"{case.name}_c", "c")
    llvm_run = _compile_and_run(src, tmp / f"{case.name}_llvm", "llvm")

    ok = True
    reason = "ok"
    if c_run.compile_exit != 0 or c_run.run_exit != 0:
        ok = False
        reason = "c_backend_failed"
    elif llvm_run.compile_exit != 0 or llvm_run.run_exit != 0:
        ok = False
        reason = "llvm_backend_failed"
    elif c_run.stdout_lines != llvm_run.stdout_lines:
        ok = False
        reason = "backend_output_mismatch"
    elif case.expected_lines and c_run.stdout_lines != case.expected_lines:
        ok = False
        reason = "expected_output_mismatch"
    elif c_run.leak_live_bytes not in (None, 0):
        ok = False
        reason = f"c_live_leak_{c_run.leak_live_bytes}_bytes"

    return DifferentialResult(
        name=case.name,
        expected_lines=case.expected_lines,
        c=c_run,
        llvm=llvm_run,
        ok=ok,
        reason=reason,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=int, default=12)
    parser.add_argument("--seed", type=int, default=166)
    parser.add_argument("--no-corpus", action="store_true")
    parser.add_argument(
        "--surface-runtime",
        action="store_true",
        help="Include curated runtime-bearing syntax surface cases",
    )
    parser.add_argument("--program", action="append", default=[])
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args()

    cases: list[ProgramCase] = []
    if not args.no_corpus:
        cases.extend(_corpus_cases(args.program or DEFAULT_CORPUS))
    if args.surface_runtime:
        cases.extend(runtime_surface_cases())
    cases.extend(generated_cases(args.generated, args.seed))

    root = Path(tempfile.mkdtemp(prefix="ailang_backend_diff_", dir=REPO_ROOT / "out"))
    try:
        results = [_evaluate(case, root) for case in cases]
        failures = [row for row in results if not row.ok]
        print(
            f"backend differential seed={args.seed} cases={len(results)} "
            f"failures={len(failures)}"
        )
        for row in failures[:20]:
            print(f"- {row.name}: {row.reason}")
            print(f"  c={row.c.stdout_lines!r} llvm={row.llvm.stdout_lines!r}")
            if row.c.stderr_preview:
                print(f"  c stderr: {row.c.stderr_preview[:240]}")
            if row.llvm.stderr_preview:
                print(f"  llvm stderr: {row.llvm.stderr_preview[:240]}")

        if args.out_json is not None:
            args.out_json.parent.mkdir(parents=True, exist_ok=True)
            args.out_json.write_text(
                json.dumps([asdict(row) for row in results], indent=2),
                encoding="utf-8",
            )
        if args.out_md is not None:
            args.out_md.parent.mkdir(parents=True, exist_ok=True)
            args.out_md.write_text(
                render_markdown_report(
                    results, seed=args.seed, generated=args.generated
                ),
                encoding="utf-8",
            )
        return 1 if failures else 0
    finally:
        if args.keep:
            print(f"kept artifacts: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

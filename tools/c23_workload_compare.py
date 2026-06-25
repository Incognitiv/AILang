#!/usr/bin/env python3
"""C23 comparison driver for parser, string, file, and process workloads."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = REPO_ROOT / "benchmarks"
RESULTS_ROOT = BENCH_ROOT / "results"
OUT_ROOT = REPO_ROOT / "out" / "c23_workload_compare"
AILANG = REPO_ROOT / "ailang.py"


@dataclass
class CommandResult:
    name: str
    returncode: int
    elapsed_ms: float
    stdout_tail: str
    stderr_tail: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclass
class ProcessResult:
    impl: str
    status: str
    compile_ms: float | None
    runs_ms: list[float]
    checksum: int | None
    note: str = ""


def _tail(text: str, limit: int = 2400) -> str:
    return text if len(text) <= limit else text[-limit:]


def _run(cmd: list[str], *, timeout: int = 300) -> CommandResult:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return CommandResult(
            " ".join(cmd),
            proc.returncode,
            elapsed_ms,
            _tail(proc.stdout),
            _tail(proc.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return CommandResult(
            " ".join(cmd), 124, elapsed_ms, _tail(stdout), _tail(stderr)
        )


def _exe(path: Path) -> Path:
    return path.with_suffix(".exe") if os.name == "nt" else path


def _last_int(text: str) -> int | None:
    for token in reversed(text.replace("\r", "\n").split()):
        try:
            return int(token)
        except ValueError:
            continue
    return None


def _json_string(text: str) -> str:
    return json.dumps(text)


def _compile_ailang(source: Path, stem: Path) -> tuple[Path | None, float, str]:
    cmd = [sys.executable, str(AILANG), str(source), "--backend=c", "-o", str(stem)]
    result = _run(cmd, timeout=300)
    if not result.passed:
        return None, result.elapsed_ms, result.stdout_tail + result.stderr_tail
    return _exe(stem), result.elapsed_ms, ""


def _compile_c(source: Path, exe: Path) -> tuple[Path | None, float, str]:
    cc = shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        return None, 0.0, "no C compiler found"
    cmd = [
        cc,
        "-std=c2x",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        str(source),
        "-o",
        str(exe),
    ]
    result = _run(cmd, timeout=300)
    if not result.passed:
        return None, result.elapsed_ms, result.stdout_tail + result.stderr_tail
    return exe, result.elapsed_ms, ""


def _compile_rust(source: Path, exe: Path) -> tuple[Path | None, float, str]:
    rustc = shutil.which("rustc")
    if rustc is None:
        return None, 0.0, "rustc not found"
    cmd = [rustc, "-O", str(source), "-o", str(exe)]
    result = _run(cmd, timeout=300)
    if not result.passed:
        return None, result.elapsed_ms, result.stdout_tail + result.stderr_tail
    return exe, result.elapsed_ms, ""


def _write_process_sources(out: Path, iters: int) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    py_exe_raw = str(Path(sys.executable).resolve())
    py_exe_ail = py_exe_raw.replace("\\", "/")
    sources = {
        "ailang": out / "process_spawn.ail",
        "c23": out / "process_spawn.c",
        "python": out / "process_spawn.py",
        "rust": out / "process_spawn.rs",
    }
    sources["ailang"].write_text(
        f"""const int PROCESS_ITERS = {iters}

def main(): int
    checksum = 0
    i = 0
    while i < PROCESS_ITERS then
        args = str_array_new(3)
        args = str_array_push(args, "{py_exe_ail}")
        args = str_array_push(args, "-c")
        args = str_array_push(args, "pass")
        rc = process_run_argv(args)
        dealloc_str_array(args)
        if rc != 0 then
            return rc
        end
        checksum = checksum + i
        i++
    end
    print checksum
    return 0
end
""",
        encoding="utf-8",
    )
    sources["c23"].write_text(
        f"""#include <stdio.h>
#ifdef _WIN32
#include <process.h>
#else
#include <sys/wait.h>
#include <unistd.h>
#endif

#define PROCESS_ITERS {iters}
static const char *PYTHON_EXE = {_json_string(py_exe_raw)};

static int run_child(void) {{
#ifdef _WIN32
    const char *argv[] = {{PYTHON_EXE, "-c", "pass", NULL}};
    return (int)_spawnv(_P_WAIT, PYTHON_EXE, argv);
#else
    pid_t pid = fork();
    if (pid == 0) {{
        char *const argv[] = {{(char *)PYTHON_EXE, (char *)"-c", (char *)"pass", NULL}};
        execv(PYTHON_EXE, argv);
        _exit(127);
    }}
    if (pid < 0) {{
        return 127;
    }}
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {{
        return 127;
    }}
    if (WIFEXITED(status)) {{
        return WEXITSTATUS(status);
    }}
    if (WIFSIGNALED(status)) {{
        return 128 + WTERMSIG(status);
    }}
    return 127;
#endif
}}

int main(void) {{
    int checksum = 0;
    for (int i = 0; i < PROCESS_ITERS; ++i) {{
        int rc = run_child();
        if (rc != 0) {{
            return rc;
        }}
        checksum += i;
    }}
    printf("%d\\n", checksum);
    return 0;
}}
""",
        encoding="utf-8",
    )
    sources["python"].write_text(
        f"""import subprocess
import sys

PYTHON_EXE = {py_exe_raw!r}
PROCESS_ITERS = {iters}

checksum = 0
for i in range(PROCESS_ITERS):
    proc = subprocess.run(
        [PYTHON_EXE, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        sys.exit(proc.returncode)
    checksum += i
print(checksum)
""",
        encoding="utf-8",
    )
    sources["rust"].write_text(
        f"""use std::process::Command;

const PYTHON_EXE: &str = r#"{py_exe_raw}"#;
const PROCESS_ITERS: i32 = {iters};

fn main() {{
    let mut checksum = 0;
    for i in 0..PROCESS_ITERS {{
        let status = Command::new(PYTHON_EXE)
            .arg("-c")
            .arg("pass")
            .status()
            .expect("spawn python");
        if !status.success() {{
            std::process::exit(status.code().unwrap_or(127));
        }}
        checksum += i;
    }}
    println!("{{}}", checksum);
}}
""",
        encoding="utf-8",
    )
    return sources


def _measure_process(
    command: list[str], runs: int, warmup: int
) -> tuple[list[float], int | None, str]:
    samples: list[float] = []
    checksum: int | None = None
    for index in range(runs + warmup):
        result = _run(command, timeout=180)
        if not result.passed:
            return samples, checksum, result.stdout_tail + result.stderr_tail
        value = _last_int(result.stdout_tail)
        if value is None:
            return samples, checksum, "no integer checksum parsed"
        if index >= warmup:
            samples.append(result.elapsed_ms)
            if checksum is None:
                checksum = value
            elif checksum != value:
                return samples, checksum, f"checksum changed: {checksum} vs {value}"
    return samples, checksum, ""


def run_process_workload(args: argparse.Namespace) -> list[ProcessResult]:
    sources = _write_process_sources(OUT_ROOT / "process", args.process_iters)
    builds: dict[str, tuple[Path | None, float | None, str]] = {}
    builds["ailang_aot"] = _compile_ailang(
        sources["ailang"], OUT_ROOT / "process_ailang"
    )
    builds["c23"] = _compile_c(sources["c23"], _exe(OUT_ROOT / "process_c23"))
    builds["python"] = (sources["python"], None, "")
    if not args.skip_rust:
        builds["rust"] = _compile_rust(sources["rust"], _exe(OUT_ROOT / "process_rust"))

    results: list[ProcessResult] = []
    for impl, (exe, compile_ms, note) in builds.items():
        if exe is None:
            results.append(
                ProcessResult(
                    impl,
                    "skip" if "not found" in note else "fail",
                    compile_ms,
                    [],
                    None,
                    note,
                )
            )
            continue
        command = [sys.executable, str(exe)] if impl == "python" else [str(exe)]
        runs_ms, checksum, run_note = _measure_process(command, args.runs, args.warmup)
        status = "ok" if not run_note else "fail"
        results.append(
            ProcessResult(impl, status, compile_ms, runs_ms, checksum, run_note)
        )
    checksums = {row.checksum for row in results if row.status == "ok"}
    if len(checksums) > 1:
        for row in results:
            if row.status == "ok":
                row.status = "fail"
                row.note = "process checksum parity mismatch"
    return results


def _core_benchmark_ok(path: Path) -> tuple[bool, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for case, impls in data.get("results", {}).items():
        checksums = set()
        for impl, row in impls.items():
            if row.get("status") != "ok":
                failures.append(f"{case}/{impl}: {row.get('status')} {row.get('note')}")
            checksum = row.get("checksum")
            if checksum is not None:
                checksums.add(checksum)
        if len(checksums) > 1:
            failures.append(f"{case}: checksum mismatch {sorted(checksums)}")
    return not failures, "; ".join(failures)


def _tri_ok(path: Path) -> tuple[bool, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    failures = [
        f"{row['kernel']}/{row['impl']}: {row['status']} {row.get('note', '')}"
        for row in data.get("results", [])
        if row.get("status") not in {"ok", "skip"}
    ]
    for kernel in {row["kernel"] for row in data.get("results", [])}:
        outputs = {
            row.get("output")
            for row in data.get("results", [])
            if row.get("kernel") == kernel and row.get("status") == "ok"
        }
        if len(outputs) > 1:
            failures.append(f"{kernel}: output mismatch {sorted(outputs)}")
    return not failures, "; ".join(failures)


def run_existing_workloads(args: argparse.Namespace) -> list[CommandResult]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    core_report = RESULTS_ROOT / "c23_workload_core.md"
    cases = (
        ["file_io", "format_str_int"]
        if args.quick
        else [
            "file_io",
            "format_str_int",
            "format_hex",
            "loop_hash",
        ]
    )
    cmd = [
        sys.executable,
        "benchmarks/run_benchmarks.py",
        "--runs",
        str(args.runs),
        "--warmup",
        str(args.warmup),
        "--output",
        str(core_report),
        "--check-output",
    ]
    for case in cases:
        cmd.extend(["--case", case])
    for impl in ["ailang_aot", "c23", "python"]:
        cmd.extend(["--impl", impl])
    results = [_run(cmd, timeout=900)]
    if results[-1].passed:
        ok, detail = _core_benchmark_ok(core_report.with_suffix(".json"))
        if not ok:
            results.append(CommandResult("core JSON validation", 1, 0.0, "", detail))

    tri_cmd = [
        sys.executable,
        "tools/tri_language_benchmark.py",
        "--runs",
        str(args.runs),
        "--warmup",
        str(args.warmup),
        "--case",
        "protocol",
        "--case",
        "ownership",
        "--impl",
        "ailang_c",
        "--impl",
        "c",
    ]
    if args.skip_leak_checks:
        tri_cmd.append("--skip-leak-checks")
    results.append(_run(tri_cmd, timeout=900))
    tri_json = RESULTS_ROOT / "tri_language_gauntlet.json"
    if results[-1].passed and tri_json.exists():
        ok, detail = _tri_ok(tri_json)
        if not ok:
            results.append(CommandResult("tri JSON validation", 1, 0.0, "", detail))
    return results


def write_report(
    command_results: list[CommandResult],
    process_results: list[ProcessResult],
) -> tuple[Path, Path]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_ROOT / "c23_workload_compare.json"
    md_path = RESULTS_ROOT / "c23_workload_compare.md"
    payload = {
        "commands": [row.__dict__ for row in command_results],
        "process": [row.__dict__ for row in process_results],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# C23 Workload Compare",
        "",
        "Covers file/string workloads, protocol-parser shape, ownership/string churn, and process spawn/wait.",
        "",
        "## Commands",
        "",
        "| Command | Status | ms |",
        "|---|---:|---:|",
    ]
    for row in command_results:
        status = "pass" if row.passed else "fail"
        lines.append(f"| `{row.name}` | {status} | {row.elapsed_ms:.2f} |")
    lines.extend(
        [
            "",
            "## Process Spawn/Wait",
            "",
            "| Impl | Status | Compile ms | Median ms | Checksum | Note |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for proc_row in process_results:
        median = statistics.median(proc_row.runs_ms) if proc_row.runs_ms else None
        lines.append(
            "| {impl} | {status} | {compile_ms} | {median} | {checksum} | {note} |".format(
                impl=proc_row.impl,
                status=proc_row.status,
                compile_ms=(
                    "" if proc_row.compile_ms is None else f"{proc_row.compile_ms:.2f}"
                ),
                median="" if median is None else f"{median:.2f}",
                checksum="" if proc_row.checksum is None else proc_row.checksum,
                note=proc_row.note.replace("|", "\\|"),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--process-iters", type=int, default=8)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--process-only", action="store_true")
    parser.add_argument("--skip-rust", action="store_true")
    parser.add_argument("--skip-leak-checks", action="store_true")
    args = parser.parse_args()
    if args.quick:
        args.runs = min(args.runs, 1)
        args.warmup = 0
        args.process_iters = min(args.process_iters, 2)
        args.skip_rust = True
        args.skip_leak_checks = True
    return args


def main() -> int:
    args = parse_args()
    command_results: list[CommandResult] = []
    if not args.process_only:
        command_results = run_existing_workloads(args)
    process_results = run_process_workload(args)
    json_path, md_path = write_report(command_results, process_results)
    print(f"reports: {json_path} and {md_path}")
    failed_commands = [row for row in command_results if not row.passed]
    failed_process = [
        row for row in process_results if row.status not in {"ok", "skip"}
    ]
    return 1 if failed_commands or failed_process else 0


if __name__ == "__main__":
    raise SystemExit(main())

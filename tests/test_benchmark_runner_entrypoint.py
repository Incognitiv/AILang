"""Exercise the runner entrypoint and orchestration, not a replacement runner."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "benchmarks" / "run_benchmarks.py"


def test_real_cli_help_executes() -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--fail-on-error" in result.stdout
    assert "--runs" in result.stdout


@dataclass
class StubMeasurement:
    status: str
    compile_ms: float | None = None
    runs_ms: list[float] | None = None
    output: str | None = None
    output_tokens: str | None = None
    checksum: int | None = None
    leak_alloc_bytes: int | None = None
    leak_freed_bytes: int | None = None
    leak_live_bytes: int | None = None
    peak_rss_bytes: int | None = None
    note: str | None = None


@pytest.fixture
def runner_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Mock native/benchmark boundaries; execute the real runner file unchanged."""
    cases = ModuleType("benchmark_cases")
    cases.define_cases = lambda: [
        SimpleNamespace(name="loop_hash", files={"python": tmp_path / "input.py"})
    ]
    report = ModuleType("benchmark_report")
    reports = []
    report.generate_report = lambda *args: reports.append(args)
    support = ModuleType("benchmark_support")
    support.Measurement = StubMeasurement
    support.BenchmarkCase = object
    support.AILEXEC = ROOT / "ailang.py"
    support.AilangTool = sys.executable
    support.OUT_DIR = tmp_path / "out"
    support.ROOT = ROOT
    support._apply_leak_check = lambda value, **kwargs: value
    support._coerce_optional_int = lambda value: value
    support._ensure_dir = lambda path: path.mkdir(parents=True, exist_ok=True)
    support._extract_result_int = lambda value: int(value.strip())
    support._median = lambda samples: samples[0] if samples else None
    support._parse_leak_report = lambda value: None
    support._run_cmd = lambda *args, **kwargs: (0, "17\n", "", 0.25, None)
    support.command_exists = lambda name: None
    for name in ("compile_ailang_aot", "compile_ailang_c_aot", "compile_c23", "compile_rust"):
        setattr(support, name, lambda *args: (False, 0, None, "not used"))
    for name, module in (("benchmark_cases", cases), ("benchmark_report", report),
                         ("benchmark_support", support)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(ROOT / "benchmarks"))
    return support, reports


def test_main_guard_really_runs_benchmark(runner_dependencies, monkeypatch) -> None:
    _support, reports = runner_dependencies
    monkeypatch.setattr(sys, "argv", [str(RUNNER), "--case", "loop_hash",
                                      "--impl", "python", "--runs", "2",
                                      "--check-output", "--fail-on-error"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(RUNNER), run_name="__main__")
    assert exit_info.value.code == 0
    assert len(reports) == 1
    measurement = reports[0][3]["loop_hash"]["python"]
    assert measurement.runs_ms == [0.25, 0.25]
    assert measurement.checksum == 17


def load_runner():
    return runpy.run_path(str(RUNNER), run_name="runner_unit_test")


@pytest.mark.parametrize("option,value", [("--runs", "0"), ("--runs", "-1"),
                                          ("--warmup", "-1"), ("--leak-threshold", "-1")])
def test_invalid_counts_fail_before_work(runner_dependencies, monkeypatch, option, value):
    monkeypatch.setattr(sys, "argv", [str(RUNNER), option, value])
    with pytest.raises(SystemExit) as exit_info:
        load_runner()["parse_args"]()
    assert exit_info.value.code == 2


@pytest.mark.parametrize("payload", [[], None, 1, {"status": "ok", "runs_ms": []},
                                     {"status": "ok", "runs_ms": [float("nan")], "checksum": 17}])
def test_bad_jit_payload_is_failed_measurement(runner_dependencies, payload):
    support, _reports = runner_dependencies
    support._run_cmd = lambda *args, **kwargs: (
        0, "JIT_WARM_RESULT=" + json.dumps(payload), "", 1, None
    )
    result = load_runner()["_build_measurements_from_json"](["unused"], run_count=1)
    assert result.status == "fail"


def test_jit_integer_fallback_has_working_regex(runner_dependencies):
    support, _reports = runner_dependencies
    support._run_cmd = lambda *args, **kwargs: (
        0, 'JIT_WARM_RESULT={"status":"ok","runs_ms":[0.5]}\n17\n', "", 1, None
    )
    result = load_runner()["_build_measurements_from_json"](["unused"], run_count=1)
    assert result.status == "ok"
    assert result.checksum == 17


def test_missing_rows_fail_main(runner_dependencies, monkeypatch):
    runner = load_runner()
    main = runner["main"]
    monkeypatch.setattr(sys, "argv", [str(RUNNER), "--case", "loop_hash",
                                      "--impl", "python", "--fail-on-error"])
    monkeypatch.setitem(main.__globals__, "run_benchmarks", lambda *args, **kwargs: {})
    assert main() == 1

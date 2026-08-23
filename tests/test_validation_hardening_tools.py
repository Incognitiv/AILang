from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
TOOLS_ROOT = REPO_ROOT / "tools"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from parser.parser import Parser

from c_strict_compile import _strict_flags
from lexer.scan import tokenize
from validation_programs import generated_cases, runtime_surface_cases


def test_generated_validation_programs_parse() -> None:
    cases = generated_cases(count=16, seed=166)
    assert len(cases) == 16
    tags = {tag for case in cases for tag in case.tags}
    assert {"record-value", "array", "ffi", "pointer", "ownership"} <= tags
    for case in cases:
        nodes = Parser(tokenize(case.source)).parse_program()
        assert nodes
        assert case.expected_lines


def test_runtime_surface_programs_parse() -> None:
    cases = runtime_surface_cases()
    assert len(cases) >= 8
    tags = {tag for case in cases for tag in case.tags}
    assert {"surface-runtime", "kw:ALLOC", "kw:RECORD", "kw:GATE_XOR"} <= tags
    for case in cases:
        nodes = Parser(tokenize(case.source)).parse_program()
        assert nodes
        assert case.expected_lines


def test_strict_c_compile_flags_are_warning_clean_policy() -> None:
    flags = _strict_flags("c23")

    assert "-std=c23" in flags
    assert {"-Wall", "-Wextra", "-Werror", "-pedantic"} <= set(flags)


def test_parser_fuzz_tool_smoke() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "parser_fuzz.py"),
            "--count",
            "5",
            "--seed",
            "166",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "failures=0" in proc.stdout

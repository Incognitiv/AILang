#!/usr/bin/env python3
"""Kernel-check actual AILang literal values, including native execution results.

The expected semantics live in Lean, not in a Python replica of the decoder.
Certificates contain source spellings and observed values as lists of numbers.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "proof"
sys.path.insert(0, str(ROOT / "source"))
BACKENDS = ("llvm", "c", "jit")
CHANNELS = ("lexer", "ast", "parser", *BACKENDS)
END_MARKER = "AILANG_LITERAL_PROOF_DONE"


@dataclass(frozen=True)
class LiteralCase:
    label: str
    literal: str


@dataclass(frozen=True)
class Observation:
    case: LiteralCase
    channel: str
    value: str


def _quoted(value: str) -> str:
    """Create source input; this is an encoder, not the value oracle."""
    escapes = {"\\": r"\\", '"': r'\"', "\n": r"\n", "\r": r"\r", "\t": r"\t", "\0": r"\0"}
    return '"' + "".join(escapes.get(char, char) for char in value) + '"'


def native_cases() -> list[LiteralCase]:
    spellings = [
        r'""', r'"a"', r'"\\n"', r'"\\t"', r'"\\r"', r'"\\0"',
        r'"\\x41"', r'"\\u017c"', r'"\x5cn"', r'"\u005cn"',
        r'"\x5cu0041"', r'"\x41"', r'"\u017c"', r'"\n\t\r"',
        r'"C:\\work\\new\\test"', r'"\q\xG0\uZZZZ"', r'"\""',
        '"żółć"', '"😀"', r'"\x7f"', r'"\xff"', r'"\u03bb"',
    ]
    return [LiteralCase(f"native-{index}", text) for index, text in enumerate(spellings)]


def frontend_cases() -> list[LiteralCase]:
    spellings = [case.literal for case in native_cases()]
    alphabet = ("a", "\\", "n", "x", "0", '"', "\n", "\r", "\t", "ż", "\0")
    for size in range(3):
        spellings.extend(_quoted("".join(chars)) for chars in itertools.product(alphabet, repeat=size))
    spellings.extend(f'"\\x{value:02x}"' for value in range(256))
    spellings.extend(f'"\\u{value:04x}"' for value in (0, 65, 380, 955, 4096, 65535))
    spellings.extend([r'"\0BACKSLASH\0"', r'"\0BACKSLASH\0n"'])
    return [LiteralCase(f"frontend-{index}", text) for index, text in enumerate(dict.fromkeys(spellings))]


def collect_frontend(cases: list[LiteralCase]) -> list[Observation]:
    from lexer.scan import tokenize, unescape_string
    from parser.ast_expr_nodes import StringLit
    from parser.parser import Parser

    observations = []
    for case in cases:
        tokens = tokenize(case.literal)
        parser = Parser(tokens)
        parsed = parser.parse_expression()
        if not isinstance(parsed, StringLit) or parser.pos != len(tokens):
            raise ValueError(f"parser did not consume exactly one literal: {case.label}")
        observations.extend([
            Observation(case, "lexer", unescape_string(case.literal)),
            Observation(case, "ast", StringLit(case.literal).value),
            Observation(case, "parser", parsed.value),
        ])
    return observations


def native_source(cases: list[LiteralCase]) -> str:
    lines = ["void main():"]
    for index, case in enumerate(cases):
        name = f"value_{index}"
        cursor = f"cursor_{index}"
        lines.extend([
            f"    string {name} = {case.literal}",
            f'    print("AILANG_LITERAL_PROOF_{index}")',
            f"    print(strlen({name}))",
            f"    int {cursor} = 0",
            f"    while {cursor} < strlen({name}):",
            f"        print(char_at({name}, {cursor}))",
            f"        {cursor} = {cursor} + 1",
            "    end",
        ])
    lines.extend([f'    print("{END_MARKER}")', "end", ""])
    return "\n".join(lines)


def parse_native_output(stdout: str, cases: list[LiteralCase], backend: str) -> list[Observation]:
    """Accept all expected frames exactly once; banners may only precede them."""
    if not cases:
        raise ValueError("native proof corpus is empty")
    lines = stdout.splitlines()
    try:
        start = lines.index("AILANG_LITERAL_PROOF_0")
    except ValueError as error:
        raise ValueError(f"{backend}: missing native proof output") from error
    position = start
    observations = []
    for index, case in enumerate(cases):
        if position >= len(lines) or lines[position] != f"AILANG_LITERAL_PROOF_{index}":
            raise ValueError(f"{backend}: missing or reordered case {index}")
        position += 1
        if position >= len(lines):
            raise ValueError("native proof length is missing")
        count = int(lines[position])
        position += 1
        if not 0 <= count <= 4096 or position + count > len(lines):
            raise ValueError("native proof length is invalid")
        octets = bytes(int(value) for value in lines[position:position + count])
        observations.append(Observation(case, backend, octets.decode("utf-8")))
        position += count
    if position >= len(lines) or lines[position] != END_MARKER:
        raise ValueError("native proof did not finish the complete corpus")
    if any(line.startswith("AILANG_LITERAL_PROOF_") for line in lines[position + 1:]):
        raise ValueError("duplicate native proof output")
    return observations


def _run(command: list[str], *, cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout)


def collect_native(cases: list[LiteralCase], directory: Path) -> list[Observation]:
    source = directory / "literal_semantics.ail"
    source.write_text(native_source(cases), encoding="utf-8")
    observations = []
    for backend in BACKENDS:
        output = directory / f"literals-{backend}"
        command = [sys.executable, str(ROOT / "ailang.py"), str(source)]
        if backend != "jit":
            command.extend(["--backend=" + backend, "-o", str(output)])
        compiled = _run(command, cwd=ROOT)
        if compiled.returncode:
            raise RuntimeError(f"{backend} compilation failed:\n{compiled.stdout}\n{compiled.stderr}")
        executed = compiled if backend == "jit" else _run([str(output)], cwd=directory)
        if executed.returncode:
            raise RuntimeError(f"{backend} execution failed:\n{executed.stdout}\n{executed.stderr}")
        (directory / f"{backend}-stdout.txt").write_text(executed.stdout, encoding="utf-8")
        observations.extend(parse_native_output(executed.stdout, cases, backend))
    return observations


def _lean_numbers(value: str) -> str:
    # Only decimal numbers enter generated Lean; source text is never injected.
    return "[" + ",".join(str(ord(char)) for char in value) + "]"


def render_proof(observations: list[Observation]) -> str:
    if not observations:
        raise ValueError("empty observations cannot certify compiler behavior")
    lines = ["import AILangProof.StringLiterals", "open AILangProof.StringLiterals", ""]
    for index, observation in enumerate(observations):
        literal = observation.case.literal
        if len(literal) < 2 or not (literal.startswith('"') and literal.endswith('"')):
            raise ValueError("a certificate requires the original quoted spelling")
        lines.append(
            f"theorem observed_{index} : decode {_lean_numbers(literal[1:-1])} = "
            f"{_lean_numbers(observation.value)} := by decide"
        )
    return "\n".join(lines) + "\n"


def _check_lean(text: str, file: Path) -> subprocess.CompletedProcess[str]:
    file.write_text(text, encoding="utf-8")
    return _run(["lake", "env", "lean", str(file.resolve())], cwd=PROOF, timeout=240)


def require_rejected(result: subprocess.CompletedProcess[str]) -> None:
    """Missing Lean, failed imports or a crash are NOT successful negative tests."""
    diagnostics = result.stdout + result.stderr
    if result.returncode == 0 or "decide" not in diagnostics or "false" not in diagnostics:
        raise RuntimeError("Lean did not reject the changed semantic equation:\n" + diagnostics)


def check_observations(observations: list[Observation], directory: Path) -> dict[str, object]:
    positive = _check_lean(render_proof(observations), directory / "ObservedLiterals.lean")
    (directory / "lean-positive.log").write_text(positive.stdout + positive.stderr, encoding="utf-8")
    if positive.returncode:
        raise RuntimeError("Lean rejected actual compiler values:\n" + positive.stdout + positive.stderr)
    original = observations[0]
    changes = [
        replace(original, value=original.value + "!"),
        replace(original, case=replace(original.case, literal='"' + original.case.literal[1:-1] + '!"')),
    ]
    for index, changed in enumerate(changes):
        result = _check_lean(render_proof([changed]), directory / f"ChangedLiteral{index}.lean")
        (directory / f"lean-negative-{index}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        require_rejected(result)
    return {
        "kernel_checked_equations": len(observations),
        "changed_value_rejected": True,
        "changed_source_rejected": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "out" / "proof_semantics")
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "passed": False,
        "scope": "source-literal equations for real lexer/AST/parser and LLVM/C/JIT outputs",
        "whole_compiler_proved": False,
    }
    try:
        frontend = frontend_cases()
        runtime = native_cases()
        observations = collect_frontend(frontend) + collect_native(runtime, directory)
        expected = {channel: len(frontend) if channel in CHANNELS[:3] else len(runtime) for channel in CHANNELS}
        counts = {channel: sum(row.channel == channel for row in observations) for channel in CHANNELS}
        if counts != expected or not all(counts.values()):
            raise RuntimeError("proof observations do not cover the requested matrix")
        report.update(check_observations(observations, directory))
        revision = _run(["git", "rev-parse", "HEAD"], cwd=ROOT)
        if revision.returncode:
            raise RuntimeError("cannot bind proof observations to a source revision")
        fingerprints = {}
        for relative in (
            "source/lexer/string_escapes.py", "source/lexer/scan.py",
            "source/parser/ast_expr_nodes.py", "proof/AILangProof/StringLiterals.lean",
            "proof/check_literal_semantics.py",
        ):
            fingerprints[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        report.update(passed=True, commit=revision.stdout.strip(), observations=counts, sha256=fingerprints)
    except (OSError, ValueError, RuntimeError, SyntaxError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        print(f"Literal semantic proof failed: {error}", file=sys.stderr)
    (directory / "literal_semantics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

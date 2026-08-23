from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
AILANG = REPO_ROOT / "ailang.py"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.codegen import CodeGen  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from parser import ast as A  # noqa: E402
from parser.parser import Parser  # noqa: E402

LIVE_AT_EXIT_RE = re.compile(r"live at exit:\s*(\d+)\s*bytes", re.IGNORECASE)


def _parse(src: str) -> list[A.ASTNode]:
    return Parser(tokenize(src)).parse_program()


def test_inverse_from_import_sugar_parses_single_group() -> None:
    nodes = _parse("import x, y from core.tools\n")
    assert len(nodes) == 1
    assert isinstance(nodes[0], A.FromImport)
    assert nodes[0].module_path == "core.tools"
    assert nodes[0].names == ["x", "y"]


def test_inverse_from_import_sugar_parses_and_groups() -> None:
    nodes = _parse("import x, y from z and b, c from a\n")
    assert len(nodes) == 2
    assert isinstance(nodes[0], A.FromImport)
    assert nodes[0].module_path == "z"
    assert nodes[0].names == ["x", "y"]
    assert isinstance(nodes[1], A.FromImport)
    assert nodes[1].module_path == "a"
    assert nodes[1].names == ["b", "c"]


def test_multi_dealloc_generates_llvm_ir() -> None:
    src = """\
def main(): int
    a = "" + "one"
    b = "" + "two"
    dealloc(a, b)
    return 0
end
"""
    ir_text = CodeGen().generate(_parse(src), "<multi-dealloc>")
    assert ir_text.count('call void @"free"') >= 2


def test_multi_dealloc_c_backend_runs_clean(tmp_path: Path) -> None:
    src = tmp_path / "multi_dealloc.ail"
    out = tmp_path / "multi_dealloc"
    src.write_text(
        """\
def main(): int
    a = "" + "one"
    b = "" + "two"
    dealloc(a, b)
    return 0
end
""",
        encoding="utf-8",
    )
    compile_proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--backend=c", "-o", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr
    exe = out.with_suffix(".exe") if os.name == "nt" else out
    env = dict(os.environ)
    env["AILANG_LEAK_REPORT"] = "1"
    run_proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    output = run_proc.stdout + run_proc.stderr
    assert run_proc.returncode == 0, output
    match = LIVE_AT_EXIT_RE.search(output)
    assert match is not None, output
    assert int(match.group(1)) == 0, output


def test_multi_dealloc_borrowed_diagnostics_check_each_argument(tmp_path: Path) -> None:
    src = tmp_path / "borrowed_multi_dealloc.ail"
    src.write_text(
        """\
def main(): int
    a = "literal-a"
    b = "literal-b"
    dealloc(a, b)
    return 0
end
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check-json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    diagnostics = json.loads(proc.stdout)["diagnostics"]
    messages = [str(item["message"]) for item in diagnostics]
    assert any("dealloc(a) may free" in message for message in messages)
    assert any("dealloc(b) may free" in message for message in messages)

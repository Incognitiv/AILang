from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402


PROGRAM = """\
static int calls = 0

int effect():
    calls = calls + 1
    return 1
end

int main():
    int passed = 0

    if 1 == 1 or effect() == 1 then
        passed = passed + 1
    end
    if 0 == 1 or effect() == 1 then
        passed = passed + 1
    end
    if 0 == 1 and effect() == 1 then
        passed = passed + 1
    end
    if 1 == 1 and effect() == 1 then
        passed = passed + 1
    end

    if calls != 2 then
        return 10
    end
    if passed != 3 then
        return 11
    end
    return 0
end
"""


def test_effectful_rhs_is_generated_once_per_logical_expression() -> None:
    ir_text = compile_to_ir_fast(
        PROGRAM,
        source_file="llvm_short_circuit_effects.ail",
    )
    main_ir = ir_text.split('define i32 @"main"', 1)[1].split("\ndefine ", 1)[0]

    assert main_ir.count('call i64 @"effect"') == 4
    assert len(re.findall(r"^and_rhs(?:\.\d+)?:", main_ir, re.MULTILINE)) == 2
    assert len(re.findall(r"^or_rhs(?:\.\d+)?:", main_ir, re.MULTILINE)) == 2


def test_jit_short_circuits_effectful_rhs(tmp_path: Path) -> None:
    source = tmp_path / "llvm_short_circuit_effects.ail"
    source.write_text(PROGRAM, encoding="ascii")

    run = subprocess.run(
        [sys.executable, str(REPO_ROOT / "ailang.py"), str(source)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert run.returncode == 0, run.stdout + run.stderr

"""Native object emission must initialize the assembler before inline asm."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW_CLOSE_IR = '''
define i32 @main() {
entry:
  %result = call i64 asm sideeffect "syscall", "={rax},{rax},{rdi},~{rcx},~{r11},~{memory}"(i64 3, i64 -1)
  %ok = icmp eq i64 %result, -9
  %exit = select i1 %ok, i32 0, i32 1
  ret i32 %exit
}
'''


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or platform.machine().lower() not in {"x86_64", "amd64"}
    or not hasattr(os, "memfd_create")
    or not (shutil.which("clang") or shutil.which("gcc")),
    reason="the regression uses the Linux x86-64 syscall ABI",
)
def test_inline_asm_auto_build_returns_raw_errno(tmp_path: Path) -> None:
    # LLVM aborts, rather than raising a Python exception, without its parser.
    # Keep the negative failure mode isolated from the pytest process.
    output = tmp_path / "syscall-test"
    script = (
        "import resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
        f"sys.path.insert(0, {str(ROOT / 'source')!r})\n"
        "from pathlib import Path\n"
        "from compiler.memory_native import compile_ir_object\n"
        "from compiler.memory_link import link_object_in_memory, publish_executable\n"
        f"obj = compile_ir_object({RAW_CLOSE_IR!r})\n"
        "image = link_object_in_memory(obj)\n"
        f"publish_executable(image, Path({str(output)!r}))\n"
    )
    compiled = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(output)], check=False, timeout=10)
    assert result.returncode == 0

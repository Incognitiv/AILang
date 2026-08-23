from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_emit_llvm_warns_that_cinclude_is_c_backend_only(tmp_path: Path) -> None:
    src = tmp_path / "uses_cinclude.ail"
    out_ll = tmp_path / "uses_cinclude.ll"
    src.write_text(
        """\
#cinclude <stdint.h>

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-llvm", "-o", str(out_ll)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert out_ll.exists()
    assert "#cinclude is C-backend-only" in proc.stderr
    assert "LLVM IR emission cannot import C headers directly" in proc.stderr
    assert "<stdint.h>" in proc.stderr


def test_emit_llvm_ignores_inactive_targeted_cinclude(tmp_path: Path) -> None:
    src = tmp_path / "inactive_cinclude.ail"
    out_ll = tmp_path / "inactive_cinclude.ll"
    src.write_text(
        """\
#cinclude never "missing_inactive_header.h"

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-llvm", "-o", str(out_ll)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert out_ll.exists()
    assert "#cinclude is C-backend-only" not in proc.stderr


def test_c_backend_does_not_emit_inactive_targeted_cinclude(tmp_path: Path) -> None:
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        return

    src = tmp_path / "inactive_cinclude_c.ail"
    out_exe = tmp_path / "inactive_cinclude_c"
    src.write_text(
        """\
#cinclude never "missing_inactive_header.h"

def main(): int
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--backend=c", "-o", str(out_exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_ffi_report_text_explains_cinclude_backend_limit(tmp_path: Path) -> None:
    src = tmp_path / "ffi_cinclude.ail"
    src.write_text(
        """\
#cinclude "native_shim.h"

extern fn native_answer(): int

def main(): int
    return native_answer()
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--ffi-report"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "unsupported_cinclude_backends=llvm,jit" in proc.stdout
    assert "LLVM/JIT cannot import C headers directly" in proc.stdout
    assert '"native_shim.h"' in proc.stdout


def test_check_json_reports_missing_local_cinclude(tmp_path: Path) -> None:
    src = tmp_path / "missing_cinclude.ail"
    src.write_text(
        """\
#cinclude "missing_native_shim.h"

def main(): int
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
    payload = json.loads(proc.stdout)
    diagnostics = payload["diagnostics"]
    assert diagnostics
    assert diagnostics[0]["severity"] == "warning"
    assert diagnostics[0]["line"] == 1
    assert "#cinclude local header was not found" in diagnostics[0]["message"]
    assert "missing_native_shim.h" in diagnostics[0]["hint"]


def test_c_backend_resolves_local_cinclude_relative_to_source(tmp_path: Path) -> None:
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        return

    header = tmp_path / "native_answer.h"
    src = tmp_path / "local_cinclude.ail"
    out_exe = tmp_path / "local_cinclude"
    header.write_text("#define NATIVE_ANSWER 77\n", encoding="utf-8")
    src.write_text(
        """\
#cinclude "native_answer.h"

#template ansi_c
int64_t native_answer(void) {
    return NATIVE_ANSWER;
}
#end

extern fn native_answer(): int

def main(): int
    print(native_answer())
    return 0
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--backend=c", "-o", str(out_exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    exe = out_exe.with_suffix(".exe") if os.name == "nt" else out_exe
    run_proc = subprocess.run(
        [str(exe)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run_proc.returncode == 0, run_proc.stderr or run_proc.stdout
    assert run_proc.stdout.strip() == "77"

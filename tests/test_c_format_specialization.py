from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _generated_c_path_from_stdout(stdout: str) -> Path:
    match = re.search(r"Generated\s+(.+\.c)\s*$", stdout, re.MULTILINE)
    assert match is not None, f"Missing generated C path in output:\n{stdout}"
    return Path(match.group(1).strip())


def _compile_c(source: str, stem_name: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / f"{stem_name}.ail"
        exe_stem = tmp / stem_name
        src.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src),
                "--backend=c",
                "-o",
                str(exe_stem),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert (
            proc.returncode == 0
        ), f"C compile failed\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        c_file = _generated_c_path_from_stdout(proc.stdout)
        assert c_file.exists(), f"Expected generated C file: {c_file}"
        return c_file.read_text(encoding="utf-8")


def test_hex_and_oct_helpers_avoid_snprintf() -> None:
    source = """\
def main(): int
    print(hex(255))
    print(oct(255))
    return 0
end
"""
    c_text = _compile_c(source, "format_hex_oct")
    assert "static char *ailang_to_hex" in c_text
    assert "static char *ailang_to_oct" in c_text
    assert "snprintf(" not in c_text


def test_known_shape_print_uses_typed_writer_path() -> None:
    source = """\
def main(): int
    print("answer", 42)
    return 0
end
"""
    c_text = _compile_c(source, "format_print_direct")
    assert "ailang_write_i64(stdout" in c_text
    assert "ailang_write_str(stdout" in c_text
    assert 'printf("%s %lld' not in c_text


def test_interpolated_print_uses_literal_segment_writer_path() -> None:
    source = """\
def main(): int
    name = "AILang"
    print("Hello #{name} value=#{42}")
    return 0
end
"""
    c_text = _compile_c(source, "format_interp_direct")
    assert 'ailang_write_str(stdout, "Hello ");' in c_text
    assert 'ailang_write_str(stdout, " value=");' in c_text
    assert "ailang_write_i64(stdout, (int64_t)(42LL));" in c_text


def test_print_baseconv_uses_typed_noalloc_writers() -> None:
    source = """\
def main(): int
    print(hex(255))
    print(bin(10))
    print(oct(255))
    return 0
end
"""
    c_text = _compile_c(source, "format_baseconv_direct")
    assert "ailang_write_hex_u64(stdout, (uint64_t)(255LL));" in c_text
    assert "ailang_write_bin_u64(stdout, (uint64_t)(10LL));" in c_text
    assert "ailang_write_oct_u64(stdout, (uint64_t)(255LL));" in c_text
    assert "char *__pr_0 = (char *)ailang_to_hex(" not in c_text
    assert "char *__pr_0 = (char *)ailang_to_bin(" not in c_text
    assert "char *__pr_0 = (char *)ailang_to_oct(" not in c_text

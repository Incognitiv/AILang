from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "cbind_probe.py"
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser.parser import Parser  # noqa: E402

from lexer.scan import tokenize  # noqa: E402

SPEC = importlib.util.spec_from_file_location("cbind_probe_tool", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cbind_probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cbind_probe
SPEC.loader.exec_module(cbind_probe)


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def _write_probe_spec(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "name": "stdio_probe",
                "headers": ["<stdio.h>", "<time.h>"],
                "c_prelude": ["#define AIL_PROBE_PRELUDE 1"],
                "constants": [
                    "SEEK_SET",
                    {"name": "EOF", "expr": "EOF"},
                ],
                "records": [
                    {
                        "name": "TM",
                        "c_name": "struct tm",
                        "kind": "extern",
                        "fields": ["tm_sec", "tm_min"],
                    }
                ],
                "functions": [
                    {
                        "name": "puts",
                        "return_type": "int",
                        "params": [{"name": "text", "type": "ptr"}],
                    }
                ],
                "wrappers": [
                    {
                        "name": "ail_wrap_eof",
                        "return_type": "int",
                        "expr": "EOF",
                    },
                    {
                        "name": "ail_wrap_ptrptr_identity",
                        "return_type": "ptrptr",
                        "params": [{"name": "slot", "type": "ptrptr"}],
                        "expr": "slot",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_cbind_probe_cli_outputs_deterministic_json_and_ail(tmp_path: Path) -> None:
    if _available_c_compiler() is None:
        pytest.skip("no C compiler available")

    spec_path = tmp_path / "probe_spec.json"
    _write_probe_spec(spec_path)

    json_a = tmp_path / "probe_a.json"
    ail_a = tmp_path / "probe_a.ail"
    c_a = tmp_path / "probe_a.c"
    first = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            str(spec_path),
            "--json-out",
            str(json_a),
            "--ail-out",
            str(ail_a),
            "--c-out",
            str(c_a),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert first.returncode == 0, first.stderr or first.stdout

    json_b = tmp_path / "probe_b.json"
    ail_b = tmp_path / "probe_b.ail"
    c_b = tmp_path / "probe_b.c"
    second = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            str(spec_path),
            "--json-out",
            str(json_b),
            "--ail-out",
            str(ail_b),
            "--c-out",
            str(c_b),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert second.returncode == 0, second.stderr or second.stdout

    assert json_a.read_text(encoding="utf-8") == json_b.read_text(encoding="utf-8")
    assert ail_a.read_text(encoding="utf-8") == ail_b.read_text(encoding="utf-8")
    assert c_a.read_text(encoding="utf-8") == c_b.read_text(encoding="utf-8")

    payload = json.loads(json_a.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["spec_name"] == "stdio_probe"
    assert payload["constants"][0]["name"] == "EOF"
    assert payload["constants"][1]["name"] == "SEEK_SET"
    assert payload["records"][0]["name"] == "TM"
    assert payload["records"][0]["size"] > 0
    assert payload["records"][0]["align"] > 0
    assert payload["records"][0]["fields"][0]["name"] == "tm_min"
    assert payload["records"][0]["fields"][1]["name"] == "tm_sec"
    assert payload["functions"][0]["name"] == "puts"
    assert payload["wrappers"][0]["name"] == "ail_wrap_eof"
    assert payload["wrappers"][1]["name"] == "ail_wrap_ptrptr_identity"

    ail_text = ail_a.read_text(encoding="utf-8")
    assert "#cinclude <stdio.h>" in ail_text
    assert "#cinclude <time.h>" in ail_text
    assert "const int EOF =" in ail_text
    assert "const int SEEK_SET =" in ail_text
    assert 'extern record TM = "struct tm" layout size' in ail_text
    assert "tm_min offset" in ail_text
    assert "tm_sec offset" in ail_text
    assert "const int SIZEOF_TM =" in ail_text
    assert "const int ALIGNOF_TM =" in ail_text
    assert "const int OFFSETOF_TM_tm_min =" in ail_text
    assert "const int OFFSETOF_TM_tm_sec =" in ail_text
    assert "extern fn puts(text: ptr): int" in ail_text
    assert "extern fn ail_wrap_eof(): int" in ail_text
    assert "extern fn ail_wrap_ptrptr_identity(slot: ptrptr): ptrptr" in ail_text
    Parser(tokenize(ail_text)).parse_program()

    c_text = c_a.read_text(encoding="utf-8")
    assert "#define AIL_PROBE_PRELUDE 1" in c_text
    assert "typedef struct tm TM;" in c_text
    assert "_Static_assert(sizeof(struct tm) ==" in c_text
    assert "_Static_assert(_Alignof(struct tm) ==" in c_text
    assert "_Static_assert(offsetof(struct tm, tm_min) ==" in c_text
    assert "int64_t ail_wrap_eof(void)" in c_text
    assert "void ** ail_wrap_ptrptr_identity(void ** slot)" in c_text

    obj = tmp_path / "probe_a.o"
    compile_proc = subprocess.run(
        [_available_c_compiler(), "-std=gnu23", "-c", str(c_a), "-o", str(obj)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stderr or compile_proc.stdout


def test_cbind_probe_reports_missing_compiler() -> None:
    spec = cbind_probe.BindingSpec(
        name="no_cc",
        headers=[],
        link_flags=[],
        cflags=[],
        constants=[],
        macros=[],
        enums=[],
        records=[],
        functions=[],
        wrappers=[],
    )
    result = cbind_probe.probe_binding_spec(
        spec, compiler="__ailang_missing_compiler__"
    )
    assert result.ok is False
    assert result.errors
    assert "requested compiler not found" in result.errors[0]


def test_cbind_probe_emits_macros_enums_and_bitfield_metadata(
    tmp_path: Path,
) -> None:
    compiler = _available_c_compiler()
    if compiler is None:
        pytest.skip("no C compiler available")

    header = tmp_path / "probe_header.h"
    header.write_text(
        """\
#ifndef AILANG_PROBE_HEADER_H
#define AILANG_PROBE_HEADER_H

#define PROBE_MAGIC 123
#define PROBE_MASK (1u << 4)

typedef enum ProbeMode {
    PROBE_MODE_A = 7,
    PROBE_MODE_B = 9
} ProbeMode;

typedef struct ProbeBits {
    unsigned int enabled : 1;
    unsigned int mode : 3;
    unsigned int count;
} ProbeBits;

#endif
""",
        encoding="utf-8",
    )

    spec_path = tmp_path / "extended_probe.cbind.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "extended_probe",
                "headers": [{"path": "probe_header.h", "system": False}],
                "cflags": [f"-I{tmp_path.as_posix()}"],
                "macros": ["PROBE_MAGIC", {"name": "PROBE_MASK", "expr": "PROBE_MASK"}],
                "enums": [
                    {
                        "name": "ProbeMode",
                        "variants": ["PROBE_MODE_A", "PROBE_MODE_B"],
                    }
                ],
                "records": [
                    {
                        "name": "ProbeBits",
                        "c_name": "ProbeBits",
                        "kind": "extern",
                        "fields": [
                            {"name": "enabled", "type": "uint", "bit_width": 1},
                            {"name": "mode", "type": "uint", "bit_width": 3},
                            {"name": "count", "type": "uint"},
                        ],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    json_out = tmp_path / "extended.probe.json"
    ail_out = tmp_path / "extended.bindings.ail"
    c_out = tmp_path / "extended.bindings.c"
    proc = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            str(spec_path),
            "--json-out",
            str(json_out),
            "--ail-out",
            str(ail_out),
            "--c-out",
            str(c_out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["macros"][0]["name"] == "PROBE_MAGIC"
    assert payload["macros"][0]["value"] == 123
    assert payload["macros"][1]["name"] == "PROBE_MASK"
    assert payload["macros"][1]["value"] == 16
    assert payload["enums"][0]["name"] == "ProbeMode"
    assert payload["enums"][0]["variants"][0]["value"] == 7
    assert payload["enums"][0]["variants"][1]["value"] == 9
    fields = {row["name"]: row for row in payload["records"][0]["fields"]}
    assert fields["enabled"]["bit_width"] == 1
    assert fields["mode"]["bit_width"] == 3
    assert fields["count"]["size"] > 0

    ail_text = ail_out.read_text(encoding="utf-8")
    assert "const int PROBE_MAGIC = 123" in ail_text
    assert "enum ProbeMode then" in ail_text
    assert "PROBE_MODE_B = 9" in ail_text
    assert "enabled offset" in ail_text
    assert "bit_width 1" in ail_text
    assert "const int BITWIDTH_ProbeBits_enabled = 1" in ail_text

    Parser(tokenize(ail_text)).parse_program()

    obj = tmp_path / "extended.o"
    compile_proc = subprocess.run(
        [
            compiler,
            "-std=gnu23",
            f"-I{tmp_path.as_posix()}",
            "-c",
            str(c_out),
            "-o",
            str(obj),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stderr or compile_proc.stdout

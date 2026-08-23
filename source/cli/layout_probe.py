"""C ABI layout probes for FFI reporting."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cli.header_generation import generate_c_header

PROBE_TIMEOUT_SECONDS = 30


def _available_c_compiler() -> str | None:
    """Return a C compiler executable if one is available."""
    return shutil.which("gcc") or shutil.which("clang")


def _quote_c_string(text: str) -> str:
    """Return a C string literal body-safe escaped value."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _probe_source(
    header_name: str,
    records: list[dict[str, Any]],
    unions: list[dict[str, Any]],
) -> str:
    """Build the C source that prints type/field layout facts."""
    lines = [
        "#include <stddef.h>",
        "#include <stdio.h>",
        f'#include "{header_name}"',
        "",
        "int main(void) {",
    ]
    for kind, rows in (("record", records), ("union", unions)):
        for row in rows:
            type_name = str(row["c_name"])
            type_label = _quote_c_string(type_name)
            lines.append(
                f'    printf("TYPE|{kind}|{type_label}|%zu|%zu\\n", '
                f"sizeof({type_name}), _Alignof({type_name}));"
            )
            for field in row.get("fields", []):
                field_name = str(field["name"])
                field_label = _quote_c_string(field_name)
                lines.append(
                    f'    printf("FIELD|{type_label}|{field_label}|%zu|%zu\\n", '
                    f"offsetof({type_name}, {field_name}), "
                    f"sizeof(((({type_name} *)0)->{field_name})));"
                )
    lines.extend(["    return 0;", "}", ""])
    return "\n".join(lines)


def _empty_payload(status: str, compiler: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "compiler": compiler,
        "records": {},
        "unions": {},
        "errors": [],
    }


def _parse_probe_output(output: str) -> dict[str, Any]:
    payload = _empty_payload("ok")
    type_kinds: dict[str, str] = {}
    for raw_line in output.splitlines():
        parts = raw_line.strip().split("|")
        if not parts:
            continue
        tag = parts[0]
        if tag == "TYPE" and len(parts) == 5:
            _tag, kind, name, size_text, align_text = parts
            type_kinds[name] = kind
            bucket = payload["records"] if kind == "record" else payload["unions"]
            bucket[name] = {
                "size": int(size_text),
                "align": int(align_text),
                "fields": {},
            }
        elif tag == "FIELD" and len(parts) == 5:
            _tag, type_name, field_name, offset_text, size_text = parts
            kind = type_kinds.get(type_name, "record")
            bucket = payload["records"] if kind == "record" else payload["unions"]
            row = bucket.setdefault(type_name, {"size": 0, "align": 0, "fields": {}})
            row["fields"][field_name] = {
                "offset": int(offset_text),
                "size": int(size_text),
            }
    return payload


def probe_ffi_layout(
    source_file: str,
    records: list[dict[str, Any]],
    unions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile and run a tiny C ABI layout probe for reportable types."""
    if not records and not unions:
        return _empty_payload("skipped")

    compiler = _available_c_compiler()
    if compiler is None:
        return _empty_payload("no_c_compiler")

    with tempfile.TemporaryDirectory(prefix="ailang_layout_probe_") as td:
        work_dir = Path(td)
        header_path = work_dir / "ailang_ffi_probe.h"
        source_path = work_dir / "ailang_ffi_probe.c"
        exe_path = work_dir / (
            "ailang_ffi_probe.exe"
            if sys.platform.startswith("win")
            else "ailang_ffi_probe"
        )
        try:
            header_path.write_text(generate_c_header(source_file), encoding="utf-8")
            source_path.write_text(
                _probe_source(header_path.name, records, unions),
                encoding="utf-8",
            )
        except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
            payload = _empty_payload("source_failed", compiler)
            payload["errors"].append(str(exc))
            return payload

        compile_proc = subprocess.run(
            [compiler, "-std=gnu23", str(source_path), "-o", str(exe_path)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
        if compile_proc.returncode != 0:
            payload = _empty_payload("compile_failed", compiler)
            detail = compile_proc.stderr.strip() or compile_proc.stdout.strip()
            if detail:
                payload["errors"].append(detail.splitlines()[0])
            return payload

        run_proc = subprocess.run(
            [str(exe_path)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
        if run_proc.returncode != 0:
            payload = _empty_payload("run_failed", compiler)
            detail = run_proc.stderr.strip() or run_proc.stdout.strip()
            if detail:
                payload["errors"].append(detail.splitlines()[0])
            return payload

    payload = _parse_probe_output(run_proc.stdout)
    payload["compiler"] = compiler
    return payload

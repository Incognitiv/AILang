"""C header generation for exported AILang ABI surfaces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from abi_symbols import (
    c_symbol_for_function,
    explicit_c_abi_parts,
    has_export_decorator,
)
from callback_types import callback_parts, is_callback_type
from calling_conventions import c_callconv_macro, normalized_decorators
from pgo.paths import sanitize_stem
from target_info import os_from_platform, target_matches

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HEADER_ROOT = REPO_ROOT / "out" / "generated" / "headers"


def default_header_output_path(source_file: str) -> Path:
    """Return the default generated header path for a source file."""
    stem = sanitize_stem(Path(source_file).stem)
    HEADER_ROOT.mkdir(parents=True, exist_ok=True)
    return HEADER_ROOT / f"{stem}.h"


def _decorator_names(node: object) -> list[str]:
    raw = getattr(node, "decorators", []) or []
    return [str(item).lstrip("@").lower() for item in raw]


def _header_guard(source_file: str) -> str:
    stem = Path(source_file).stem
    clean = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_").upper()
    if not clean:
        clean = "AILANG_EXPORTS"
    if clean[:1].isdigit():
        clean = f"AILANG_{clean}"
    return f"{clean}_H"


def _parse_source(source_file: str):
    from parser.parser import Parser

    from lexer.scan import tokenize
    from transpiler.import_resolver import ImportResolver

    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()
    parser = Parser(tokenize(source))
    nodes = parser.parse_program()
    return ImportResolver().run(nodes, source_file)


def _configured_transpiler(nodes: list[Any]) -> Any:
    from parser import ast as A

    from transpiler.core import CTranspiler
    from transpiler.type_collector import TypeCollector

    transpiler = CTranspiler()
    TypeCollector().run(nodes, transpiler.type_info)
    transpiler._type_aliases = dict(transpiler.type_info.type_aliases)
    for node in nodes:
        if isinstance(node, A.Function):
            transpiler.user_defined_funcs.add(node.name)
    transpiler._function_c_symbols = {
        node.name: c_symbol_for_function(
            node.name,
            getattr(node, "decorators", []),
            transpiler._default_mangle_name,
        )
        for node in nodes
        if isinstance(node, A.Function)
    }
    return transpiler


def _emit_includes(nodes: list[object]) -> list[str]:
    from parser import ast as A

    lines = [
        "#include <stdint.h>",
        "#include <stdbool.h>",
        "#include <stddef.h>",
    ]
    seen: set[tuple[str, bool]] = set()
    current_os = os_from_platform()
    for node in nodes:
        if not isinstance(node, A.CInclude):
            continue
        if not target_matches(getattr(node, "target_os", None), current_os):
            continue
        key = (node.path, bool(node.is_system))
        if key in seen:
            continue
        seen.add(key)
        if node.is_system:
            lines.append(f"#include <{node.path}>")
        else:
            lines.append(f'#include "{node.path}"')
    return lines


def _emit_record(lines: list[str], transpiler: Any, node: Any) -> None:
    lines.append("typedef struct {")
    for field_name, field_type in getattr(node, "fields", []):
        decl = transpiler._format_c_declaration(field_type, field_name)
        lines.append(f"    {decl};")
    decos = _decorator_names(node)
    packed = " AILANG_PACKED" if "packed" in decos else ""
    lines.append(f"}}{packed} {node.name};")
    lines.append("")


def _emit_union(lines: list[str], transpiler: Any, node: Any) -> None:
    lines.append("typedef union {")
    for field_name, field_type in getattr(node, "fields", []):
        decl = transpiler._format_c_declaration(field_type, field_name)
        lines.append(f"    {decl};")
    decos = _decorator_names(node)
    packed = " AILANG_PACKED" if "packed" in decos else ""
    lines.append(f"}}{packed} {node.name};")
    lines.append("")


def _emit_callback_alias(
    lines: list[str], transpiler: Any, name: str, target: Any
) -> None:
    params, ret_type, decorators = callback_parts(target)
    ret_c = transpiler._ailang_type_to_c(_type_to_text(ret_type))
    callconv = c_callconv_macro(normalized_decorators(decorators))
    c_params = []
    for index, item in enumerate(params):
        raw_name, raw_type = item
        pname = raw_name if raw_name else f"arg{index}"
        c_params.append(
            transpiler._format_c_declaration(_type_to_text(raw_type), pname)
        )
    params_text = ", ".join(c_params) if c_params else "void"
    lines.append(f"typedef {ret_c} ({callconv}*{name})({params_text});")
    lines.append("")


def _emit_extern_record(lines: list[str], node: Any) -> None:
    c_name = str(getattr(node, "c_name", node.name))
    explicit = bool(getattr(node, "c_name_explicit", False))
    if c_name != node.name:
        lines.append(f"typedef {c_name} {node.name};")
    elif explicit:
        lines.append(f"/* extern record {node.name} uses existing C type */")
    else:
        lines.append(f"typedef struct {node.name} {node.name};")
    lines.append("")


def _type_to_text(type_spec: Any) -> str:
    from parser.ast import parsed_type_to_str

    return parsed_type_to_str(type_spec)


def _emit_exported_function(lines: list[str], transpiler: Any, node: Any) -> None:
    ret_type = transpiler._get_return_type(node)
    params = transpiler._format_params(getattr(node, "params", []))
    c_abi = explicit_c_abi_parts(getattr(node, "decorators", []))
    if c_abi is not None:
        ret_type, c_params = c_abi
        params = ", ".join(c_params) if c_params else "void"
    symbol_name = transpiler._mangle_name(node.name)
    callconv = c_callconv_macro(normalized_decorators(getattr(node, "decorators", [])))
    lines.append(f"{ret_type} {callconv}{symbol_name}({params});")


def generate_c_header(source_file: str) -> str:
    """Generate a C header for records, unions, and @export functions."""
    from parser import ast as A

    nodes = _parse_source(source_file)
    transpiler = _configured_transpiler(nodes)
    guard = _header_guard(source_file)
    lines = [
        "/* Generated by AILang. Do not edit by hand. */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        *_emit_includes(nodes),
        "",
        "#if defined(__GNUC__) || defined(__clang__)",
        "#define AILANG_PACKED __attribute__((packed))",
        "#else",
        "#define AILANG_PACKED",
        "#endif",
        "",
        "#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)",
        "#define AILANG_STDCALL __stdcall",
        "#define AILANG_FASTCALL __fastcall",
        "#else",
        "#define AILANG_STDCALL",
        "#define AILANG_FASTCALL",
        "#endif",
        "",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "",
        "typedef struct ailang_dyn_array {",
        "    int64_t *data;",
        "    int64_t length;",
        "    int64_t capacity;",
        "} ailang_dyn_array;",
        "typedef struct ailang_str_array {",
        "    const char **data;",
        "    int64_t length;",
        "    int64_t capacity;",
        "} ailang_str_array;",
        "",
    ]

    emitted_types: set[str] = set()
    for node in nodes:
        if isinstance(node, A.ExternRecordDef) and node.name not in emitted_types:
            _emit_extern_record(lines, node)
            emitted_types.add(node.name)

    emitted_callbacks: set[str] = set()
    for node in nodes:
        if (
            isinstance(node, A.TypeAlias)
            and node.name not in emitted_callbacks
            and is_callback_type(node.target_type)
        ):
            _emit_callback_alias(lines, transpiler, node.name, node.target_type)
            emitted_callbacks.add(node.name)

    for node in nodes:
        if isinstance(node, A.RecordDef) and node.name not in emitted_types:
            _emit_record(lines, transpiler, node)
            emitted_types.add(node.name)
        elif isinstance(node, A.UnionDef) and node.name not in emitted_types:
            _emit_union(lines, transpiler, node)
            emitted_types.add(node.name)

    exported = [
        node
        for node in nodes
        if isinstance(node, A.Function)
        and has_export_decorator(getattr(node, "decorators", []))
    ]
    if exported:
        lines.append("/* Exported functions */")
        for node in exported:
            _emit_exported_function(lines, transpiler, node)
        lines.append("")

    lines.extend(
        [
            "#ifdef __cplusplus",
            "}",
            "#endif",
            "",
            "#undef AILANG_PACKED",
            f"#endif /* {guard} */",
            "",
        ]
    )
    return "\n".join(lines)


_CABI_TYPE_ALIASES = {
    "void": "void",
    "bool": "bool",
    "char": "char",
    "short": "short",
    "ushort": "unsigned short",
    "int": "int",
    "uint": "unsigned int",
    "long": "long",
    "ulong": "unsigned long",
    "size_t": "size_t",
    "ssize_t": "ssize_t",
    "uintptr": "uintptr_t",
    "uintptr_t": "uintptr_t",
    "intptr": "intptr_t",
    "intptr_t": "intptr_t",
    "pid_t": "pid_t",
    "uid_t": "uid_t",
    "gid_t": "gid_t",
    "mode_t": "mode_t",
    "pointer": "void *",
    "ptr": "void *",
    "const_pointer": "const void *",
    "cptr": "const void *",
    "cstring": "const char *",
    "charptr": "char *",
    "char_pointer": "char *",
    "charpp": "char **",
    "cstringp": "const char **",
    "fileptr": "FILE *",
    "size_tp": "size_t *",
}


def _cabi_guard(path: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    clean = re.sub(r"[^0-9A-Za-z]+", "_", path).strip("_").upper()
    if not clean:
        clean = "AILANG_CABI_HEADER"
    if clean[:1].isdigit():
        clean = f"AILANG_{clean}"
    return f"{clean}_H"


def _cabi_type_to_c(type_name: str) -> str:
    stripped = type_name.strip()
    mapped = _CABI_TYPE_ALIASES.get(stripped.lower())
    return mapped if mapped is not None else stripped


def _cabi_value_to_c(value: str) -> str:
    """Return a readable C preprocessor expression for a cabi define."""
    text = value.strip()
    # Parser token text is intentionally conservative and joins tokens with
    # spaces. Collapse only obvious unary signs; do not rewrite binary math.
    text = re.sub(r"(^|[(\[,])\s*-\s+([0-9])", r"\1-\2", text)
    text = re.sub(r"(^|[(\[,])\s*\+\s+([0-9])", r"\1+\2", text)
    text = re.sub(r"\b(0[xX][0-9A-Fa-f]+|[0-9]+)\s+([uUlL]+)\b", r"\1\2", text)
    return text


def _cabi_params_to_c(params: list[tuple[str, str]], variadic: bool = False) -> str:
    c_params = [
        f"{_cabi_type_to_c(param_type)} {param_name}"
        for param_name, param_type in params
    ]
    if variadic:
        c_params.append("...")
    return ", ".join(c_params) if c_params else ("..." if variadic else "void")


def _emit_cabi_macro(lines: list[str], node: Any) -> None:
    params = ", ".join(getattr(node, "params", []))
    body_lines = [line.strip() for line in str(getattr(node, "body", "")).splitlines()]
    body_lines = [line for line in body_lines if line.strip()]
    if not body_lines:
        lines.append(f"#define {node.name}({params})")
        return
    escaped = " \\\n    ".join(body_lines)
    lines.append(f"#define {node.name}({params}) {escaped}")


def _emit_cabi_inline(lines: list[str], node: Any) -> None:
    ret = _cabi_type_to_c(node.return_type)
    params_text = _cabi_params_to_c(
        list(getattr(node, "params", [])),
        bool(getattr(node, "variadic", False)),
    )
    lines.append(f"static inline {ret}")
    lines.append(f"{node.name}({params_text})")
    lines.append("{")
    body_lines = [line.rstrip() for line in str(getattr(node, "body", "")).splitlines()]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    for line in body_lines:
        if line.strip():
            lines.append(f"    {line.lstrip()}")
        else:
            lines.append("")
    lines.append("}")


def _emit_cabi_entries(
    lines: list[str],
    entries: list[Any],
    seen_includes: set[tuple[str, bool, bool]],
) -> None:
    from parser import ast as A

    for entry in entries:
        if isinstance(entry, A.CAbiDefine):
            lines.append(f"#define {entry.name} {_cabi_value_to_c(str(entry.value))}")
        elif isinstance(entry, A.CAbiInclude):
            key = (
                str(entry.path),
                bool(entry.is_system),
                bool(getattr(entry, "include_next", False)),
            )
            if key in seen_includes:
                continue
            seen_includes.add(key)
            directive = (
                "#include_next" if getattr(entry, "include_next", False) else "#include"
            )
            if entry.is_system:
                lines.append(f"{directive} <{entry.path}>")
            else:
                lines.append(f'{directive} "{entry.path}"')
        elif isinstance(entry, A.CAbiTypedef):
            lines.append(f"typedef {_cabi_type_to_c(entry.c_type)} {entry.name};")
        elif isinstance(entry, A.CAbiStruct):
            lines.append(f"struct {entry.name} {{")
            for field in entry.fields:
                c_type = _cabi_type_to_c(field.c_type)
                lines.append(f"    {c_type} {field.name};")
            lines.append("};")
        elif isinstance(entry, A.CAbiPrototype):
            ret = _cabi_type_to_c(entry.return_type)
            params_text = _cabi_params_to_c(
                list(entry.params), bool(getattr(entry, "variadic", False))
            )
            lines.append(f"{ret} {entry.name}({params_text});")
        elif isinstance(entry, A.CAbiInlineFunction):
            _emit_cabi_inline(lines, entry)
        elif isinstance(entry, A.CAbiConditional):
            directive = str(entry.directive)
            expression = str(entry.expression)
            if directive == "ifdef":
                lines.append(f"#ifdef {expression}")
            elif directive == "ifndef":
                lines.append(f"#ifndef {expression}")
            elif directive == "if":
                lines.append(f"#if {expression}")
            else:
                raise ValueError(f"unknown abi conditional directive: {directive}")
            _emit_cabi_entries(lines, list(entry.entries), seen_includes)
            if getattr(entry, "else_entries", []):
                lines.append("#else")
                _emit_cabi_entries(lines, list(entry.else_entries), seen_includes)
            lines.append("#endif")
        elif isinstance(entry, A.CAbiMacro):
            _emit_cabi_macro(lines, entry)
        lines.append("")


def _emit_cabi_header_block(node: Any) -> str:
    guard = _cabi_guard(str(node.path), getattr(node, "guard", None))
    lines = [
        "/* Generated by AILang ABI header. Do not edit by hand. */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        "#include <stddef.h>",
        "#include <stdbool.h>",
        "#include <sys/types.h>",
        "",
    ]
    seen_includes: set[tuple[str, bool, bool]] = {
        ("stdint.h", True, False),
        ("stddef.h", True, False),
        ("stdbool.h", True, False),
        ("sys/types.h", True, False),
    }
    _emit_cabi_entries(lines, list(getattr(node, "entries", [])), seen_includes)
    lines.extend([f"#endif /* {guard} */", ""])
    return "\n".join(lines)


def generate_cabi_headers(source_file: str) -> dict[str, str]:
    """Generate C ABI headers declared by ``abi header`` blocks."""
    from parser import ast as A

    nodes = _parse_source(source_file)
    headers: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, A.CAbiHeader):
            headers[str(node.path)] = _emit_cabi_header_block(node)
    return headers


def write_cabi_headers(source_file: str, output_dir: str) -> bool:
    """Generate all ``abi header`` blocks into an output include directory."""
    try:
        headers = generate_cabi_headers(source_file)
        if not headers:
            print("Error: no abi header blocks found")
            return False
        root = Path(output_dir)
        for rel_path, header in headers.items():
            output_path = root / rel_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(header, encoding="utf-8")
    except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
        print(f"Error: failed to generate ABI headers: {exc}")
        return False
    print(f"ABI headers written to: {output_dir}")
    return True


def write_c_header(source_file: str, output_header: str) -> bool:
    """Generate and write a C header file."""
    try:
        header = generate_c_header(source_file)
        output_path = Path(output_header)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(header, encoding="utf-8")
    except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
        print(f"Error: failed to generate C header: {exc}")
        return False
    print(f"C header written to: {output_header}")
    return True

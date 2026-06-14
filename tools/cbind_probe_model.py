#!/usr/bin/env python3
"""Probe selected C ABI/constants from a small binding spec and emit AILang bindings."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROBE_TIMEOUT_SECONDS = 30

def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")
def _normalize_identifier(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    if not clean:
        return "_"
    if clean[0].isdigit():
        return f"_{clean}"
    return clean
def _quote_c_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
def _flatten_flags(flags: list[str]) -> list[str]:
    out: list[str] = []
    for raw in flags:
        pieces = str(raw).split()
        out.extend(piece for piece in pieces if piece)
    return out
@dataclass
class HeaderSpec:
    path: str
    system: bool = True
@dataclass
class ConstantSpec:
    name: str
    expr: str
@dataclass
class MacroSpec:
    name: str
    expr: str
    kind: str = "constant"
@dataclass
class EnumVariantSpec:
    name: str
    expr: str
@dataclass
class EnumSpec:
    name: str
    variants: list[EnumVariantSpec]
@dataclass
class RecordFieldSpec:
    name: str
    c_name: str
    type: str
    bit_width: int | None = None
@dataclass
class RecordSpec:
    name: str
    c_name: str
    kind: str
    fields: list[RecordFieldSpec]
@dataclass
class ParamSpec:
    name: str
    type: str
@dataclass
class WrapperParamSpec:
    name: str
    type: str
    abi_type: str
@dataclass
class FunctionSpec:
    name: str
    return_type: str
    params: list[ParamSpec]
    variadic: bool
    decorators: list[str]
@dataclass
class WrapperSpec:
    name: str
    return_type: str
    abi_return_type: str
    params: list[WrapperParamSpec]
    expr: str | None
    body: str | None
    decorators: list[str]
@dataclass
class BindingSpec:
    name: str
    headers: list[HeaderSpec]
    link_flags: list[str]
    cflags: list[str]
    constants: list[ConstantSpec]
    macros: list[MacroSpec]
    enums: list[EnumSpec]
    records: list[RecordSpec]
    functions: list[FunctionSpec]
    wrappers: list[WrapperSpec]
    c_prelude: list[str] = field(default_factory=list)
    base_dir: str | None = None
@dataclass
class ProbeResult:
    ok: bool
    spec_name: str
    compiler: str | None
    headers: list[dict[str, Any]]
    link_flags: list[str]
    constants: list[dict[str, Any]]
    macros: list[dict[str, Any]]
    enums: list[dict[str, Any]]
    records: list[dict[str, Any]]
    functions: list[dict[str, Any]]
    wrappers: list[dict[str, Any]]
    errors: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)
def _parse_header(raw: Any) -> HeaderSpec:
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("<") and text.endswith(">"):
            return HeaderSpec(path=text[1:-1].strip(), system=True)
        if text.startswith('"') and text.endswith('"'):
            return HeaderSpec(path=text[1:-1].strip(), system=False)
        return HeaderSpec(path=text, system=True)
    if isinstance(raw, dict):
        path = str(raw.get("path", "")).strip()
        if not path:
            raise ValueError("header.path cannot be empty")
        system = bool(raw.get("system", True))
        return HeaderSpec(path=path, system=system)
    raise ValueError(f"invalid header entry: {raw!r}")
def _parse_constant(raw: Any) -> ConstantSpec:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            raise ValueError("constant name cannot be empty")
        return ConstantSpec(name=name, expr=name)
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("constant name cannot be empty")
        expr = str(raw.get("expr", name)).strip()
        if not expr:
            raise ValueError(f"constant expr cannot be empty for {name!r}")
        return ConstantSpec(name=name, expr=expr)
    raise ValueError(f"invalid constant entry: {raw!r}")
def _parse_macro(raw: Any) -> MacroSpec:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            raise ValueError("macro name cannot be empty")
        return MacroSpec(name=name, expr=name)
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("macro name cannot be empty")
        expr = str(raw.get("expr", name)).strip()
        if not expr:
            raise ValueError(f"macro expr cannot be empty for {name!r}")
        kind = str(raw.get("kind", "constant")).strip().lower() or "constant"
        return MacroSpec(name=name, expr=expr, kind=kind)
    raise ValueError(f"invalid macro entry: {raw!r}")
def _parse_enum_variant(raw: Any, enum_name: str) -> EnumVariantSpec:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            raise ValueError(f"enum variant cannot be empty for {enum_name!r}")
        return EnumVariantSpec(name=name, expr=name)
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"enum variant cannot be empty for {enum_name!r}")
        expr = str(raw.get("expr", name)).strip()
        if not expr:
            raise ValueError(f"enum variant expr cannot be empty for {name!r}")
        return EnumVariantSpec(name=name, expr=expr)
    raise ValueError(f"invalid enum variant for {enum_name!r}: {raw!r}")
def _parse_enum(raw: Any) -> EnumSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"invalid enum entry: {raw!r}")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("enum name cannot be empty")
    variants_raw = raw.get("variants", [])
    if isinstance(variants_raw, dict):
        variants_iter = [
            {"name": key, "expr": value}
            for key, value in sorted(variants_raw.items(), key=lambda item: str(item[0]))
        ]
    elif isinstance(variants_raw, list):
        variants_iter = variants_raw
    else:
        raise ValueError(f"enum variants must be a list or object for {name!r}")
    variants = sorted(
        (_parse_enum_variant(item, name) for item in variants_iter),
        key=lambda x: x.name,
    )
    return EnumSpec(name=name, variants=variants)
def _parse_record_field(raw: Any, record_name: str) -> RecordFieldSpec:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            raise ValueError(f"record field cannot be empty for {record_name!r}")
        return RecordFieldSpec(name=name, c_name=name, type="int")
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"record field cannot be empty for {record_name!r}")
        c_name = str(raw.get("c_name", name)).strip() or name
        field_type = str(raw.get("type", "int")).strip() or "int"
        bit_width_raw = raw.get("bit_width")
        bit_width = None
        if bit_width_raw is not None:
            bit_width = int(bit_width_raw)
            if bit_width < 0:
                raise ValueError(
                    f"record bitfield width cannot be negative for {record_name!r}"
                )
        return RecordFieldSpec(
            name=name,
            c_name=c_name,
            type=field_type,
            bit_width=bit_width,
        )
    raise ValueError(f"invalid record field for {record_name!r}: {raw!r}")
def _parse_record(raw: Any) -> RecordSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"invalid record entry: {raw!r}")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("record name cannot be empty")
    c_name = str(raw.get("c_name", name)).strip()
    if not c_name:
        raise ValueError(f"record c_name cannot be empty for {name!r}")
    kind = str(raw.get("kind", "extern")).strip().lower()
    if kind not in ("extern", "opaque"):
        raise ValueError(f"record kind must be 'extern' or 'opaque' for {name!r}")
    fields_raw = raw.get("fields", [])
    if not isinstance(fields_raw, list):
        raise ValueError(f"record fields must be a list for {name!r}")
    fields = sorted(
        (_parse_record_field(field, name) for field in fields_raw),
        key=lambda x: x.name,
    )
    return RecordSpec(name=name, c_name=c_name, kind=kind, fields=fields)
def _parse_function(raw: Any) -> FunctionSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"invalid function entry: {raw!r}")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("function name cannot be empty")
    return_type = str(raw.get("return_type", "int")).strip()
    params_raw = raw.get("params", [])
    if not isinstance(params_raw, list):
        raise ValueError(f"function params must be a list for {name!r}")
    params: list[ParamSpec] = []
    for index, item in enumerate(params_raw):
        if not isinstance(item, dict):
            raise ValueError(f"function param must be object for {name!r}: {item!r}")
        pname = str(item.get("name", f"arg{index}")).strip() or f"arg{index}"
        ptype = str(item.get("type", "int")).strip() or "int"
        params.append(ParamSpec(name=pname, type=ptype))
    variadic = bool(raw.get("variadic", False))
    decorators = sorted(
        str(item).lstrip("@").strip().lower()
        for item in raw.get("decorators", [])
        if str(item).strip()
    )
    return FunctionSpec(
        name=name,
        return_type=return_type,
        params=params,
        variadic=variadic,
        decorators=decorators,
    )
def _abi_type_for_ail_type(type_name: str) -> str:
    lowered = type_name.strip().lower()
    mapping = {
        "int": "int64_t",
        "i64": "int64_t",
        "long": "int64_t",
        "uint": "uint64_t",
        "u64": "uint64_t",
        "ulong": "uint64_t",
        "byte": "uint8_t",
        "u8": "uint8_t",
        "small": "int16_t",
        "i16": "int16_t",
        "usmall": "uint16_t",
        "u16": "uint16_t",
        "short": "int32_t",
        "i32": "int32_t",
        "ushort": "uint32_t",
        "u32": "uint32_t",
        "tiny": "int8_t",
        "i8": "int8_t",
        "bool": "bool",
        "ptr": "void *",
        "ptrptr": "void **",
        "string": "const char *",
        "void": "void",
    }
    return mapping.get(lowered, "int64_t")
def _parse_wrapper(raw: Any) -> WrapperSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"invalid wrapper entry: {raw!r}")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("wrapper name cannot be empty")
    return_type = str(raw.get("return_type", "int")).strip() or "int"
    abi_return_type = str(
        raw.get("abi_return_type", _abi_type_for_ail_type(return_type))
    ).strip()
    if not abi_return_type:
        raise ValueError(f"wrapper abi_return_type cannot be empty for {name!r}")
    params_raw = raw.get("params", [])
    if not isinstance(params_raw, list):
        raise ValueError(f"wrapper params must be a list for {name!r}")
    params: list[WrapperParamSpec] = []
    for index, item in enumerate(params_raw):
        if not isinstance(item, dict):
            raise ValueError(f"wrapper param must be object for {name!r}: {item!r}")
        pname = str(item.get("name", f"arg{index}")).strip() or f"arg{index}"
        ptype = str(item.get("type", "int")).strip() or "int"
        abi_type = str(item.get("abi_type", _abi_type_for_ail_type(ptype))).strip()
        if not abi_type:
            raise ValueError(f"wrapper param abi_type cannot be empty for {name!r}")
        params.append(WrapperParamSpec(name=pname, type=ptype, abi_type=abi_type))

    expr_raw = raw.get("expr")
    body_raw = raw.get("body")
    expr = str(expr_raw).strip() if expr_raw is not None else None
    if isinstance(body_raw, list):
        body = "\n".join(str(line) for line in body_raw)
    elif body_raw is not None:
        body = str(body_raw)
    else:
        body = None
    if not expr and not body:
        raise ValueError(f"wrapper {name!r} requires expr or body")
    if expr and body:
        raise ValueError(f"wrapper {name!r} cannot define both expr and body")
    decorators = sorted(
        str(item).lstrip("@").strip().lower()
        for item in raw.get("decorators", [])
        if str(item).strip()
    )
    return WrapperSpec(
        name=name,
        return_type=return_type,
        abi_return_type=abi_return_type,
        params=params,
        expr=expr,
        body=body,
        decorators=decorators,
    )
def load_binding_spec(path: str | Path) -> BindingSpec:
    spec_path = Path(path).resolve()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("binding spec must be a JSON object")

    name = str(payload.get("name", spec_path.stem)).strip() or spec_path.stem
    headers_raw = payload.get("headers", [])
    constants_raw = payload.get("constants", [])
    macros_raw = payload.get("macros", [])
    enums_raw = payload.get("enums", [])
    records_raw = payload.get("records", [])
    functions_raw = payload.get("functions", [])
    wrappers_raw = payload.get("wrappers", [])
    c_prelude_raw = payload.get("c_prelude", [])
    link_flags_raw = payload.get("link_flags", [])
    cflags_raw = payload.get("cflags", [])

    if not isinstance(headers_raw, list):
        raise ValueError("headers must be a list")
    if not isinstance(constants_raw, list):
        raise ValueError("constants must be a list")
    if not isinstance(macros_raw, list):
        raise ValueError("macros must be a list")
    if not isinstance(enums_raw, list):
        raise ValueError("enums must be a list")
    if not isinstance(records_raw, list):
        raise ValueError("records must be a list")
    if not isinstance(functions_raw, list):
        raise ValueError("functions must be a list")
    if not isinstance(wrappers_raw, list):
        raise ValueError("wrappers must be a list")
    if not isinstance(c_prelude_raw, (list, str)):
        raise ValueError("c_prelude must be a list or string")
    if not isinstance(link_flags_raw, list):
        raise ValueError("link_flags must be a list")
    if not isinstance(cflags_raw, list):
        raise ValueError("cflags must be a list")

    headers = [_parse_header(item) for item in headers_raw]
    constants = sorted(
        (_parse_constant(item) for item in constants_raw), key=lambda x: x.name
    )
    macros = sorted((_parse_macro(item) for item in macros_raw), key=lambda x: x.name)
    enums = sorted((_parse_enum(item) for item in enums_raw), key=lambda x: x.name)
    records = sorted(
        (_parse_record(item) for item in records_raw), key=lambda x: x.name
    )
    functions = sorted(
        (_parse_function(item) for item in functions_raw), key=lambda x: x.name
    )
    wrappers = sorted(
        (_parse_wrapper(item) for item in wrappers_raw), key=lambda x: x.name
    )
    if isinstance(c_prelude_raw, str):
        c_prelude = [c_prelude_raw]
    else:
        c_prelude = [str(item) for item in c_prelude_raw if str(item).strip()]
    link_flags = [str(item).strip() for item in link_flags_raw if str(item).strip()]
    cflags = [str(item).strip() for item in cflags_raw if str(item).strip()]

    return BindingSpec(
        name=name,
        headers=headers,
        link_flags=link_flags,
        cflags=cflags,
        constants=constants,
        macros=macros,
        enums=enums,
        records=records,
        functions=functions,
        wrappers=wrappers,
        c_prelude=c_prelude,
        base_dir=str(spec_path.parent),
    )

__all__ = [name for name in globals() if not name.startswith("__")]

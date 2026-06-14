from __future__ import annotations

try:
    from .cbind_probe_model import *
except ImportError:
    from cbind_probe_model import *

def _resolve_spec_relative_path(spec: BindingSpec, path_text: str) -> str:
    """Resolve existing local compiler paths relative to the binding spec."""
    path = Path(path_text)
    if path.is_absolute() or spec.base_dir is None:
        return path_text
    candidate = Path(spec.base_dir) / path
    if candidate.exists():
        return str(candidate)
    return path_text
def _compile_cflags(spec: BindingSpec) -> list[str]:
    """Return compiler flags with source-relative include paths resolved."""
    raw_flags = _flatten_flags(spec.cflags)
    out: list[str] = []
    if spec.base_dir:
        out.extend(["-I", str(Path(spec.base_dir))])
    index = 0
    while index < len(raw_flags):
        flag = raw_flags[index]
        if flag == "-I" and index + 1 < len(raw_flags):
            out.extend(["-I", _resolve_spec_relative_path(spec, raw_flags[index + 1])])
            index += 2
            continue
        if flag.startswith("-I") and len(flag) > 2:
            out.append("-I" + _resolve_spec_relative_path(spec, flag[2:]))
            index += 1
            continue
        if flag == "-include" and index + 1 < len(raw_flags):
            out.extend(
                ["-include", _resolve_spec_relative_path(spec, raw_flags[index + 1])]
            )
            index += 2
            continue
        out.append(flag)
        index += 1
    return out
def _render_c_probe_source(spec: BindingSpec) -> str:
    lines = [
        "#include <stddef.h>",
        "#include <stdio.h>",
        "",
    ]
    for header in spec.headers:
        if header.system:
            lines.append(f"#include <{header.path}>")
        else:
            lines.append(f'#include "{header.path}"')
    lines.extend(["", "int main(void) {"])

    for const in spec.constants:
        cname = _quote_c_string(const.name)
        lines.append(f'    printf("CONST|{cname}|%lld\\n", (long long)({const.expr}));')

    for macro in spec.macros:
        if macro.kind != "constant":
            continue
        mname = _quote_c_string(macro.name)
        lines.append(
            f'    printf("MACRO|{mname}|%lld\\n", (long long)({macro.expr}));'
        )

    for enum in spec.enums:
        ename = _quote_c_string(enum.name)
        for variant in enum.variants:
            vname = _quote_c_string(variant.name)
            lines.append(
                f'    printf("ENUM|{ename}|{vname}|%lld\\n", '
                f"(long long)({variant.expr}));"
            )

    for record in spec.records:
        rname = _quote_c_string(record.name)
        lines.append(
            f'    printf("RECORD|{rname}|%zu|%zu\\n", '
            f"sizeof({record.c_name}), _Alignof({record.c_name}));"
        )
        for field in sorted(record.fields, key=lambda row: row.name):
            fname = _quote_c_string(field.name)
            if field.bit_width is not None:
                width = int(field.bit_width)
                lines.extend(
                    [
                        "    {",
                        f"        {record.c_name} obj = {{0}};",
                        f"        obj.{field.c_name} = 1;",
                        "        unsigned char *bytes = (unsigned char *)&obj;",
                        "        size_t bit_index = (size_t)-1;",
                        f"        for (size_t i = 0; i < sizeof({record.c_name}) * 8u; i++) {{",
                        "            if ((bytes[i / 8u] & (unsigned char)(1u << (i % 8u))) != 0u) {",
                        "                bit_index = i;",
                        "                break;",
                        "            }",
                        "        }",
                        f'        printf("BITFIELD|{rname}|{fname}|{width}|%zu\\n", bit_index);',
                        "    }",
                    ]
                )
                continue
            lines.append(
                f'    printf("FIELD|{rname}|{fname}|%zu|%zu\\n", '
                f"offsetof({record.c_name}, {field.c_name}), "
                f"sizeof((({record.c_name}*)0)->{field.c_name}));"
            )

    lines.extend(["    return 0;", "}", ""])
    return "\n".join(lines)
def _parse_probe_stdout(
    spec: BindingSpec, output: str
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    constants: dict[str, int] = {}
    macros: dict[str, int] = {}
    enums: dict[str, dict[str, int]] = {}
    record_map: dict[str, dict[str, Any]] = {
        row.name: {
            "name": row.name,
            "c_name": row.c_name,
            "kind": row.kind,
            "size": 0,
            "align": 0,
            "fields": {},
        }
        for row in spec.records
    }
    for raw_line in output.splitlines():
        parts = raw_line.strip().split("|")
        if not parts:
            continue
        tag = parts[0]
        if tag == "CONST" and len(parts) == 3:
            _tag, name, value_text = parts
            constants[name] = int(value_text, 10)
            continue
        if tag == "MACRO" and len(parts) == 3:
            _tag, name, value_text = parts
            macros[name] = int(value_text, 10)
            continue
        if tag == "ENUM" and len(parts) == 4:
            _tag, enum_name, variant_name, value_text = parts
            enums.setdefault(enum_name, {})[variant_name] = int(value_text, 10)
            continue
        if tag == "RECORD" and len(parts) == 4:
            _tag, name, size_text, align_text = parts
            row = record_map.setdefault(
                name,
                {
                    "name": name,
                    "c_name": name,
                    "kind": "extern",
                    "size": 0,
                    "align": 0,
                    "fields": {},
                },
            )
            row["size"] = int(size_text, 10)
            row["align"] = int(align_text, 10)
            continue
        if tag == "FIELD" and len(parts) == 5:
            _tag, rec_name, field_name, offset_text, size_text = parts
            row = record_map.setdefault(
                rec_name,
                {
                    "name": rec_name,
                    "c_name": rec_name,
                    "kind": "extern",
                    "size": 0,
                    "align": 0,
                    "fields": {},
                },
            )
            row["fields"][field_name] = {
                "name": field_name,
                "offset": int(offset_text, 10),
                "size": int(size_text, 10),
            }
            continue
        if tag == "BITFIELD" and len(parts) == 5:
            _tag, rec_name, field_name, width_text, bit_offset_text = parts
            row = record_map.setdefault(
                rec_name,
                {
                    "name": rec_name,
                    "c_name": rec_name,
                    "kind": "extern",
                    "size": 0,
                    "align": 0,
                    "fields": {},
                },
            )
            bit_offset = int(bit_offset_text, 10)
            row["fields"][field_name] = {
                "name": field_name,
                "offset": 0 if bit_offset < 0 else bit_offset // 8,
                "size": 0,
                "bit_offset": bit_offset,
                "bit_width": int(width_text, 10),
            }
            continue

    constant_rows = [
        {"name": row.name, "expr": row.expr, "value": int(constants.get(row.name, 0))}
        for row in spec.constants
    ]
    macro_rows = [
        {
            "name": row.name,
            "expr": row.expr,
            "kind": row.kind,
            "value": int(macros.get(row.name, 0)) if row.kind == "constant" else 0,
        }
        for row in spec.macros
    ]
    enum_rows = []
    for row in spec.enums:
        values = enums.get(row.name, {})
        enum_rows.append(
            {
                "name": row.name,
                "variants": [
                    {
                        "name": variant.name,
                        "expr": variant.expr,
                        "value": int(values.get(variant.name, 0)),
                    }
                    for variant in row.variants
                ],
            }
        )
    record_rows = []
    for row in spec.records:
        parsed = record_map.get(
            row.name,
            {
                "name": row.name,
                "c_name": row.c_name,
                "kind": row.kind,
                "size": 0,
                "align": 0,
                "fields": {},
            },
        )
        fields = []
        field_specs = {field.name: field for field in row.fields}
        for name in sorted(parsed.get("fields", {}).keys()):
            field_row = dict(parsed["fields"][name])
            field_spec = field_specs.get(name)
            if field_spec is not None:
                field_row["c_name"] = field_spec.c_name
                field_row["type"] = field_spec.type
                if field_spec.bit_width is not None:
                    field_row["bit_width"] = int(field_spec.bit_width)
            fields.append(field_row)
        record_rows.append(
            {
                "name": row.name,
                "c_name": row.c_name,
                "kind": row.kind,
                "size": int(parsed.get("size", 0)),
                "align": int(parsed.get("align", 0)),
                "fields": fields,
            }
        )
    return constant_rows, macro_rows, enum_rows, record_rows
def _empty_result(
    spec: BindingSpec, *, compiler: str | None, errors: list[str]
) -> ProbeResult:
    return ProbeResult(
        ok=False,
        spec_name=spec.name,
        compiler=compiler,
        headers=[asdict(row) for row in spec.headers],
        link_flags=list(spec.link_flags),
        constants=[],
        macros=[],
        enums=[],
        records=[],
        functions=[
            {
                "name": row.name,
                "return_type": row.return_type,
                "params": [asdict(p) for p in row.params],
                "variadic": row.variadic,
                "decorators": row.decorators,
            }
            for row in spec.functions
        ],
        wrappers=[
            {
                "name": row.name,
                "return_type": row.return_type,
                "abi_return_type": row.abi_return_type,
                "params": [asdict(p) for p in row.params],
                "decorators": row.decorators,
                "body_kind": "expr" if row.expr is not None else "body",
            }
            for row in spec.wrappers
        ],
        errors=errors,
    )
def probe_binding_spec(
    spec: BindingSpec,
    *,
    compiler: str | None = None,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    work_dir: str | Path | None = None,
) -> ProbeResult:
    selected_compiler = compiler or _available_c_compiler()
    if not selected_compiler:
        return _empty_result(spec, compiler=None, errors=["no C compiler available"])
    if shutil.which(selected_compiler) is None:
        return _empty_result(
            spec,
            compiler=selected_compiler,
            errors=[f"requested compiler not found: {selected_compiler}"],
        )

    temp_ctx = None
    if work_dir is None:
        temp_ctx = tempfile.TemporaryDirectory(prefix="ailang_cbind_probe_")
        root = Path(temp_ctx.name)
    else:
        root = Path(work_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)

    source_path = root / "cbind_probe.c"
    exe_path = root / (
        "cbind_probe.exe" if sys.platform.startswith("win") else "cbind_probe"
    )

    try:
        source_path.write_text(_render_c_probe_source(spec), encoding="utf-8")
        cmd = [
            selected_compiler,
            "-std=gnu23",
            str(source_path),
            "-o",
            str(exe_path),
            *_compile_cflags(spec),
            *_flatten_flags(spec.link_flags),
        ]
        compile_proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if compile_proc.returncode != 0:
            detail = (compile_proc.stderr or compile_proc.stdout).strip()
            first = detail.splitlines()[0] if detail else "probe compile failed"
            return _empty_result(spec, compiler=selected_compiler, errors=[first])

        run_proc = subprocess.run(
            [str(exe_path)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if run_proc.returncode != 0:
            detail = (run_proc.stderr or run_proc.stdout).strip()
            first = detail.splitlines()[0] if detail else "probe runtime failed"
            return _empty_result(spec, compiler=selected_compiler, errors=[first])

        constants, macros, enums, records = _parse_probe_stdout(spec, run_proc.stdout)
        return ProbeResult(
            ok=True,
            spec_name=spec.name,
            compiler=selected_compiler,
            headers=[asdict(row) for row in spec.headers],
            link_flags=list(spec.link_flags),
            constants=constants,
            macros=macros,
            enums=enums,
            records=records,
            functions=[
                {
                    "name": row.name,
                    "return_type": row.return_type,
                    "params": [asdict(p) for p in row.params],
                    "variadic": row.variadic,
                    "decorators": row.decorators,
                }
                for row in spec.functions
            ],
            wrappers=[
                {
                    "name": row.name,
                    "return_type": row.return_type,
                    "abi_return_type": row.abi_return_type,
                    "params": [asdict(p) for p in row.params],
                    "decorators": row.decorators,
                    "body_kind": "expr" if row.expr is not None else "body",
                }
                for row in spec.wrappers
            ],
            errors=[],
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return _empty_result(spec, compiler=selected_compiler, errors=[str(exc)])
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
def _c_include_lines(spec: BindingSpec) -> list[str]:
    lines = [
        "#include <stdbool.h>",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
    ]
    for header in spec.headers:
        if header.system:
            lines.append(f"#include <{header.path}>")
        else:
            lines.append(f'#include "{header.path}"')
    if spec.c_prelude:
        lines.append("")
        lines.extend(spec.c_prelude)
    return lines
def _render_c_string_literal(text: str) -> str:
    return '"' + _quote_c_string(text).replace("\n", "\\n") + '"'
def generate_c_binding_unit(spec: BindingSpec, result: ProbeResult) -> str:
    """Generate C layout assertions and optional wrapper functions."""
    lines = [
        "/* Generated by tools/cbind_probe.py. Do not edit by hand. */",
        *_c_include_lines(spec),
        "",
    ]

    aliases: set[tuple[str, str]] = set()
    for row in result.records:
        c_name = str(row["c_name"])
        name = str(row["name"])
        if c_name != name and (c_name, name) not in aliases:
            aliases.add((c_name, name))
            lines.append(f"typedef {c_name} {name};")
    if aliases:
        lines.append("")

    for row in result.constants:
        name = str(row["name"])
        expr = str(row.get("expr", name))
        value = int(row.get("value", 0))
        lines.append(
            f"_Static_assert((long long)({expr}) == {value}LL, "
            f"{_render_c_string_literal(f'{name} constant drift')});"
        )
    for row in result.macros:
        if str(row.get("kind", "constant")) != "constant":
            continue
        name = str(row["name"])
        expr = str(row.get("expr", name))
        value = int(row.get("value", 0))
        lines.append(
            f"_Static_assert((long long)({expr}) == {value}LL, "
            f"{_render_c_string_literal(f'{name} macro drift')});"
        )
    for enum in result.enums:
        enum_name = str(enum.get("name", "enum"))
        for variant in enum.get("variants", []):
            name = str(variant["name"])
            expr = str(variant.get("expr", name))
            value = int(variant.get("value", 0))
            lines.append(
                f"_Static_assert((long long)({expr}) == {value}LL, "
                f"{_render_c_string_literal(f'{enum_name}.{name} enum drift')});"
            )
    if result.constants or result.macros or result.enums:
        lines.append("")

    for row in result.records:
        c_name = str(row["c_name"])
        name = str(row["name"])
        size = int(row["size"])
        align = int(row["align"])
        lines.append(
            f"_Static_assert(sizeof({c_name}) == {size}u, "
            f"{_render_c_string_literal(f'{name} size drift')});"
        )
        lines.append(
            f"_Static_assert(_Alignof({c_name}) == {align}u, "
            f"{_render_c_string_literal(f'{name} align drift')});"
        )
        for field in row.get("fields", []):
            field_name = str(field["name"])
            c_field_name = str(field.get("c_name", field_name))
            if "bit_width" in field:
                bit_width = int(field.get("bit_width", 0))
                bit_offset = int(field.get("bit_offset", 0))
                lines.append(
                    f"/* bitfield {name}.{field_name}: "
                    f"width={bit_width} bit_offset={bit_offset} */"
                )
                continue
            offset = int(field["offset"])
            field_size = int(field["size"])
            lines.append(
                f"_Static_assert(offsetof({c_name}, {c_field_name}) == {offset}u, "
                f"{_render_c_string_literal(f'{name}.{field_name} offset drift')});"
            )
            lines.append(
                f"_Static_assert(sizeof((({c_name}*)0)->{c_field_name}) == "
                f"{field_size}u, "
                f"{_render_c_string_literal(f'{name}.{field_name} size drift')});"
            )
        lines.append("")

    for wrapper in spec.wrappers:
        params = ", ".join(f"{param.abi_type} {param.name}" for param in wrapper.params)
        if not params:
            params = "void"
        lines.append(f"{wrapper.abi_return_type} {wrapper.name}({params}) {{")
        if wrapper.expr is not None:
            if wrapper.abi_return_type == "void":
                lines.append(f"    {wrapper.expr};")
                lines.append("    return;")
            else:
                lines.append(f"    return ({wrapper.abi_return_type})({wrapper.expr});")
        else:
            for body_line in (wrapper.body or "").splitlines():
                lines.append(f"    {body_line}" if body_line else "")
        lines.append("}")
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)

__all__ = [name for name in globals() if not name.startswith("__")]

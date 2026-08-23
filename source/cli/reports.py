"""CLI report commands for runtime needs, check elision, formatting, and effects."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from abi_symbols import c_symbol_for_function, has_export_decorator
from callback_types import callback_parts, is_callback_type
from cli.cinclude_diagnostics import (
    CIncludeDirective,
    cinclude_backend_support_payload,
    cinclude_backend_warning,
    cinclude_directive_from_node,
    cinclude_directive_payload,
    diagnose_cinclude_directives,
    format_cinclude,
)
from cli.layout_probe import probe_ffi_layout
from target_info import os_from_platform, target_matches


def report_runtime_needs(source_file: str, *, as_json: bool = False) -> bool:
    """Report runtime helper families required by the C backend."""
    try:
        from parser.parser import Parser

        from lexer.scan import tokenize
        from transpiler.core import CTranspiler
        from transpiler.runtime_needs import RUNTIME_FAMILY_NAMES
    except ImportError:
        print("Error: transpiler/parser modules unavailable for runtime-needs report")
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()
        transpiler = CTranspiler()
        c_code = transpiler.transpile(ast, source_file)
        needs = transpiler.runtime_needs
        families = needs.family_flags()
        family_helper_counts = needs.helper_counts_by_family()
        helpers_sorted = sorted(needs.helpers)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: failed to build runtime-needs report: {exc}")
        return False

    normalized_families: dict[str, bool] = {
        name: bool(families.get(name, False)) for name in RUNTIME_FAMILY_NAMES
    }
    normalized_family_helper_counts: dict[str, int] = {
        name: int(family_helper_counts.get(name, 0)) for name in RUNTIME_FAMILY_NAMES
    }
    helper_count = len(helpers_sorted)
    generated_c_bytes = len(c_code.encode("utf-8"))
    spawn_target_count = len(needs.spawn_targets)

    payload = {
        "source": str(Path(source_file).resolve()),
        "helper_count": helper_count,
        "helpers": helpers_sorted,
        "families": normalized_families,
        "family_helper_counts": normalized_family_helper_counts,
        "generated_c_bytes": generated_c_bytes,
        "spawn_target_count": spawn_target_count,
    }

    if as_json:
        print(json.dumps(payload, indent=2))
        return True

    print(f"Runtime needs report for: {source_file}")
    print("")
    print(f"helper_count={helper_count}")
    print(f"generated_c_bytes={generated_c_bytes}")
    print(f"spawn_target_count={spawn_target_count}")
    print("")
    print("families:")
    for name in RUNTIME_FAMILY_NAMES:
        enabled = "yes" if normalized_families.get(name, False) else "no"
        count = normalized_family_helper_counts.get(name, 0)
        print(f"  {name}: {enabled} (helpers={count})")
    print("")
    print("helpers:")
    if helpers_sorted:
        for name in helpers_sorted:
            print(f"  {name}")
    else:
        print("  (none)")
    return True


def _decorator_names(node: object) -> list[str]:
    raw = getattr(node, "decorators", []) or []
    return [str(item).lstrip("@") for item in raw]


def _c_symbol_mangler(nodes: Sequence[object]) -> Callable[[str], str]:
    from parser import ast as A

    from transpiler.core import CTranspiler

    transpiler = CTranspiler()
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
    return transpiler._mangle_name


def report_ffi(source_file: str, *, as_json: bool = False) -> bool:
    """Report C/FFI surface required or exposed by a source file."""
    try:
        from parser import ast as A
        from parser.ast import parsed_type_to_str
        from parser.parser import Parser

        from lexer.scan import tokenize
        from transpiler.import_resolver import ImportResolver
    except ImportError:
        print("Error: parser/import modules unavailable for FFI report")
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        nodes = parser.parse_program()
        nodes = ImportResolver().run(nodes, source_file)
    except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
        print(f"Error: failed to build FFI report: {exc}")
        return False

    mangle_c_symbol = _c_symbol_mangler(nodes)

    def fields_payload(fields: list[tuple[str, str]]) -> list[dict[str, str]]:
        return [
            {"name": name, "type": parsed_type_to_str(ftype)} for name, ftype in fields
        ]

    def params_payload(params: Sequence[tuple[Any, ...]]) -> list[dict[str, str]]:
        rows = []
        for param in params:
            if len(param) >= 2:
                name, ptype, *_rest = param
            else:
                (name,) = param
                ptype = "int"
            rows.append({"name": str(name), "type": parsed_type_to_str(ptype)})
        return rows

    include_directives_all: list[CIncludeDirective] = []
    includes: list[dict[str, Any]] = []
    links: list[str] = []
    link_directives: list[dict[str, Any]] = []
    extern_functions: list[dict[str, Any]] = []
    extern_vars: list[dict[str, Any]] = []
    opaque_records: list[dict[str, Any]] = []
    callback_aliases: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    unions: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    current_os = os_from_platform()

    for node in nodes:
        decorators = _decorator_names(node)
        if isinstance(node, A.CInclude):
            directive = cinclude_directive_from_node(
                node,
                current_os=current_os,
                fallback_source_file=source_file,
            )
            include_directives_all.append(directive)
            includes.append(cinclude_directive_payload(directive))
        elif isinstance(node, A.LinkDirective):
            target_os = getattr(node, "target_os", None)
            links.append(node.flags)
            link_directives.append(
                {
                    "flags": node.flags,
                    "target_os": target_os,
                    "active": target_matches(target_os, current_os),
                    "line": int(getattr(node, "line", 1) or 1),
                    "column": int(getattr(node, "column", 1) or 1),
                    "source_file": getattr(node, "_source_file", None) or source_file,
                }
            )
        elif isinstance(node, A.ExternFn):
            extern_functions.append(
                {
                    "name": node.name,
                    "return_type": parsed_type_to_str(node.ret_type),
                    "params": [
                        {"name": name, "type": parsed_type_to_str(ptype)}
                        for name, ptype in node.params
                    ],
                    "variadic": bool(node.variadic),
                    "decorators": decorators,
                }
            )
        elif isinstance(node, A.ExternVar):
            extern_vars.append(
                {
                    "name": node.name,
                    "type": parsed_type_to_str(node.var_type),
                    "decorators": decorators,
                }
            )
        elif isinstance(node, A.ExternRecordDef):
            field_offsets = getattr(node, "field_offsets", {}) or {}
            field_sizes = getattr(node, "field_sizes", {}) or {}
            bitfields = getattr(node, "bitfields", {}) or {}
            layout = None
            if getattr(node, "layout_size", None) is not None:
                layout = {
                    "size": int(getattr(node, "layout_size", 0) or 0),
                    "align": int(getattr(node, "layout_align", 0) or 0),
                    "fields": {
                        field_name: {
                            "offset": int(offset),
                            "size": int(field_sizes.get(field_name, 0)),
                            **(
                                {
                                    "bit_width": int(
                                        bitfields[field_name].get("width", 0)
                                    ),
                                    "bit_offset": int(
                                        bitfields[field_name].get("bit_offset", 0)
                                    ),
                                }
                                if field_name in bitfields
                                else {}
                            ),
                        }
                        for field_name, offset in sorted(field_offsets.items())
                    },
                }
            row = {
                "name": node.name,
                "c_name": getattr(node, "c_name", node.name),
                "opaque": bool(getattr(node, "is_opaque", True)),
                "fields": fields_payload(getattr(node, "fields", [])),
                "decorators": decorators,
            }
            if layout is not None:
                row["layout"] = layout
            opaque_records.append(row)
        elif isinstance(node, A.TypeAlias) and is_callback_type(node.target_type):
            params, ret_type, callback_decorators = callback_parts(node.target_type)
            callback_aliases.append(
                {
                    "name": node.name,
                    "return_type": parsed_type_to_str(ret_type),
                    "params": [
                        {"name": name, "type": parsed_type_to_str(ptype)}
                        for name, ptype in params
                    ],
                    "decorators": callback_decorators,
                }
            )
        elif isinstance(node, A.RecordDef):
            records.append(
                {
                    "name": node.name,
                    "c_name": node.name,
                    "fields": fields_payload(node.fields),
                    "decorators": decorators,
                }
            )
        elif isinstance(node, A.UnionDef):
            unions.append(
                {
                    "name": node.name,
                    "c_name": node.name,
                    "fields": fields_payload(node.fields),
                    "decorators": decorators,
                }
            )
        elif isinstance(node, A.Function) and has_export_decorator(
            getattr(node, "decorators", [])
        ):
            exports.append(
                {
                    "kind": "function",
                    "name": node.name,
                    "c_symbol": mangle_c_symbol(node.name),
                    "return_type": parsed_type_to_str(node.return_type),
                    "params": params_payload(node.params),
                    "decorators": decorators,
                }
            )

    active_include_directives = [row for row in include_directives_all if row.active]
    inactive_include_directives = [
        row for row in include_directives_all if not row.active
    ]
    active_links = [row for row in link_directives if row["active"]]
    inactive_links = [row for row in link_directives if not row["active"]]
    backend_warning = cinclude_backend_warning(active_include_directives, "LLVM/JIT")
    backend_warnings = [backend_warning] if backend_warning else []
    unsupported_cinclude_backends = ["llvm", "jit"] if active_include_directives else []
    cinclude_support = cinclude_backend_support_payload()
    cinclude_diagnostics = [
        row.to_dict() for row in diagnose_cinclude_directives(include_directives_all)
    ]
    generated_c_symbols = [
        {
            "kind": row["kind"],
            "name": row["name"],
            "c_symbol": row["c_symbol"],
        }
        for row in exports
    ]
    layout_probe = probe_ffi_layout(source_file, records, unions)
    for row in records:
        layout = layout_probe.get("records", {}).get(row["c_name"])
        if layout:
            row["layout"] = layout
    for row in unions:
        layout = layout_probe.get("unions", {}).get(row["c_name"])
        if layout:
            row["layout"] = layout

    payload = {
        "source": str(Path(source_file).resolve()),
        "target_os": current_os,
        "cinclude_backend": "c_backend_only",
        "cinclude_support": cinclude_support,
        "unsupported_cinclude_backends": unsupported_cinclude_backends,
        "backend_warnings": backend_warnings,
        "cinclude_diagnostic_count": len(cinclude_diagnostics),
        "cinclude_diagnostics": cinclude_diagnostics,
        "include_count": len(includes),
        "active_include_count": len(active_include_directives),
        "inactive_include_count": len(inactive_include_directives),
        "link_count": len(links),
        "active_link_count": len(active_links),
        "inactive_link_count": len(inactive_links),
        "extern_function_count": len(extern_functions),
        "extern_var_count": len(extern_vars),
        "opaque_record_count": len(opaque_records),
        "callback_alias_count": len(callback_aliases),
        "record_count": len(records),
        "union_count": len(unions),
        "export_count": len(exports),
        "includes": includes,
        "links": links,
        "link_directives": link_directives,
        "extern_functions": extern_functions,
        "extern_vars": extern_vars,
        "opaque_records": opaque_records,
        "callback_aliases": callback_aliases,
        "records": records,
        "unions": unions,
        "exports": exports,
        "generated_c_symbols": generated_c_symbols,
        "layout_probe": layout_probe,
    }

    if as_json:
        print(json.dumps(payload, indent=2))
        return True

    print(f"FFI report for: {source_file}")
    print("")
    print(f"target_os={payload['target_os']}")
    for key in (
        "include_count",
        "active_include_count",
        "inactive_include_count",
        "link_count",
        "active_link_count",
        "inactive_link_count",
        "extern_function_count",
        "extern_var_count",
        "opaque_record_count",
        "callback_alias_count",
        "record_count",
        "union_count",
        "export_count",
    ):
        print(f"{key}={payload[key]}")
    print(f"cinclude_backend={payload['cinclude_backend']}")
    if unsupported_cinclude_backends:
        print(
            "unsupported_cinclude_backends=" + ",".join(unsupported_cinclude_backends)
        )
    print("")
    print("cinclude support:")
    for backend, support in cinclude_support.items():
        print(f"  {backend}: {support['status']}")
    print("")
    if backend_warnings:
        print("backend warnings:")
        for warning in backend_warnings:
            print(f"  {warning}")
        print("")
    print("cinclude diagnostics:")
    if cinclude_diagnostics:
        for row in cinclude_diagnostics:
            print(
                f"  {row['severity']} {row['kind']} "
                f"line={row['line']} col={row['column']}: {row['message']}"
            )
            print(f"    hint: {row['suggestion']}")
    else:
        print("  (none)")
    print("")
    print("includes:")
    for row in includes or [{"path": "(none)", "system": False}]:
        scope = "system" if row.get("system") else "local"
        target = f" target={row['target_os']}" if row.get("target_os") else ""
        active = " active" if row.get("active", True) else " inactive"
        exists = ""
        if row.get("exists") is not None:
            exists = " exists=yes" if row.get("exists") else " exists=no"
        spelling = row.get("spelling") or format_cinclude(
            CIncludeDirective(str(row["path"]), bool(row.get("system")))
        )
        print(f"  {scope}{target}{active}{exists}: {spelling}")
    print("links:")
    if link_directives:
        for row in link_directives:
            target = f" target={row['target_os']}" if row.get("target_os") else ""
            active = " active" if row.get("active", True) else " inactive"
            print(f"  {row['flags']}{target}{active}")
    else:
        print("  (none)")
    print("extern functions:")
    if extern_functions:
        for row in extern_functions:
            variadic = " variadic" if row["variadic"] else ""
            print(f"  {row['name']} -> {row['return_type']}{variadic}")
    else:
        print("  (none)")
    print("extern vars:")
    if extern_vars:
        for row in extern_vars:
            print(f"  {row['name']}: {row['type']}")
    else:
        print("  (none)")
    print("opaque records:")
    if opaque_records:
        for row in opaque_records:
            kind = "opaque" if row.get("opaque", True) else "extern"
            layout = row.get("layout")
            layout_text = ""
            if isinstance(layout, dict):
                layout_text = f" size={layout.get('size')} align={layout.get('align')}"
            print(f"  {row['name']}: {kind} c_name={row['c_name']}{layout_text}")
    else:
        print("  (none)")
    print("callback aliases:")
    if callback_aliases:
        for row in callback_aliases:
            decorator_text = " ".join(f"@{name}" for name in row["decorators"])
            suffix = f" {decorator_text}" if decorator_text else ""
            print(f"  {row['name']} -> {row['return_type']}{suffix}")
    else:
        print("  (none)")
    print("exports:")
    if exports:
        for row in exports:
            print(f"  {row['name']} -> {row['c_symbol']}")
    else:
        print("  (none)")
    print("generated C symbols:")
    if generated_c_symbols:
        for row in generated_c_symbols:
            print(f"  {row['kind']} {row['name']} -> {row['c_symbol']}")
    else:
        print("  (none)")
    print("layout probe:")
    status = str(layout_probe.get("status", "unknown"))
    compiler = layout_probe.get("compiler") or "(none)"
    print(f"  status={status}")
    print(f"  compiler={compiler}")
    for kind, bucket_name in (("record", "records"), ("union", "unions")):
        bucket = layout_probe.get(bucket_name, {})
        if not isinstance(bucket, dict):
            continue
        for type_name, layout in bucket.items():
            if not isinstance(layout, dict):
                continue
            print(
                f"  {kind} {type_name}: size={layout.get('size')} "
                f"align={layout.get('align')}"
            )
            fields = layout.get("fields", {})
            if not isinstance(fields, dict):
                continue
            for field_name, field_layout in fields.items():
                if not isinstance(field_layout, dict):
                    continue
                print(
                    f"    {field_name}: offset={field_layout.get('offset')} "
                    f"size={field_layout.get('size')}"
                )
    return True


def report_checks(source_file: str, *, as_json: bool = False) -> bool:
    """Generate a check-elision report without compiling a native binary."""
    try:
        from parser.parser import Parser

        from lexer.scan import tokenize
        from transpiler.core import CTranspiler
    except ImportError:
        print("Error: transpiler/parser modules unavailable for check reporting")
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()
        transpiler = CTranspiler()
        _ = transpiler.transpile(ast, source_file)
        report = transpiler.get_check_report()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: failed to build check report: {exc}")
        return False

    summary = dict(report.get("summary", {}))
    decisions = list(report.get("decisions", []))
    if as_json:
        payload = {
            "source": str(Path(source_file).resolve()),
            "summary": summary,
            "decision_count": len(decisions),
            "decisions": decisions,
        }
        print(json.dumps(payload, indent=2))
        return True

    print(f"Check report for: {source_file}")
    print("")
    if not summary:
        print("summary: no check decisions recorded")
    else:
        print("summary:")
        for key in sorted(summary):
            print(f"  {key}={summary[key]}")
    print("")
    print("decisions:")
    if not decisions:
        print("  (none)")
        return True
    for row in decisions:
        line = int(row.get("line", 0) or 0)
        col = int(row.get("col", 0) or 0)
        func = str(row.get("function", "<global>"))
        op = str(row.get("operation", "?"))
        kind = str(row.get("check_kind", "unknown"))
        decision = str(row.get("decision", "unknown"))
        reason = str(row.get("reason", "unknown"))
        print(
            f"  line={line} col={col} func={func} "
            f"kind={kind} op={op} decision={decision} reason={reason}"
        )
    return True


def report_format(source_file: str, *, as_json: bool = False) -> bool:
    """Generate formatting-specialization report without native compilation."""
    try:
        from parser.parser import Parser

        from lexer.scan import tokenize
        from transpiler.core import CTranspiler
    except ImportError:
        print("Error: transpiler/parser modules unavailable for format reporting")
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()
        transpiler = CTranspiler()
        _ = transpiler.transpile(ast, source_file)
        report = transpiler.get_format_report()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: failed to build format report: {exc}")
        return False

    summary = dict(report.get("summary", {}))
    decisions = list(report.get("decisions", []))
    if as_json:
        payload = {
            "source": str(Path(source_file).resolve()),
            "summary": summary,
            "decision_count": len(decisions),
            "decisions": decisions,
        }
        print(json.dumps(payload, indent=2))
        return True

    print(f"Format report for: {source_file}")
    print("")
    if not summary:
        print("summary: no format decisions recorded")
    else:
        print("summary:")
        for key in sorted(summary):
            print(f"  {key}={summary[key]}")
    print("")
    print("decisions:")
    if not decisions:
        print("  (none)")
        return True
    for row in decisions:
        line = int(row.get("line", 0) or 0)
        col = int(row.get("col", 0) or 0)
        func = str(row.get("function", "<global>"))
        kind = str(row.get("format_kind", "unknown"))
        decision = str(row.get("decision", "unknown"))
        reason = str(row.get("reason", "unknown"))
        fallback_func = str(row.get("fallback_func", "") or "")
        fallback_text = f" fallback={fallback_func}" if fallback_func else ""
        print(
            f"  line={line} col={col} func={func} "
            f"kind={kind} decision={decision} reason={reason}{fallback_text}"
        )
    return True


def report_effect_policy(source_file: str, *, as_json: bool = False) -> bool:
    """Report hosted/freestanding effect/capability violations."""
    try:
        from parser.parser import Parser

        from diagnostics.effect_policy import collect_effect_policy_violations
        from lexer.scan import tokenize
        from runtime.modes import CompilationContext, CompilationMode
    except ImportError:
        print("Error: effect-policy modules unavailable")
        return False

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        program_ast = parser.parse_program()
        mode = CompilationContext.get_mode()
        violations = collect_effect_policy_violations(program_ast, mode)
    except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
        print(f"Error: failed to build effect-policy report: {exc}")
        return False

    by_kind: dict[str, int] = {}
    by_effect: dict[str, int] = {}
    for row in violations:
        by_kind[row.kind] = int(by_kind.get(row.kind, 0)) + 1
        by_effect[row.required_effect] = int(by_effect.get(row.required_effect, 0)) + 1

    mode_name = "freestanding" if mode == CompilationMode.FREESTANDING else "hosted"
    payload = {
        "source": str(Path(source_file).resolve()),
        "mode": mode_name,
        "violation_count": len(violations),
        "by_kind": dict(sorted(by_kind.items())),
        "by_effect": dict(sorted(by_effect.items())),
        "violations": [
            {
                "kind": row.kind,
                "function": row.function,
                "operation": row.operation,
                "required_effect": row.required_effect,
                "line": row.line,
                "column": row.column,
                "message": row.message,
                "suggestion": row.suggestion,
            }
            for row in violations
        ],
    }

    if as_json:
        print(json.dumps(payload, indent=2))
        return not violations

    print(f"Effect policy report for: {source_file}")
    print(f"mode={mode_name}")
    print(f"violation_count={len(violations)}")
    if by_kind:
        print("by_kind:")
        for key in sorted(by_kind):
            print(f"  {key}={by_kind[key]}")
    if by_effect:
        print("by_effect:")
        for key in sorted(by_effect):
            print(f"  {key}={by_effect[key]}")
    if violations:
        print("")
        print("violations:")
        for row in violations:
            print(
                f"  line={row.line} col={row.column} "
                f"func={row.function} op={row.operation} "
                f"effect={row.required_effect} kind={row.kind}"
            )
            print(f"    {row.message}")
            print(f"    hint: {row.suggestion}")
    return not violations

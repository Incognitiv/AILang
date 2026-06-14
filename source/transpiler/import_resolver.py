"""
ImportResolver — service that inlines `.ail` imports into a single AST.

Replaces ``_ImportsMixin``. Receives no shared state from CTranspiler;
input is the raw parsed AST + the source file path, output is a single
flat AST list with every transitively referenced module's definitions
spliced in (de-duplicated, ordered so types / constants precede
function bodies that reference them).

Phase 2 of the New Path roadmap. Pure AST -> AST transformation; no
new data container is needed because the output is already a useful
type (``List[A.ASTNode]``). A future ``ModuleSet`` could carry import
metadata (which file each node came from, dependency graph, etc.) but
nothing in CTranspiler needs that today.

CTranspiler calls this from ``transpile()``::

    nodes = ImportResolver().run(nodes, source_file)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from parser import ast as A
from parser.parser import Parser
from pathlib import Path
from typing import Any, List, Optional, Set

from lexer.scan import tokenize
from target_info import os_from_platform, target_matches
from transpiler.cbind_flags import headers_from_cflags


class ImportResolver:
    """Inlines all `.ail` and probe/AIL import payloads referenced
    (transitively) by the input AST. Returns a flat AST containing the
    original non-import nodes plus every imported function / constant /
    type, sorted so types / constants precede function bodies that
    reference them.
    """

    # Hard cap so a malformed import path can't loop forever when walking
    # up the directory tree looking for the module file.
    _MAX_PARENT_WALK = 10

    def run(self, nodes: List[A.ASTNode], source_file: str) -> List[A.ASTNode]:
        """Return the AST with all transitive imports inlined."""
        result: List[A.ASTNode] = []
        imported_funcs: Set[str] = set()
        base_dir = Path(source_file).parent if source_file else Path(".")
        processed_files: Set[str] = set()
        self._tag_source_file(nodes, source_file)

        self._process_file_imports(
            nodes, base_dir, result, imported_funcs, processed_files
        )

        # Add non-import nodes from the main file. Imported file nodes
        # already landed in `result` via the recursion above.
        result.extend(
            node
            for node in nodes
            if not isinstance(node, (A.Import, A.FromImport, A.CImport))
        )

        # Order so type definitions and constants precede function bodies
        # that reference them. Without this, static globals would be
        # declared after their first use site -- C compile error.
        result.sort(key=self._sort_key)

        return result

    # ==================== internals ====================

    def _process_file_imports(
        self,
        file_nodes: List[A.ASTNode],
        file_base_dir: Path,
        result: List[A.ASTNode],
        imported_funcs: Set[str],
        processed_files: Set[str],
    ) -> None:
        """Recursively splice in each Import/FromImport/CImport target."""
        current_os = os_from_platform()
        for node in file_nodes:
            if not isinstance(node, (A.Import, A.FromImport)):
                if isinstance(node, A.CImport):
                    if not target_matches(node.target_os, current_os):
                        continue
                    import_file = self._resolve_cimport_path(node.path, file_base_dir)
                else:
                    continue
            else:
                if not target_matches(getattr(node, "target_os", None), current_os):
                    continue
                import_file = self._resolve_module_path(node.module_path, file_base_dir)
            if import_file is None:
                continue
            import_file_str = str(import_file.resolve())
            if import_file_str in processed_files:
                continue
            processed_files.add(import_file_str)
            if import_file.suffix.lower() == ".json":
                imported_nodes = self._parse_probe_import_file(import_file_str)
            else:
                imported_nodes = self._parse_import_file(import_file_str)
            # Recurse into the imported file's own imports first so its
            # dependencies are inlined before its definitions.
            self._process_file_imports(
                imported_nodes,
                import_file.parent,
                result,
                imported_funcs,
                processed_files,
            )
            # Then splice in this file's definitions.
            filter_names: Optional[Set[str]] = None
            if isinstance(node, A.FromImport):
                filter_names = set(node.names)
            for imp_node in imported_nodes:
                self._add_imported_node(imp_node, result, imported_funcs, filter_names)

    def _resolve_cimport_path(
        self, raw_path: str, file_base_dir: Path
    ) -> Optional[Path]:
        """Resolve a filesystem path from a #cimport directive.

        Supports quoted paths, optional suffix inference, and parent-tree
        search (same strategy as module imports) for portability.
        """
        requested = raw_path.strip()
        if not requested:
            return None
        candidate_path = Path(requested)
        wanted = self._cimport_candidate_paths(candidate_path)

        if candidate_path.is_absolute():
            for candidate in wanted:
                if candidate.exists():
                    return candidate
            return None

        for candidate in wanted:
            direct = file_base_dir / candidate
            if direct.exists():
                return direct

        walk = file_base_dir
        for _ in range(self._MAX_PARENT_WALK):
            for candidate in wanted:
                ancestor_candidate = walk / candidate
                if ancestor_candidate.exists():
                    return ancestor_candidate
            if walk.parent == walk:
                break
            walk = walk.parent

        # Final fallback: CWD-relative.
        for candidate in wanted:
            cwd_candidate = Path.cwd() / candidate
            if cwd_candidate.exists():
                return cwd_candidate
        return None

    def _cimport_candidate_paths(self, base_path: Path) -> list[Path]:
        """Build import candidates for cimport: explicit path and common ABI files."""
        requested = base_path
        candidates: list[Path] = []
        if requested.suffix:
            candidates.append(requested)
        else:
            candidates.extend(
                [
                    requested.with_suffix(".bindings.ail"),
                    requested.with_suffix(".cbind.json"),
                    requested.with_suffix(".probe.json"),
                    requested.with_suffix(".ail"),
                    requested,
                ]
            )
            # Dedupe while preserving order.
        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate)
            if text in seen:
                continue
            deduped.append(candidate)
            seen.add(text)
        return deduped

    def _resolve_module_path(
        self, module_path: str, file_base_dir: Path
    ) -> Optional[Path]:
        """Resolve `module.path` -> filesystem `module/path.ail`.

        Tries source-relative first, then walks up the tree to find an
        ancestor where the path resolves (so `src/parser/foo.ail`
        importing `lexer.scan` finds `src/lexer/scan.ail` even though
        it's a sibling, not a child). Falls back to CWD as a last
        resort. Returns None if the module can't be located -- the
        caller silently skips, matching the legacy mixin's behavior.
        """
        rel_path = module_path.replace(".", "/") + ".ail"
        candidate = file_base_dir / rel_path
        if candidate.exists():
            return candidate
        # Walk up the directory tree looking for an ancestor that resolves.
        walk = file_base_dir
        for _ in range(self._MAX_PARENT_WALK):
            ancestor_candidate = walk / rel_path
            if ancestor_candidate.exists():
                return ancestor_candidate
            if walk.parent == walk:  # filesystem root
                break
            walk = walk.parent
        # Final fallback: CWD-relative.
        cwd_path = Path.cwd() / rel_path
        if cwd_path.exists():
            return cwd_path
        return None

    def _add_imported_node(
        self,
        imp_node: A.ASTNode,
        result: List[A.ASTNode],
        imported_funcs: Set[str],
        filter_names: Optional[Set[str]] = None,
    ) -> None:
        """Splice one node from an imported module into ``result`` if it
        isn't already there and isn't filtered out by a `from` clause.
        """
        if isinstance(imp_node, A.Function):
            # An imported module's `main` is a peer entry point, not a
            # library function. Including it would produce two `int main`
            # in one TU. Only the top-level file's main belongs in output.
            if imp_node.name == "main":
                return
            if filter_names is not None and imp_node.name not in filter_names:
                return
            if imp_node.name in imported_funcs:
                return
            result.append(imp_node)
            imported_funcs.add(imp_node.name)
        elif isinstance(imp_node, A.VarDecl):
            # Constants (VarDecl with is_const=True) AND mutable statics.
            var_name = imp_node.var_name
            if filter_names is not None and var_name not in filter_names:
                return
            if var_name in imported_funcs:
                return
            result.append(imp_node)
            imported_funcs.add(var_name)
        elif isinstance(imp_node, (A.RecordDef, A.EnumDef, A.ClassDef)):
            result.append(imp_node)
        elif isinstance(imp_node, A.ExternFn):
            if filter_names is not None and imp_node.name not in filter_names:
                return
            key = f"extern fn {imp_node.name}"
            if key in imported_funcs:
                return
            result.append(imp_node)
            imported_funcs.add(key)
        elif isinstance(imp_node, A.ExternVar):
            if filter_names is not None and imp_node.name not in filter_names:
                return
            key = f"extern var {imp_node.name}"
            if key in imported_funcs:
                return
            result.append(imp_node)
            imported_funcs.add(key)
        elif isinstance(imp_node, A.ExternRecordDef):
            if filter_names is not None and imp_node.name not in filter_names:
                return
            key = f"extern record {imp_node.name}"
            if key in imported_funcs:
                return
            result.append(imp_node)
            imported_funcs.add(key)
        elif isinstance(imp_node, (A.CInclude, A.LinkDirective, A.TemplateBlock)):
            result.append(imp_node)

    def _parse_import_file(self, filepath: str) -> List[A.ASTNode]:
        """Parse one imported `.ail` file.

        Existing-but-invalid imports must fail at the import boundary. Returning
        an empty module hides the real parse error and later reports unrelated
        backend errors such as missing constants or functions.
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()
            tokens = tokenize(code)
            p = Parser(tokens)
            nodes = p.parse_program()
            self._tag_source_file(nodes, filepath)
            return nodes
        except SyntaxError as exc:
            raise SyntaxError(
                f"{filepath}: failed to parse imported module: {exc}"
            ) from exc
        except OSError as exc:
            raise OSError(f"{filepath}: failed to read imported module: {exc}") from exc

    def _parse_probe_import_file(self, filepath: str) -> List[A.ASTNode]:
        """Parse one imported cbind JSON file.

        Accepts generated `.probe.json` payloads and raw binding specs. Raw specs
        are resolved by running `tools/cbind_probe.py` in-process, so `#cimport`
        can consume the binding source directly when a C compiler is available.
        """
        try:
            payload_text = Path(filepath).read_text(encoding="utf-8")
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                return []
            payload, c_unit = self._probe_payload_from_json(filepath, payload)
            if not payload.get("ok", True):
                return []
            nodes: list[A.ASTNode] = []

            for header in payload.get("headers", []):
                header_path, is_system = self._normalize_probe_header(header)
                if header_path is None:
                    continue
                nodes.append(A.CInclude(header_path, is_system=is_system))

            for flag in payload.get("link_flags", []):
                text = str(flag).strip()
                if text:
                    nodes.append(A.LinkDirective(text))

            for row in self._iter_probe_constants(payload):
                const_node = self._probe_constant_node(row, c_header_declared=True)
                if const_node is not None:
                    nodes.append(const_node)

            for row in payload.get("enums", []):
                enum_node = self._probe_enum_node(row)
                if enum_node is not None:
                    nodes.append(enum_node)

            for row in payload.get("records", []):
                record_node = self._probe_record_node(row)
                if record_node is not None:
                    nodes.append(record_node)
                    nodes.extend(self._probe_record_layout_constant_nodes(record_node))

            for row in payload.get("functions", []):
                fn_node = self._probe_fn_node(
                    row,
                    function_name=row.get("name"),
                    header_declared=True,
                )
                if fn_node is not None:
                    nodes.append(fn_node)

            for row in payload.get("wrappers", []):
                wrapper_node = self._probe_fn_node(row)
                if wrapper_node is not None:
                    nodes.append(wrapper_node)

            if c_unit:
                nodes.append(A.TemplateBlock("ansi_c", c_unit))

            self._tag_source_file(nodes, filepath)
            return nodes
        except (OSError, ValueError, TypeError):
            return []

    def _probe_payload_from_json(
        self, filepath: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Return a generated cbind probe payload from a JSON cimport file."""
        if "ok" in payload or "spec_name" in payload:
            return payload, None
        tool = self._load_cbind_probe_tool()
        if tool is None:
            return {"ok": False, "errors": ["tools/cbind_probe.py unavailable"]}, None
        try:
            spec = tool.load_binding_spec(filepath)
            result = tool.probe_binding_spec(spec)
            generated = json.loads(result.to_json())
            include_headers = headers_from_cflags(getattr(spec, "cflags", []))
            if include_headers:
                headers = generated.get("headers", [])
                if not isinstance(headers, list):
                    headers = []
                seen = {
                    str(item.get("path", ""))
                    for item in headers
                    if isinstance(item, dict)
                }
                for header in include_headers:
                    header_path = str(header["path"])
                    if header_path not in seen:
                        headers.append(header)
                        seen.add(header_path)
                generated["headers"] = headers
            c_unit = None
            if result.ok and (
                getattr(spec, "wrappers", None) or getattr(spec, "c_prelude", None)
            ):
                c_unit = tool.generate_c_binding_unit(spec, result)
            return generated, c_unit
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            return {"ok": False, "errors": ["cbind probe failed"]}, None

    @staticmethod
    def _load_cbind_probe_tool() -> Any | None:
        root = Path(__file__).resolve().parent.parent.parent
        tool_path = root / "tools" / "cbind_probe.py"
        spec = importlib.util.spec_from_file_location("ailang_cbind_probe", tool_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _iter_probe_constants(payload: dict[str, Any]) -> list[object]:
        """Return constants plus integer-like macro rows from a probe payload."""
        rows: list[object] = []
        constants = payload.get("constants", [])
        macros = payload.get("macros", [])
        if isinstance(constants, list):
            rows.extend(constants)
        if isinstance(macros, list):
            rows.extend(
                row
                for row in macros
                if isinstance(row, dict)
                and str(row.get("kind", "constant")).lower() == "constant"
            )
        return rows

    @staticmethod
    def _normalize_probe_header(header: object) -> tuple[Optional[str], bool]:
        """Return ``(path, is_system)`` from cbind probe header descriptor."""
        if isinstance(header, str):
            text = header.strip()
            if not text:
                return None, True
            if text.startswith("<") and text.endswith(">"):
                return text[1:-1], True
            if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                return text[1:-1], False
            return text, bool(text) and not any(ch in text for ch in '<>"')

        if not isinstance(header, dict):
            return None, True
        path = str(header.get("path", "")).strip()
        if not path:
            return None, True
        system = bool(header.get("system", True))
        return path, system

    @staticmethod
    def _probe_constant_node(
        row: object,
        *,
        c_header_declared: bool = False,
    ) -> Optional[A.VarDecl]:
        if not isinstance(row, dict):
            return None
        name = str(row.get("name", "")).strip()
        if not name:
            return None

        value = row.get("value", 0)
        if isinstance(value, str):
            try:
                value = int(value, 0)
            except ValueError:
                try:
                    value = int(float(value))
                except (ValueError, TypeError):
                    value = 0
        elif not isinstance(value, (int, bool)):
            value = 0

        node = A.VarDecl(
            "int",
            name,
            A.Number(str(int(value)), is_long=False),
            is_const=True,
        )
        if c_header_declared:
            setattr(node, "c_header_declared", True)
        return node

    @staticmethod
    def _probe_enum_node(row: object) -> Optional[A.EnumDef]:
        if not isinstance(row, dict):
            return None
        name = str(row.get("name", "")).strip()
        if not name:
            return None
        variants_raw = row.get("variants", [])
        variants: list[A.EnumVariant] = []
        if isinstance(variants_raw, dict):
            variants_iter = [
                {"name": key, "value": value}
                for key, value in sorted(
                    variants_raw.items(), key=ImportResolver._dict_key_sort_key
                )
            ]
        elif isinstance(variants_raw, list):
            variants_iter = variants_raw
        else:
            variants_iter = []
        for index, entry in enumerate(variants_iter):
            if isinstance(entry, str):
                variant_name = entry.strip()
                value = index
            elif isinstance(entry, dict):
                variant_name = str(entry.get("name", "")).strip()
                value = entry.get("value", index)
            else:
                continue
            if not variant_name:
                continue
            try:
                int_value = int(value, 0) if isinstance(value, str) else int(value)
            except (TypeError, ValueError):
                int_value = index
            variants.append(A.EnumVariant(variant_name, value=int_value))
        if not variants:
            return None
        return A.EnumDef(name, variants)

    @staticmethod
    def _dict_key_sort_key(item: tuple[object, object]) -> str:
        key, _value = item
        return str(key)

    @staticmethod
    def _probe_record_node(row: object) -> Optional[A.ExternRecordDef]:
        if not isinstance(row, dict):
            return None
        name = str(row.get("name", "")).strip()
        if not name:
            return None
        has_c_name = "c_name" in row
        c_name = str(row.get("c_name", name)).strip() or name
        kind = str(row.get("kind", "extern")).strip().lower()
        is_opaque = kind == "opaque"
        fields_raw = row.get("fields", [])

        fields: list[tuple[str, str]] = []
        if isinstance(fields_raw, list):
            for entry in fields_raw:
                if isinstance(entry, str):
                    fname = entry.strip()
                    if fname:
                        fields.append((fname, "int"))
                elif isinstance(entry, dict):
                    fname = str(entry.get("name", "")).strip()
                    if fname:
                        field_type = str(entry.get("type", "int")).strip() or "int"
                        fields.append((fname, field_type))

        size = int(row.get("size", 0) or 0)
        align = int(row.get("align", 0) or 0)
        field_offsets: dict[str, int] = {}
        field_sizes: dict[str, int] = {}
        bitfields: dict[str, dict[str, int]] = {}
        for raw_field in row.get("fields", []):
            if not isinstance(raw_field, dict):
                continue
            fname = str(raw_field.get("name", "")).strip()
            if not fname:
                continue
            if "offset" in raw_field:
                try:
                    field_offsets[fname] = int(raw_field.get("offset", 0) or 0)
                except (TypeError, ValueError):
                    field_offsets[fname] = 0
            if "size" in raw_field:
                try:
                    field_sizes[fname] = int(raw_field.get("size", 0) or 0)
                except (TypeError, ValueError):
                    field_sizes[fname] = 0
            bit_width = raw_field.get("bit_width")
            if bit_width is not None:
                bit_offset_raw = raw_field.get("bit_offset", 0)
                try:
                    bitfields[fname] = {
                        "width": int(bit_width),
                        "bit_offset": int(bit_offset_raw or 0),
                    }
                except (TypeError, ValueError):
                    bitfields[fname] = {"width": 0, "bit_offset": 0}

        node = A.ExternRecordDef(
            name,
            fields=fields,
            is_opaque=is_opaque,
            c_name=c_name,
            c_name_explicit=has_c_name or c_name != name,
            layout_size=size if size > 0 or field_offsets or field_sizes else None,
            layout_align=align if align > 0 or field_offsets or field_sizes else None,
            field_offsets=field_offsets,
            field_sizes=field_sizes,
            bitfields=bitfields,
        )
        return node

    @staticmethod
    def _probe_record_layout_constant_nodes(node: A.ExternRecordDef) -> list[A.VarDecl]:
        """Emit SIZEOF/ALIGNOF/OFFSETOF constants for direct JSON cimports.

        `tools/cbind_probe.py --ail-out` has emitted these convenience constants
        for generated `.bindings.ail` files for a while. Direct raw `.cbind.json`
        imports should expose the same names so users do not have to choose
        between checked layout metadata and ergonomic raw-memory code.
        """
        nodes: list[A.VarDecl] = []
        name_ident = ImportResolver._normalize_identifier(node.name)

        if node.layout_size is not None:
            nodes.append(
                ImportResolver._int_constant_node(
                    f"SIZEOF_{name_ident}", int(node.layout_size)
                )
            )
        if node.layout_align is not None:
            nodes.append(
                ImportResolver._int_constant_node(
                    f"ALIGNOF_{name_ident}", int(node.layout_align)
                )
            )

        for field_name, offset in sorted((node.field_offsets or {}).items()):
            field_ident = ImportResolver._normalize_identifier(field_name)
            nodes.append(
                ImportResolver._int_constant_node(
                    f"OFFSETOF_{name_ident}_{field_ident}", int(offset)
                )
            )
        for field_name, meta in sorted((node.bitfields or {}).items()):
            field_ident = ImportResolver._normalize_identifier(field_name)
            width = int(meta.get("width", 0) or 0)
            bit_offset = int(meta.get("bit_offset", 0) or 0)
            nodes.append(
                ImportResolver._int_constant_node(
                    f"BITWIDTH_{name_ident}_{field_ident}", width
                )
            )
            nodes.append(
                ImportResolver._int_constant_node(
                    f"BITOFFSET_{name_ident}_{field_ident}", bit_offset
                )
            )
        return nodes

    @staticmethod
    def _normalize_identifier(name: str) -> str:
        import re

        clean = re.sub(r"[^A-Za-z0-9_]", "_", str(name)).strip("_")
        if not clean:
            return "_"
        if clean[0].isdigit():
            return f"_{clean}"
        return clean

    @staticmethod
    def _int_constant_node(name: str, value: int) -> A.VarDecl:
        return A.VarDecl(
            "int",
            name,
            A.Number(str(int(value)), is_long=False),
            is_const=True,
        )

    @staticmethod
    def _probe_fn_node(
        row: object,
        *,
        function_name: str | None = None,
        header_declared: bool = False,
    ) -> Optional[A.ExternFn]:
        if not isinstance(row, dict):
            return None
        name = function_name or str(row.get("name", "")).strip()
        if not name:
            name = ""
        if not name:
            return None
        ret_type = str(row.get("return_type", "int")).strip() or "int"
        params_raw = row.get("params", [])
        params: list[tuple[str, str]] = []
        if isinstance(params_raw, list):
            for index, entry in enumerate(params_raw):
                if not isinstance(entry, dict):
                    continue
                pname = str(entry.get("name", f"arg{index}")).strip() or f"arg{index}"
                ptype = str(entry.get("type", "int")).strip() or "int"
                params.append((pname, ptype))
        fn = A.ExternFn(
            name, params, ret_type, variadic=bool(row.get("variadic", False))
        )
        decorators = row.get("decorators", [])
        fn_decorators: list[str] = []
        if isinstance(decorators, list):
            fn_decorators = [str(item).lstrip("@") for item in decorators if str(item)]
        if header_declared:
            fn_decorators.append("header_declared")
        fn.decorators = fn_decorators
        return fn

    @staticmethod
    def _tag_source_file(nodes: List[A.ASTNode], filepath: str) -> None:
        """Attach source path metadata to parsed nodes for diagnostics/reports."""
        if not filepath:
            return
        for node in nodes:
            if not hasattr(node, "_source_file"):
                setattr(node, "_source_file", filepath)

    @staticmethod
    def _sort_key(node: A.ASTNode) -> int:
        """Order so type defs / constants come before functions that
        reference them. Stable sort preserves the within-bucket order
        from the splice walk."""
        if isinstance(node, (A.RecordDef, A.EnumDef, A.ExternRecordDef)):
            return 0
        if isinstance(node, A.VarDecl):
            return 1
        if isinstance(node, A.ClassDef):
            return 2
        return 3

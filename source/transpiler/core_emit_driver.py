"""CTranspiler emission driver, name mangling, and ABI builtins."""

from __future__ import annotations

from parser import ast as A
from typing import Any, List, Optional

from abi_symbols import c_symbol_for_function
from callback_types import callback_parts, resolve_callback_alias


class _CTranspilerEmitDriverMixin:
    def __init__(self: Any) -> None:
        # Linter-only declarations for attributes reassigned during transpile().
        self._source_file: str = ""
        self.runtime_needs: Any = None
        self.range_facts: Any = None
        self._globally_used_names: set[str] = set()
        self._const_global_names: set[str] = set()
        self._static_global_names: set[str] = set()
        self._check_decisions: list[dict[str, object]] = []
        self._check_summary: dict[str, int] = {}
        self._format_decisions: list[dict[str, object]] = []
        self._format_summary: dict[str, int] = {}
        self._array_len_hints: dict[tuple[Optional[str], str], int] = {}
        self._type_aliases: dict[str, Any] = {}
        self._function_nodes: dict[str, A.Function] = {}

    def _format_method_param(self: Any, param: Any) -> str:
        """Format a single method parameter for a C declaration. Used
        by stmt_visit's class-method emitter; PrologueEmitter has its
        own copy for forward-decl emission. Both are tiny and
        identical -- keeping two avoids forcing stmt_visit to
        construct a PrologueEmitter just to call this one helper."""
        if isinstance(param, tuple):
            if len(param) >= 2:
                pname, ptype, *_ = param
            else:
                (pname,) = param
                ptype = "int"
            return self._format_c_param_declaration(ptype, pname)
        return f"int64_t {param}"

    def _default_mangle_name(self: Any, name: str) -> str:
        """Mangle function name if it conflicts with C stdlib."""
        if name in self.C_RESERVED_NAMES and name in self.user_defined_funcs:
            return f"ailang_{name}"
        return name

    def _mangle_name(self: Any, name: str) -> str:
        """Return the emitted C symbol for a function name."""
        mapped = getattr(self, "_function_c_symbols", {}).get(name)
        if mapped:
            return str(mapped)
        return self._default_mangle_name(name)

    def _mangle_var(self: Any, name: str) -> str:
        """Mangle variable name if it conflicts with C stdlib."""
        if name in self.C_RESERVED_NAMES:
            return f"v_{name}"
        return name

    def emit(self: Any, line: str) -> None:
        """Emit a line of C code with proper indentation."""
        self.output.append("    " * self.indent + line)

    def emit_raw(self: Any, line: str) -> None:
        """Emit a line without indentation."""
        self.output.append(line)

    def transpile(self: Any, nodes: List[A.ASTNode], source_file: str = "") -> str:
        """Transpile AST nodes to C code."""
        from runtime.phases import Phase

        self._check_decisions = []
        self._check_summary = {}
        self._format_decisions = []
        self._format_summary = {}
        self._array_len_hints = {}

        # Store source file path for import resolution
        self._source_file = source_file
        # Process imports first - inline imported modules via the
        # ImportResolver service.
        with Phase("transpile.imports"):
            from transpiler.import_resolver import ImportResolver

            nodes = ImportResolver().run(nodes, source_file)
        self._function_nodes = {
            node.name: node for node in nodes if isinstance(node, A.Function)
        }
        # Collect user-defined function names first (for name mangling).
        # Pre-assign profile indices in source order so that main() can
        # emit the full name table even when it appears LAST in the
        # file (the typical pattern). Without the pre-pass, the name
        # population baked into main's body would only see helper
        # functions if main is defined first, which it usually isn't.
        for node in nodes:
            if isinstance(node, A.Function):
                self.user_defined_funcs.add(node.name)
                if (
                    self.profile_enabled
                    and node.name != "main"
                    and node.name not in self._profile_func_index
                ):
                    self._profile_func_index[node.name] = len(self._profile_func_index)
            elif isinstance(node, A.GenericFunction):
                self._monomorphizer.register_generic(node)
            elif isinstance(node, A.ExternFn):
                # Register extern functions so call sites know their types
                self.user_defined_funcs.add(node.name)
                param_types = [pt for _, pt in node.params]
                self.functions[node.name] = (param_types, node.ret_type)
            elif isinstance(node, A.ExternVar):
                # Track extern vars for code generation
                self.extern_vars[node.name] = node.var_type
        self._function_c_symbols = {
            node.name: c_symbol_for_function(
                node.name,
                getattr(node, "decorators", []),
                self._default_mangle_name,
            )
            for node in nodes
            if isinstance(node, A.Function)
        }
        # First pass: collect type info (includes enums, records, classes,
        # function signatures, recursive-function set) via TypeCollector
        # service. Mixin path (self._collect_types) is dead code now.
        with Phase("transpile.collect_types"):
            from transpiler.type_collector import TypeCollector

            TypeCollector().run(nodes, self.type_info)
            self._type_aliases = dict(self.type_info.type_aliases)
        # Collect string variables for type inference (needs enums from above)
        with Phase("transpile.collect_string_vars"):
            from transpiler.var_typing_scanner import VarTypingScanner

            VarTypingScanner().run(nodes, self.type_info)
        with Phase("transpile.virtual_strings"):
            from transpiler.virtual_string_analysis import (
                analyze_virtual_string_materialization,
            )

            (
                self._virtual_string_length_only_fields,
                self._virtual_string_elidable_params,
            ) = analyze_virtual_string_materialization(nodes, self.classes)
        # Interval/range proof pass used by safety-check elision.
        with Phase("transpile.range_facts"):
            from transpiler.range_facts import RangeFactsAnalyzer

            self.range_facts = RangeFactsAnalyzer().run(nodes)
        # Scan for used helpers via the HelperScanner service. The
        # mixin path (self._scan_for_helpers) is dead code now and will
        # be deleted in stage C.
        with Phase("transpile.helper_scan"):
            from transpiler.helper_scanner import HelperScanner

            self.runtime_needs = HelperScanner(
                functions=self.functions,
                array_vars=self._array_vars,
                dict_vars=self._dict_vars,
                dyn_array_vars=self._dyn_array_vars,
                classes=self.classes,
                is_owned_string_alloc=self._is_owned_string_alloc,
                is_string_expr=self._is_string_expr_for_scan,
                can_elide_binary_safety=self._can_elide_binary_safety,
            ).run(nodes)
        # Collect all globally referenced names (for dead-const elimination)
        with Phase("transpile.collect_used_names"):
            self._globally_used_names = self._collect_globally_used_names(nodes)
        # Track which globals are const (for reassignment checks)
        self._const_global_names = {
            n.var_name for n in nodes if isinstance(n, A.VarDecl) and n.is_const
        }
        # Track static (mutable) globals so functions assign to them
        # instead of creating local shadows
        self._static_global_names = {
            n.var_name for n in nodes if isinstance(n, A.VarDecl) and not n.is_const
        }
        # Recursion detection now happens inside TypeCollector.run()
        # above; ``self.type_info.recursive_funcs`` (alias:
        # ``self._recursive_funcs``) is populated by that pass. Only
        # cycle members need the runtime recursion-depth guard;
        # everyone else skips both the entry check and the per-return
        # wrapper -- collapses ~3 lines per Return site in graph-shaped
        # programs like adapt_serve.
        # Generate header + prologue (includes, types, forward decls)
        # via the PrologueEmitter service. The legacy mixin is dead
        # code now and gets deleted in stage C.
        with Phase("transpile.emit_prologue"):
            from transpiler.prologue_emitter import PrologueEmitter

            prologue = PrologueEmitter(
                type_info=self.type_info,
                runtime_needs=self.runtime_needs,
                user_defined_funcs=self.user_defined_funcs,
                output=self.output,
                ailang_type_to_c=self._ailang_type_to_c,
                mangle_name=self._mangle_name,
                get_return_type=self._get_return_type,
                format_params=self._format_params,
                class_new_signature=self._class_new_signature,
                format_declaration=self._format_c_declaration,
            )
            # Methods are still ``_emit_*`` from the mixin extraction;
            # they're callable from the orchestrator the same way.
            # Public renames are a future cleanup that won't change
            # output bytes.
            prologue._emit_header(source_file)
            prologue._emit_cinclude_directives(nodes)
            prologue._emit_link_directives(nodes)
            # Dynamic-collection typedefs BEFORE user-type emission so
            # ``class Foo: arr: array`` finds ``ailang_dyn_array``
            # already declared. Function definitions for those shapes
            # come later via RuntimeEmitter, gated on usage.
            prologue._emit_dynamic_collection_typedefs()
            prologue._emit_type_definitions()
            prologue._emit_forward_declarations(nodes)
        # Generate only needed runtime helpers via RuntimeEmitter.
        with Phase("transpile.emit_runtime"):
            from transpiler.runtime_emitter import RuntimeEmitter

            RuntimeEmitter(
                runtime_needs=self.runtime_needs,
                functions=self.functions,
                user_defined_funcs=self.user_defined_funcs,
                ailang_type_to_c=self._ailang_type_to_c,
                output=self.output,
            ).run()
        # Emit template blocks (raw C code)
        with Phase("transpile.emit_templates"):
            prologue._emit_template_blocks(nodes)
        # Generate code (visit all nodes)
        with Phase("transpile.emit_user_code"):
            for node in nodes:
                self.visit(node)
        # Post-process: add AILANG_UNUSED to static function definitions
        # that don't already have it, to suppress -Wunused-function warnings
        with Phase("transpile.postprocess"):
            self._postprocess_unused_static()
        return "\n".join(self.output) + "\n"

    def _postprocess_unused_static(self: Any) -> None:
        """Add AILANG_UNUSED attribute to all static function definitions.
        This prevents -Wunused-function warnings when compiling with
        -Wall -Wextra -Werror. Static helpers are emitted based on
        used_helpers detection, but some may still be unused in specific
        programs (e.g. safe_array_set emitted but only get is called).
        """
        import re

        # Match any static function definition: static [qualifiers] type name(
        # But NOT: static inline (already has attributes)
        # And NOT: lines already having AILANG_UNUSED
        # And NOT: static variable declarations (no parenthesis before =)
        pattern = re.compile(r"^static\s+(?!inline\b).*\w+\s*\(")
        for i, line in enumerate(self.output):
            stripped = line.lstrip()
            if (
                stripped.startswith("static ")
                and "AILANG_UNUSED" not in stripped
                and "static inline" not in stripped
                and pattern.match(stripped)
                # Exclude variable declarations: static type var = ...;
                # by checking there's a '(' before any '=' on the line
                and "(" in stripped.split("=")[0]
            ):
                self.output[i] = line.replace("static ", "AILANG_UNUSED static ", 1)

    @staticmethod
    def _emit_fn_call(fn_args: list, ret_type: str) -> str:
        """Generate C cast for fn_call/fn_call_str builtins."""
        param_types = ", ".join(["int64_t"] * (len(fn_args) - 1))
        call_args = ", ".join(fn_args[1:])
        ptr = fn_args[0]
        return f"(({ret_type}(*)({param_types}))" f"(uintptr_t)({ptr}))({call_args})"

    def _emit_typed_fn_ptr(self: Any, fn_args: list) -> str:
        """Generate typed C function-pointer construction."""
        if len(fn_args) == 1:
            return f"(int64_t)(uintptr_t)&{fn_args[0][1:-1]}"
        alias = fn_args[1].strip('"')
        if resolve_callback_alias(alias, getattr(self, "_type_aliases", {})) is None:
            raise ValueError(f"fn_ptr() unknown callback alias {alias!r}")
        return f"(({alias})&{fn_args[0][1:-1]})"

    def _emit_typed_fn_call(self: Any, fn_args: list, ret_type: str) -> str:
        """Generate typed C callback invocation when an alias string is supplied."""
        if (
            len(fn_args) >= 2
            and fn_args[1].startswith('"')
            and fn_args[1].endswith('"')
        ):
            alias = fn_args[1].strip('"')
            spec = resolve_callback_alias(alias, getattr(self, "_type_aliases", {}))
            if spec is None:
                raise ValueError(f"fn_call() unknown callback alias {alias!r}")
            params, _callback_ret, _decorators = callback_parts(spec)
            if len(fn_args) - 2 != len(params):
                raise ValueError(
                    f"fn_call() alias {alias!r} expects {len(params)} argument(s)"
                )
            call_args = ", ".join(fn_args[2:])
            return f"(({alias})(uintptr_t)({fn_args[0]}))({call_args})"
        return self._emit_fn_call(fn_args, ret_type)

    def _extern_record_layout(self: Any, type_name: str) -> dict[str, Any] | None:
        """Return generated imported-record ABI layout metadata, if present."""
        type_spec = self._resolve_type_alias_spec(type_name)
        layouts = getattr(self.type_info, "extern_record_layouts", {})
        layout = layouts.get(type_spec)
        if isinstance(layout, dict):
            return layout
        return None

    def _emit_sizeof(self: Any, arg: str) -> str:
        """Emit sizeof for a type name string or expression."""
        # If arg is a quoted string like "int", strip quotes and map to C type
        stripped = arg.strip()
        if stripped.startswith('"') and stripped.endswith('"'):
            type_name = stripped[1:-1]
            type_spec = self._resolve_type_alias_spec(type_name)
            layout = self._extern_record_layout(type_spec)
            if layout is not None:
                return f"((int64_t){int(layout.get('size', 0))})"
            fixed = self._parse_fixed_array_type_spec(type_spec)
            if fixed is not None:
                elem_type, size = fixed
                c_elem = self._ailang_type_to_c(elem_type)
                return f"((int64_t)sizeof({c_elem}[{size}]))"
            c_type = self._ailang_type_to_c(type_name)
            return f"((int64_t)sizeof({c_type}))"
        return f"((int64_t)sizeof({arg}))"

    def _emit_alignof(self: Any, arg: str) -> str:
        """Emit alignof for a type name string or expression."""
        stripped = arg.strip()
        if stripped.startswith('"') and stripped.endswith('"'):
            type_name = stripped[1:-1]
            type_spec = self._resolve_type_alias_spec(type_name)
            layout = self._extern_record_layout(type_spec)
            if layout is not None:
                return f"((int64_t){int(layout.get('align', 0))})"
            fixed = self._parse_fixed_array_type_spec(type_spec)
            if fixed is not None:
                elem_type, size = fixed
                c_elem = self._ailang_type_to_c(elem_type)
                return f"((int64_t)_Alignof({c_elem}[{size}]))"
            c_type = self._ailang_type_to_c(type_name)
            return f"((int64_t)_Alignof({c_type}))"
        return f"((int64_t)_Alignof({arg}))"

    def _emit_offsetof(self: Any, type_name: str, field_name: str) -> str:
        """Emit offsetof for a record/union type and field name."""
        if not type_name.strip():
            raise ValueError("offsetof() type name cannot be empty")
        if not field_name.isidentifier():
            raise ValueError(f"offsetof() invalid field name: {field_name!r}")
        type_spec = self._resolve_type_alias_spec(type_name)
        fixed = self._parse_fixed_array_type_spec(type_spec)
        if fixed is not None:
            raise ValueError("offsetof() expects a record or union type")
        layout = self._extern_record_layout(type_spec)
        if layout is not None:
            fields = layout.get("fields", {})
            field_layout = fields.get(field_name) if isinstance(fields, dict) else None
            if not isinstance(field_layout, dict):
                raise ValueError(
                    f"offsetof() field {field_name!r} not found in imported record "
                    f"{type_spec!r}"
                )
            return f"((int64_t){int(field_layout.get('offset', 0))})"
        c_type = self._ailang_type_to_c(type_name)
        return f"((int64_t)offsetof({c_type}, {field_name}))"

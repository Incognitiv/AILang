"""CodeGen module/global declaration mixin."""

from __future__ import annotations

from parser.ast import (
    ASTNode,
    BinaryOp,
    Call,
    ClassDef,
    EnumDef,
    FromImport,
    Function,
    InterpolatedString,
    LinkDirective,
    RecordDef,
    StringLit,
    StringSlice,
    TypeAlias,
    Use,
    VarDecl,
)
from typing import Any

from abi_symbols import c_symbol_for_function, has_export_decorator
from ast_access import arg_at
from calling_conventions import llvm_calling_convention, normalized_decorators
from llvmlite import ir
from target_info import os_from_triple, target_matches

from .codegen_module_globals_mixin import _CodeGenModuleGlobalsMixin


class _CodeGenModuleMixin(_CodeGenModuleGlobalsMixin):
    def __init__(self: Any) -> None:
        # Linter-only declaration for compile-source tracking.
        self._compile_source_file = ""

    def _process_module_import(
        self: Any,
        module_name: str,
        module: Any,
        from_import_names: dict[str, list[str]],
    ) -> None:
        """Process module import for records and enums."""
        if module_name.startswith("__from__"):
            requested_names = from_import_names.get(module_name, [])
            exports = module.get_all_exports()
            for name in requested_names:
                if name not in exports:
                    continue
                self._generate_export_type(exports[name])
        else:
            for node in module.get_all_exports().values():
                self._generate_export_type(node)

    def _generate_export_type(self: Any, node: Any) -> None:
        """Generate code for an exported record, enum, class, or constant."""
        from parser.ast import Assign

        if isinstance(node, TypeAlias):
            self.type_aliases[node.name] = node.target_type
        elif isinstance(node, RecordDef):
            if node.name not in self.record_types:
                self.generate_record(node)
        elif isinstance(node, EnumDef):
            if node.name not in self.enum_values:
                self.generate_enum(node)
        elif isinstance(node, ClassDef):
            if node.name not in self.class_types:
                self.generate_class(node)
        elif isinstance(node, VarDecl):
            # Handle imported constants
            self.generate_global_var(node)
        # Handle imported mutable globals (library module state)
        elif isinstance(node, Assign) and (node.var_name not in self.globals):
            self.generate_global_assign(node)

    def _collect_imported_functions(
        self: Any,
        module_name: str,
        module: Any,
        from_import_names: dict[str, list[str]],
    ) -> list[Function]:
        """Collect and declare imported functions from a module."""
        result: list[Function] = []
        seen = set()  # Track already-collected function names
        if module_name.startswith("__from__"):
            requested_names = from_import_names.get(module_name, [])
            exports = module.get_all_exports()
            for name in requested_names:
                if name not in exports:
                    continue
                node = exports[name]
                if (
                    isinstance(node, Function) and node.name not in self.functions
                ) and (node.name not in seen):
                    seen.add(node.name)
                    result.append(node)
                    self.declare_function(node)
        else:
            for node in module.get_all_exports().values():
                if (
                    isinstance(node, Function) and node.name not in self.functions
                ) and (node.name not in seen):
                    seen.add(node.name)
                    result.append(node)
                    self.declare_function(node)
        return result

    def _register_type_aliases_from_nodes(self: Any, nodes: Any) -> None:
        """Collect aliases before lowering records/functions that use them."""
        for node in nodes:
            if isinstance(node, TypeAlias):
                self.type_aliases[node.name] = node.target_type

    def _module_needs_string_arena(self: Any, nodes: Any) -> bool:
        """Return True when the module contains operations that allocate strings."""
        from parser import ast as A

        from codegen.strlen_scalarization import collect_length_only_str_locals

        allocating_calls = {
            "input",
            "read_stdin",
            "read_file",
            "substr",
            "substring",
            "string_concat",
        }

        def is_length_expr(value: Any) -> bool:
            return isinstance(value, (Call, InterpolatedString)) and (
                isinstance(value, InterpolatedString)
                or value.name in {"str", "hex", "bin", "oct", "read_file"}
            )

        def length_expr_children_need_arena(value: Any, length_only: set[str]) -> bool:
            if isinstance(value, InterpolatedString):
                return any(
                    expr_needs_arena(part, length_only)
                    for part in value.parts
                    if not isinstance(part, str)
                )
            if isinstance(value, Call):
                return any(expr_needs_arena(arg, length_only) for arg in value.args)
            return False

        def expr_needs_arena(value: Any, length_only: set[str]) -> bool:
            if value is None:
                return False
            if isinstance(value, (A.Assign, A.VarDecl)):
                var_name = getattr(value, "var_name", "")
                expr = getattr(value, "value", None)
                if expr is None:
                    expr = getattr(value, "init_value", None)
                if var_name in length_only and is_length_expr(expr):
                    return length_expr_children_need_arena(expr, length_only)
                return expr_needs_arena(expr, length_only)
            if (
                isinstance(value, Call)
                and value.name in {"len", "strlen"}
                and value.args
            ):
                first = arg_at(value, 0)
                if is_length_expr(first):
                    return length_expr_children_need_arena(first, length_only)
            if isinstance(value, InterpolatedString):
                return True
            if isinstance(value, StringSlice):
                return True
            if isinstance(value, Call):
                if value.name in {"arena_use", "arena_destroy"}:
                    return True
                if value.name in allocating_calls:
                    return True
                if value.name in {"str", "hex", "bin", "oct"}:
                    return True
            if isinstance(value, BinaryOp) and value.op in {"+", "plus"}:
                if isinstance(value.left, StringLit) or isinstance(
                    value.right, StringLit
                ):
                    return True
            return False

        for top in nodes:
            if isinstance(top, Function):
                length_only = collect_length_only_str_locals(top.body or [])
                for stmt in top.body or []:
                    if expr_needs_arena(stmt, length_only):
                        return True
                continue
            if expr_needs_arena(top, set()):
                return True
        return False

    def _collect_fn_ptr_references(self: Any, nodes: Any) -> set[str]:
        """Return function names whose address is taken through fn_ptr()."""
        names: set[str] = set()
        for node in self._walk_ast_nodes(nodes):
            if not isinstance(node, Call) or node.name != "fn_ptr":
                continue
            if not node.args:
                continue
            target = arg_at(node, 0)
            if isinstance(target, StringLit):
                names.add(target.value)
        return names

    def _module_uses_argc_argv(self: Any, nodes: Any) -> bool:
        """Return True when argc()/argv() are actually referenced."""
        return any(
            isinstance(node, Call) and node.name in {"argc", "argv"}
            for node in self._walk_ast_nodes(nodes)
        )

    def generate(self: Any, ast_nodes: list[ASTNode], source_file: str = "") -> str:
        """Generate IR for all functions, records, classes, and enums"""
        # Process imports first.  Language-level runtime semantics are pure
        # AILang modules selected from semantic type usage, never handwritten
        # backend-language runtimes.
        from compiler.modules import get_loader, process_imports
        from compiler.runtime_modules import ensure_language_runtime_imports
        from runtime.stdlib import is_std_module

        ast_nodes = ensure_language_runtime_imports(ast_nodes)

        # Stash for the source_map fallback in generate_function (top-level
        # functions don't get _source_path stamped by modules.py).
        self._compile_source_file = source_file
        ast_nodes, imported_modules = process_imports(ast_nodes, source_file)
        self._function_nodes = {
            node.name: node for node in ast_nodes if isinstance(node, Function)
        }
        # Process 'use' statements - either standard library or user libraries
        for node in ast_nodes:
            if isinstance(node, Use):
                self._process_use_statement(
                    node,
                    source_file,
                    is_std_module=is_std_module,
                    get_loader=get_loader,
                    imported_modules=imported_modules,
                )
        # Build a map of from-imports for selective symbol lookup
        # Accumulate names to handle multiple from-imports from the same module
        from_import_names: dict[str, list[str]] = {}
        for node in ast_nodes:
            if isinstance(node, FromImport):
                key = f"__from__{node.module_path}"
                if key not in from_import_names:
                    from_import_names[key] = []
                from_import_names[key].extend(node.names)

        # Parser-level inference deliberately defers functions whose return
        # values depend on imports.  At this point the LLVM module loader has
        # the complete exported symbol closure, so infer on concrete nodes.
        from parser.return_type_inference import (
            infer_unannotated_return_types,
            validate_return_contracts,
        )

        inference_nodes: list[ASTNode] = [
            node for node in ast_nodes if not isinstance(node, FromImport)
        ]
        seen_inference_nodes = {id(node) for node in inference_nodes}
        for imported_module in imported_modules.values():
            for imported_node in imported_module.get_all_exports().values():
                if id(imported_node) in seen_inference_nodes:
                    continue
                inference_nodes.append(imported_node)
                seen_inference_nodes.add(id(imported_node))
        infer_unannotated_return_types(inference_nodes)
        validate_return_contracts(inference_nodes)

        # Aliases must be known before records/classes/functions are lowered.
        self._register_type_aliases_from_nodes(ast_nodes)
        for module in imported_modules.values():
            self._register_type_aliases_from_nodes(module.get_all_exports().values())
        # Import symbols from loaded modules
        for module_name, module in imported_modules.items():
            self._process_module_import(module_name, module, from_import_names)
        # PERFORMANCE OPTIMIZATION: Single pass with cached type checks
        # Eliminates duplicate loop and repeated isinstance() calls
        functions: list[Function] = []
        imported_functions: list[Function] = []
        # Pass 1: Types and declarations
        for node in ast_nodes:
            func = self._process_ast_node(node)
            if func is not None:
                functions.append(func)
        # Declare imported functions
        for module_name, module in imported_modules.items():
            funcs = self._collect_imported_functions(
                module_name, module, from_import_names
            )
            imported_functions.extend(funcs)
        self._function_nodes.update({func.name: func for func in imported_functions})
        # Build range/interval facts once for proof-driven arithmetic flags.
        # Include imported function ASTs too so their bodies can be proven.
        from transpiler.range_facts import RangeFactsAnalyzer

        analysis_nodes = list(ast_nodes)
        analysis_nodes.extend(imported_functions)
        self._fn_ptr_function_names = self._collect_fn_ptr_references(analysis_nodes)
        self._module_uses_program_args = self._module_uses_argc_argv(analysis_nodes)
        from transpiler.virtual_string_analysis import (
            analyze_virtual_string_materialization,
        )

        analysis_classes = {
            name: (self.class_fields.get(name, []), self.class_methods.get(name, []))
            for name in self.class_types
        }
        (
            self._virtual_string_length_only_fields,
            self._virtual_string_elidable_params,
        ) = analyze_virtual_string_materialization(analysis_nodes, analysis_classes)
        self.range_facts = RangeFactsAnalyzer().run(analysis_nodes)
        self._module_uses_string_arena = self._module_needs_string_arena(analysis_nodes)
        self._recursive_functions = self._find_recursive_functions(
            [*imported_functions, *functions]
        )
        self._recursion_guard_elided = self._find_recursion_guard_elisions(
            [*imported_functions, *functions]
        )
        # Pass 2: Function bodies (use cached list - no isinstance needed!)
        for func in imported_functions:
            self.generate_function(func)
        for func in functions:
            self.generate_function(func)
        # Pass 3: Generate any specialized generic types created during codegen
        for specialized in self.monomorphizer.get_specialized_definitions():
            if isinstance(specialized, RecordDef):
                self.generate_record(specialized)
            elif isinstance(specialized, ClassDef):
                self.generate_class(specialized)
            # Generic function specialization - declare + generate if not done
            elif isinstance(specialized, Function) and (
                specialized.name not in self.functions
            ):
                self.declare_function(specialized)
                self.generate_function(specialized)
        ir_string = str(self.module)
        if self.personality_func:
            ir_string = self._patch_personality_functions(ir_string)
        # Merge template IRs (foreign code blocks) into final IR
        if self.template_irs:
            from .templates import template_compiler

            # Strip forward-declarations for template functions to avoid
            # "invalid redefinition" when the merged template IR provides
            # the actual define.
            ir_string = self._strip_template_declares(ir_string)
            ir_string = template_compiler.merge_ir(ir_string, self.template_irs)
        link_directives = self._collect_link_directives(ast_nodes, imported_modules)
        if link_directives:
            link_comments = "\n".join(
                f"; AILANG_LINK: {flags}" for flags in link_directives
            )
            ir_string = f"{link_comments}\n{ir_string}"
        return ir_string

    def _collect_link_directives(
        self: Any, ast_nodes: list[ASTNode], imported_modules: dict[str, Any]
    ) -> list[str]:
        """Collect explicit #link payloads for LLVM AOT build tools."""
        result: list[str] = []
        seen: set[str] = set()
        module = getattr(self, "module", None)
        current_os = os_from_triple(getattr(module, "triple", ""))

        def add(flags: str, target_os: str | None = None) -> None:
            if not target_matches(target_os, current_os):
                return
            flags = (flags or "").strip()
            if not flags or flags in seen:
                return
            seen.add(flags)
            result.append(flags)

        for node in ast_nodes:
            if isinstance(node, LinkDirective):
                add(node.flags, getattr(node, "target_os", None))
        for module in imported_modules.values():
            for directive in getattr(module, "link_directives", []):
                if isinstance(directive, LinkDirective):
                    add(directive.flags, getattr(directive, "target_os", None))
                else:
                    add(str(directive))
        return result

    def _patch_personality_functions(self: Any, ir_string: str) -> str:
        """Post-process IR to add personality attributes for exception handling."""
        lines = ir_string.split("\n")
        result_lines = []
        for i, line in enumerate(lines):
            if (
                line.startswith("define ") and "()" in line
            ) and self._function_has_exception_handling(lines, i + 1):
                line = line.replace(
                    "()", "() personality i32 (...)* @__gxx_personality_v0", 1
                )
            result_lines.append(line)
        return "\n".join(result_lines)

    def _function_has_exception_handling(
        self: Any, lines: list[str], start: int
    ) -> bool:
        """Check if function body contains exception handling constructs."""
        brace_depth = 0
        for j in range(start, len(lines)):
            brace_depth += lines[j].count("{") - lines[j].count("}")
            if brace_depth <= 0:
                break
            if "landingpad" in lines[j] or "invoke" in lines[j]:
                return True
        return False

    def _strip_template_declares(self: Any, ir_string: str) -> str:
        """Remove forward-declarations of template functions from AILang IR.
        When template code is merged, its 'define' would conflict with the
        'declare' that was emitted during Pass 1.  Strip those declares so
        the template's define is the only copy.
        """
        if not self.template_irs:
            return ir_string
        # Collect all function names defined in template IRs
        template_func_names: set[str] = set()
        for tir in self.template_irs:
            for line in tir.split("\n"):
                stripped = line.strip()
                if stripped.startswith("define") and "@" in stripped:
                    name = stripped.split("@", 1)[1].split("(", 1)[0]
                    template_func_names.add(name)
        if not template_func_names:
            return ir_string
        # Filter out declare lines for template functions
        lines = ir_string.split("\n")
        result: list[str] = []
        for line in lines:
            if line.startswith("declare") and "@" in line:
                # Extract name, stripping quotes (llvmlite emits @"name")
                raw_name = line.split("@", 1)[1].split("(", 1)[0]
                clean_name = raw_name.strip('"')
                if clean_name in template_func_names:
                    continue  # Skip this declare
            result.append(line)
        return "\n".join(result)

    def declare_function(self: Any, node: Any) -> None:
        """Declare function signature with optimized LLVM attributes"""
        # Skip if already declared (transitive imports can re-export same function)
        decorators = normalized_decorators(getattr(node, "decorators", []))
        func_name = c_symbol_for_function(
            node.name,
            getattr(node, "decorators", []),
            lambda name: f"_{name}" if not getattr(node, "is_public", True) else name,
        )
        is_private = not getattr(node, "is_public", True)
        if is_private:
            func_name = f"_{node.name}"
        if func_name in self.functions:
            return
        param_types: list[Any] = []
        param_type_strs: list[str] = []  # Keep original types for attribute decisions
        for param_info in node.params:
            # Support both old (name, type) and new (name, type, default) format
            if len(param_info) == 2:
                param_name, param_type = param_info
            else:
                param_name, param_type, _ = param_info
            param_types.append(self.get_llvm_type(param_type))
            type_str = param_type if isinstance(param_type, str) else str(param_type)
            param_type_strs.append(type_str)
            # Track class type annotations for method resolution (Option 2: explicit types)
            # PascalCase types are assumed to be class types
            if (
                isinstance(param_type, str)
                and param_type
                and param_type[0].isupper()
                and not param_type.isupper()
            ):
                self.param_class_types[(node.name, param_name)] = param_type
        ret_type = self.get_llvm_type(getattr(node, "return_type", "i64"))
        if node.name == "main" and not node.params:
            # The language-level main may infer ``void`` when it has no value
            # return, but the native process ABI is always ``int main(...)``.
            # Keep that ABI detail out of source-level return inference.
            ret_type = ir.IntType(32)
            param_types = [
                ir.IntType(32),
                ir.IntType(8).as_pointer().as_pointer(),
            ]
        fnty = ir.FunctionType(ret_type, param_types)
        # Public module visibility is not the same thing as native ABI export.
        # Keep helpers internal by default so LLVM can inline and dead-strip them;
        # users opt into a stable external symbol with @export.
        func = ir.Function(self.module, fnty, name=func_name)
        if is_private or (
            node.name != "main"
            and not has_export_decorator(getattr(node, "decorators", []))
        ):
            func.linkage = "internal"
        callconv = llvm_calling_convention(decorators)
        if callconv:
            func.calling_convention = callconv
        # ===== CRITICAL OPTIMIZATION: Add function attributes =====
        # These enable LLVM to optimize more aggressively
        # nounwind: Function doesn't throw exceptions (enables better codegen)
        func.attributes.add("nounwind")
        # Add parameter attributes for pointers (enables alias analysis)
        for i, (llvm_type, type_str) in enumerate(
            zip(param_types, param_type_strs, strict=False)
        ):
            if isinstance(llvm_type, ir.PointerType):
                # String parameters are read-only and don't alias
                if type_str in ("string", "str"):
                    func.args[i].add_attribute("noalias")
                    # llvmlite 0.49 no longer accepts nocapture through the
                    # high-level ArgumentAttributes API. It is an optimization
                    # hint only, so retain the semantic attributes and omit it.
                    func.args[i].add_attribute("nonnull")
                else:
                    pass
        # Store under original name for lookup
        self.functions[node.name] = func
        # Store default argument info for call-time resolution
        self.function_defaults[node.name] = [
            (i, default)
            for i, param_info in enumerate(node.params)
            if len(param_info) == 3 and param_info[2] is not None
            for default in [param_info[2]]
        ]

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
from calling_conventions import llvm_calling_convention, normalized_decorators
from llvmlite import ir
from target_info import os_from_triple, target_matches


class _CodeGenModuleMixin:
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
        allocating_calls = {
            "input",
            "read_stdin",
            "read_file",
            "str",
            "substr",
            "substring",
            "string_concat",
        }

        def walk(value: Any):
            if isinstance(value, ASTNode):
                yield value
                for child in vars(value).values():
                    yield from walk(child)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from walk(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from walk(item)

        for node in walk(list(nodes)):
            if isinstance(node, (InterpolatedString, StringSlice)):
                return True
            if isinstance(node, Call) and node.name in allocating_calls:
                return True
            if isinstance(node, BinaryOp) and node.op in {"+", "plus"}:
                if isinstance(node.left, StringLit) or isinstance(
                    node.right, StringLit
                ):
                    return True
        return False

    def generate(self: Any, ast_nodes: list[ASTNode], source_file: str = "") -> str:
        """Generate IR for all functions, records, classes, and enums"""
        # Process imports first
        from compiler.modules import get_loader, process_imports
        from runtime.stdlib import is_std_module

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
            # Native executables need the host ABI main signature so argc()/argv()
            # can work in LLVM AOT the same way they do in the C backend.
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
                    func.args[i].add_attribute("nocapture")
                    func.args[i].add_attribute("nonnull")
                # Other pointers are just nocapture (don't escape)
                else:
                    func.args[i].add_attribute("nocapture")
        # Store under original name for lookup
        self.functions[node.name] = func
        # Store default argument info for call-time resolution
        self.function_defaults[node.name] = [
            (i, default)
            for i, param_info in enumerate(node.params)
            if len(param_info) == 3 and param_info[2] is not None
            for default in [param_info[2]]
        ]

    def generate_global_var(self: Any, node: VarDecl) -> None:
        """Generate a global variable declaration at module level.
        Creates an LLVM GlobalVariable with the specified type and initial value.
        Global variables are stored in self.globals for lookup during codegen.
        """
        # Skip if already defined (e.g., same global imported from multiple modules)
        if node.var_name in self.globals:
            return
        llvm_type = self.get_llvm_type(node.type_name)
        # Set initializer based on type and init_value
        from parser.ast import ArrayLit, Bool, Number, StringLit

        # Handle array literals specially - they need array type, not scalar
        if isinstance(node.init_value, ArrayLit):
            self._generate_global_array(node.var_name, node.init_value, node.is_const)
            return
        # Create the global variable for scalar types
        global_var = ir.GlobalVariable(self.module, llvm_type, node.var_name)
        # Use internal linkage for all globals in JIT mode
        # Public just affects visibility in module system, not LLVM linkage
        global_var.linkage = "internal"
        global_var.global_constant = node.is_const
        if isinstance(node.init_value, Number):
            # Check if target type is floating point
            is_float_type = isinstance(
                llvm_type, (ir.FloatType, ir.DoubleType, ir.HalfType)
            )
            if is_float_type or node.init_value.is_float:
                global_var.initializer = ir.Constant(
                    llvm_type, float(node.init_value.value)
                )
            else:
                global_var.initializer = ir.Constant(
                    llvm_type, int(node.init_value.value)
                )
        elif isinstance(node.init_value, Bool):
            global_var.initializer = ir.Constant(
                llvm_type, 1 if node.init_value.value else 0
            )
        elif isinstance(node.init_value, StringLit):
            # For strings, create a global string constant and point to it
            str_const = self.create_string_constant(node.init_value.value)
            global_var.initializer = str_const
        else:
            # Default to zero initializer for complex expressions
            # (Would need runtime init for computed values)
            if isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
                global_var.initializer = ir.Constant(llvm_type, 0.0)
            else:
                global_var.initializer = ir.Constant(llvm_type, 0)
        # Register in globals dict for lookup
        self.globals[node.var_name] = global_var

    def _generate_global_array(
        self: Any, var_name: str, array_lit: Any, is_const: bool = False
    ) -> None:
        """Generate a global array from an ArrayLit AST node."""
        from parser.ast import Bool, Number, StringLit

        if not array_lit.elements:
            # Empty array
            elem_type = ir.IntType(64)
            array_type = ir.ArrayType(elem_type, 0)
            global_var = ir.GlobalVariable(self.module, array_type, var_name)
            global_var.initializer = ir.Constant(array_type, [])
            global_var.linkage = "internal"
            global_var.global_constant = is_const
            self.globals[var_name] = global_var
            self.array_metadata[var_name] = (0, elem_type)
            return
        # Determine element type from first element
        first_elem = array_lit.elements[0]
        if isinstance(first_elem, Number):
            elem_type = ir.DoubleType() if first_elem.is_float else ir.IntType(64)
        elif isinstance(first_elem, Bool):
            elem_type = ir.IntType(1)
        elif isinstance(first_elem, StringLit):
            elem_type = ir.IntType(8).as_pointer()
        else:
            elem_type = ir.IntType(64)  # Default
        array_len = len(array_lit.elements)
        array_type = ir.ArrayType(elem_type, array_len)
        # Build initializer values
        init_values = []
        for elem in array_lit.elements:
            if isinstance(elem, Number):
                if isinstance(elem_type, ir.DoubleType):
                    init_values.append(ir.Constant(elem_type, float(elem.value)))
                else:
                    init_values.append(ir.Constant(elem_type, int(elem.value)))
            elif isinstance(elem, Bool):
                init_values.append(ir.Constant(elem_type, 1 if elem.value else 0))
            elif isinstance(elem, StringLit):
                str_const = self.create_string_constant(elem.value)
                init_values.append(str_const)
            else:
                # Default to zero for complex expressions
                init_values.append(ir.Constant(elem_type, 0))
        global_var = ir.GlobalVariable(self.module, array_type, var_name)
        global_var.initializer = ir.Constant(array_type, init_values)
        global_var.linkage = "internal"
        global_var.global_constant = is_const
        self.globals[var_name] = global_var
        self.array_metadata[var_name] = (array_len, elem_type)

    def generate_global_assign(self: Any, node: Any) -> None:
        """Generate a global assignment (for arrays and simple values).
        Handles global scope assignments like:
            arr = [1, 2, 3, 4, 5]
            SIZE = 100
        """
        from parser.ast import ArrayLit, Bool, Number, StringLit

        var_name = node.var_name
        value = node.value
        if isinstance(value, ArrayLit):
            # Global array - create as global constant array
            if not value.elements:
                # Empty array - create null pointer
                elem_type = ir.IntType(64)
                array_type = ir.ArrayType(elem_type, 0)
                global_var = ir.GlobalVariable(self.module, array_type, var_name)
                global_var.initializer = ir.Constant(array_type, [])
                global_var.linkage = "internal"
                self.globals[var_name] = global_var
                self.array_metadata[var_name] = (0, elem_type)
                return
            # Determine element type from first element
            first_elem = value.elements[0]
            if isinstance(first_elem, Number):
                elem_type = ir.DoubleType() if first_elem.is_float else ir.IntType(64)
            elif isinstance(first_elem, Bool):
                elem_type = ir.IntType(1)
            elif isinstance(first_elem, StringLit):
                elem_type = ir.IntType(8).as_pointer()
            else:
                elem_type = ir.IntType(64)  # Default
            array_len = len(value.elements)
            array_type = ir.ArrayType(elem_type, array_len)
            # Build initializer values
            init_values = []
            for elem in value.elements:
                if isinstance(elem, Number):
                    if isinstance(elem_type, ir.DoubleType):
                        init_values.append(ir.Constant(elem_type, float(elem.value)))
                    else:
                        init_values.append(ir.Constant(elem_type, int(elem.value)))
                elif isinstance(elem, Bool):
                    init_values.append(ir.Constant(elem_type, 1 if elem.value else 0))
                elif isinstance(elem, StringLit):
                    str_const = self.create_string_constant(elem.value)
                    init_values.append(str_const)
                else:
                    # Default to zero for complex expressions
                    init_values.append(ir.Constant(elem_type, 0))
            global_var = ir.GlobalVariable(self.module, array_type, var_name)
            global_var.initializer = ir.Constant(array_type, init_values)
            global_var.linkage = "internal"
            self.globals[var_name] = global_var
            self.array_metadata[var_name] = (array_len, elem_type)
        elif isinstance(value, Number):
            # Global scalar constant
            if value.is_float:
                llvm_type = ir.DoubleType()
                init_val = ir.Constant(llvm_type, float(value.value))
            else:
                llvm_type = ir.IntType(64)
                init_val = ir.Constant(llvm_type, int(value.value))
            global_var = ir.GlobalVariable(self.module, llvm_type, var_name)
            global_var.initializer = init_val
            global_var.linkage = "internal"
            self.globals[var_name] = global_var
        elif isinstance(value, Bool):
            llvm_type = ir.IntType(1)
            global_var = ir.GlobalVariable(self.module, llvm_type, var_name)
            global_var.initializer = ir.Constant(llvm_type, 1 if value.value else 0)
            global_var.linkage = "internal"
            self.globals[var_name] = global_var
        elif isinstance(value, StringLit):
            # Global string - create as i8* pointing to constant
            str_const = self.create_string_constant(value.value)
            llvm_type = ir.IntType(8).as_pointer()
            global_var = ir.GlobalVariable(self.module, llvm_type, var_name)
            global_var.initializer = str_const
            global_var.linkage = "internal"
            self.globals[var_name] = global_var

    def generate_enum(self: Any, node: Any) -> None:
        """Register enum values as constants, with support for data-carrying enums."""
        enum_name = node.name
        # Check if this enum has data-carrying variants
        has_data = (
            node.has_data_variants() if hasattr(node, "has_data_variants") else False
        )
        if has_data:
            # Data-carrying enum - create tagged union struct
            self._generate_data_enum(node)
        else:
            # Simple enum - store as integer constants
            for value_name, value_int in node.values:
                full_name = f"{enum_name}.{value_name}"
                self.enum_values[full_name] = value_int

    def _generate_data_enum(self: Any, node: Any) -> None:
        """Generate LLVM tagged union type for data-carrying enum."""
        enum_name = node.name
        variant_data: dict[str, list[tuple[str, str]]] = {}
        tag_values: dict[str, int] = {}
        # Collect variant information
        for idx, variant in enumerate(node.variants):
            tag_values[variant.name] = idx
            if variant.fields:
                variant_data[variant.name] = variant.fields
            else:
                variant_data[variant.name] = []
            # Also store simple enum value for backwards compatibility
            full_name = f"{enum_name}.{variant.name}"
            self.enum_values[full_name] = idx
        # Create union of all variant structs
        # Find the largest variant to determine union size
        max_size = 0
        variant_types: dict[str, ir.Type] = {}
        for variant_name, fields in variant_data.items():
            if fields:
                field_types = [self.get_llvm_type(ftype) for _, ftype in fields]
                variant_type = ir.LiteralStructType(field_types)
                variant_types[variant_name] = variant_type
                # Estimate size (rough - just count bytes)
                size = sum(self._type_size(ft) for ft in field_types)
                max_size = max(max_size, size)
            else:
                variant_types[variant_name] = ir.LiteralStructType([])
        # Create the tagged union struct: { i32 tag, [max_size x i8] data }
        # Using byte array for union data to handle different variant sizes
        tag_type = ir.IntType(32)
        data_size = max(max_size, 8)  # Minimum 8 bytes for data
        data_type = ir.ArrayType(ir.IntType(8), data_size)
        enum_struct = ir.LiteralStructType([tag_type, data_type])
        # Store in registries
        self.data_enums[enum_name] = variant_data
        self.data_enum_types[enum_name] = enum_struct
        self.data_enum_tags[enum_name] = tag_values
        # Also register as a record type for field access
        self.record_types[enum_name] = enum_struct

    def _type_size(self: Any, llvm_type: ir.Type) -> int:
        """Estimate size in bytes of an LLVM type."""
        if isinstance(llvm_type, ir.IntType):
            return (llvm_type.width + 7) // 8
        if isinstance(llvm_type, ir.FloatType):
            return 4
        if isinstance(llvm_type, ir.DoubleType):
            return 8
        if isinstance(llvm_type, ir.PointerType):
            return 8  # 64-bit pointers
        if isinstance(llvm_type, ir.ArrayType):
            return llvm_type.count * self._type_size(llvm_type.element)
        if isinstance(llvm_type, ir.LiteralStructType):
            return sum(self._type_size(e) for e in llvm_type.elements)
        return 8  # Default

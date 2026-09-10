from pathlib import Path


def replace_one(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one patch site, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


path = "source/compiler/modules.py"
replace_one(
    path,
    "        self.exports: dict[str, ASTNode] = {}\n        self.link_directives: list[LinkDirective] = []\n",
    "        self.exports: dict[str, ASTNode] = {}\n"
    "        # Public interface and implementation closure are separate.\n"
    "        self.implementation: dict[str, ASTNode] = {}\n"
    "        self.link_directives: list[LinkDirective] = []\n",
)
replace_one(
    path,
    '''            elif isinstance(node, Function):
                # Stamp source path for the profiler's func -> file:line map.
                node._source_path = self.path
                # Export all non-private functions
                if not node.name.startswith("_"):
                    self.exports[node.name] = node
            elif isinstance(node, (RecordDef, EnumDef)):
                self.exports[node.name] = node
            elif isinstance(node, ClassDef):
                # ClassDef methods get tagged too — they're emitted as
                # functions and end up in the same instrumentation path.
                node._source_path = self.path
                self.exports[node.name] = node
            elif isinstance(node, VarDecl):
                # Export all variables from library modules so imported functions
                # can reference their module's mutable state (e.g. counters, tables).
                # Non-library modules still only export const/public variables.
                if self.is_library or node.is_const or node.is_public:
                    self.exports[node.var_name] = node
            # Export bare assignments from library modules (e.g. _count = 0)
            # so imported functions can reference and mutate their module globals
            elif isinstance(node, Assign) and self.is_library:
                self.exports[node.var_name] = node
''',
    '''            elif isinstance(node, Function):
                # Stamp source path for the profiler's func -> file:line map.
                node._source_path = self.path
                self.implementation[node.name] = node
                if getattr(node, "is_public", True) and not node.name.startswith("_"):
                    self.exports[node.name] = node
            elif isinstance(node, (RecordDef, EnumDef)):
                self.implementation[node.name] = node
                self.exports[node.name] = node
            elif isinstance(node, ClassDef):
                # ClassDef methods get tagged too — they're emitted as
                # functions and end up in the same instrumentation path.
                node._source_path = self.path
                self.implementation[node.name] = node
                self.exports[node.name] = node
            elif isinstance(node, VarDecl):
                self.implementation[node.var_name] = node
                # Export all variables from library modules so imported functions
                # can reference their module's mutable state (e.g. counters, tables).
                # Non-library modules still only export const/public variables.
                if self.is_library or node.is_const or node.is_public:
                    self.exports[node.var_name] = node
            # Export bare assignments from library modules (e.g. _count = 0)
            # so imported functions can reference and mutate their module globals.
            elif isinstance(node, Assign) and self.is_library:
                self.implementation[node.var_name] = node
                self.exports[node.var_name] = node
''',
)
replace_one(
    path,
    '''    def get_all_exports(self) -> dict[str, ASTNode]:
        """Get all exported symbols"""
        return self.exports.copy()
''',
    '''    def get_all_exports(self) -> dict[str, ASTNode]:
        """Get the public source-level interface of this module."""
        return self.exports.copy()

    def get_all_implementation(self) -> dict[str, ASTNode]:
        """Get declarations required to lower this module's implementation."""
        return self.implementation.copy()
''',
)
replace_one(
    path,
    '''        exports = imported_module.exports
        names = list(exports) if requested_names is None else requested_names
''',
    '''        exports = imported_module.exports
        for name, node in imported_module.implementation.items():
            if name not in module.implementation:
                module.implementation[name] = node
        names = list(exports) if requested_names is None else requested_names
''',
)

path = "source/codegen/codegen_module_mixin.py"
replace_one(
    path,
    '''        result: list[Function] = []
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
''',
    '''        result: list[Function] = []
        seen: set[str] = set()
        if module_name.startswith("__from__"):
            requested_names = from_import_names.get(module_name, [])
            exports = module.get_all_exports()
            ordered_nodes = [exports[name] for name in requested_names if name in exports]
        else:
            ordered_nodes = list(module.get_all_exports().values())

        # Backend lowering needs the implementation closure as well as the public
        # interface. Source-level visibility is enforced independently.
        ordered_nodes.extend(module.get_all_implementation().values())
        for node in ordered_nodes:
            if not isinstance(node, Function):
                continue
            if node.name in self.functions or node.name in seen:
                continue
            seen.add(node.name)
            result.append(node)
            self.declare_function(node)
        return result
''',
)
replace_one(
    path,
    '''        for node in ast_nodes:
            if isinstance(node, FromImport):
                key = f"__from__{node.module_path}"
                if key not in from_import_names:
                    from_import_names[key] = []
                from_import_names[key].extend(node.names)

        # Parser-level inference deliberately defers functions whose return
''',
    '''        for node in ast_nodes:
            if isinstance(node, FromImport):
                key = f"__from__{node.module_path}"
                if key not in from_import_names:
                    from_import_names[key] = []
                from_import_names[key].extend(node.names)

        # Names callable by functions in the entry source. Implementation-only
        # declarations can be emitted without becoming source-visible.
        entry_visible = {
            node.name for node in ast_nodes if isinstance(node, Function)
        }
        for module_name, module in imported_modules.items():
            exports = module.get_all_exports()
            if module_name.startswith("__from__"):
                visible_names = from_import_names.get(module_name, [])
            else:
                visible_names = list(exports)
            entry_visible.update(
                name
                for name in visible_names
                if isinstance(exports.get(name), Function)
            )
        self._entry_visible_function_names = entry_visible

        # Parser-level inference deliberately defers functions whose return
''',
)
replace_one(
    path,
    '''        for imported_module in imported_modules.values():
            for imported_node in imported_module.get_all_exports().values():
''',
    '''        for imported_module in imported_modules.values():
            for imported_node in imported_module.get_all_implementation().values():
''',
)

path = "source/transpiler/expr_calls.py"
replace_one(
    path,
    '''    def visit_Call(self, node: Call):
        func_name = node.name.lower()
''',
    '''    def _check_module_function_visibility(self, node: Call) -> None:
        function_nodes = getattr(self.codegen, "_function_nodes", {})
        target_node = function_nodes.get(node.name)
        if target_node is None:
            return
        target_source = getattr(target_node, "_source_path", "")
        entry_source = getattr(self.codegen, "_compile_source_file", "")
        caller_name = getattr(self.codegen, "_current_function_name", None)
        caller_node = function_nodes.get(caller_name) if caller_name else None
        caller_source = getattr(caller_node, "_source_path", "") or entry_source
        if not target_source or target_source == caller_source:
            return
        if not getattr(target_node, "is_public", True):
            raise ExprGenError(
                f"Private function '{node.name}' is not visible outside its module"
            )
        if caller_source == entry_source and node.name not in getattr(
            self.codegen, "_entry_visible_function_names", set()
        ):
            raise ExprGenError(f"Function '{node.name}' is not imported into this module")

    def visit_Call(self, node: Call):
        self._check_module_function_visibility(node)
        func_name = node.name.lower()
''',
)

path = "source/transpiler/import_resolver.py"
replace_one(
    path,
    '''        # Order so type definitions and constants precede function bodies
        # that reference them. Without this, static globals would be
        # declared after their first use site -- C compile error.
        result.sort(key=self._sort_key)

        # Parsing a file cannot infer return types that depend on imported
''',
    '''        # Order so type definitions and constants precede function bodies
        # that reference them. Without this, static globals would be
        # declared after their first use site -- C compile error.
        result.sort(key=self._sort_key)
        self._validate_private_function_boundaries(result)

        # Parsing a file cannot infer return types that depend on imported
''',
)
replace_one(
    path,
    '''            else:
                imported_nodes = self._parse_import_file(import_file_str)
            # Recurse into the imported file's own imports first so its
''',
    '''            else:
                imported_nodes = self._parse_import_file(import_file_str)
            if isinstance(node, A.FromImport):
                self._reject_private_selective_imports(
                    imported_nodes, set(node.names), import_file_str
                )
            # Recurse into the imported file's own imports first so its
''',
)
replace_one(
    path,
    '''            if filter_names is not None and imp_node.name not in filter_names:
                return
            if imp_node.name in imported_funcs:
''',
    '''            if (
                filter_names is not None
                and imp_node.name not in filter_names
                and getattr(imp_node, "is_public", True)
            ):
                return
            if imp_node.name in imported_funcs:
''',
)
replace_one(
    path,
    '''    def _resolve_cimport_path(self, raw_path: str, file_base_dir: Path) -> Path | None:
''',
    '''    @staticmethod
    def _reject_private_selective_imports(
        nodes: list[A.ASTNode], requested: set[str], source_file: str
    ) -> None:
        for node in nodes:
            if not isinstance(node, A.Function) or node.name not in requested:
                continue
            if not getattr(node, "is_public", True):
                raise ImportError(
                    f"Cannot import private function '{node.name}' from '{source_file}'"
                )

    @staticmethod
    def _walk_ast(value: Any):
        if isinstance(value, A.ASTNode):
            yield value
            for child in vars(value).values():
                yield from ImportResolver._walk_ast(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from ImportResolver._walk_ast(child)
        elif isinstance(value, dict):
            for child in value.values():
                yield from ImportResolver._walk_ast(child)

    @staticmethod
    def _validate_private_function_boundaries(nodes: list[A.ASTNode]) -> None:
        private_functions = {
            node.name: node
            for node in nodes
            if isinstance(node, A.Function) and not getattr(node, "is_public", True)
        }
        if not private_functions:
            return
        for caller in nodes:
            if not isinstance(caller, A.Function):
                continue
            caller_source = getattr(caller, "_source_file", "")
            for child in ImportResolver._walk_ast(caller.body):
                if not isinstance(child, A.Call):
                    continue
                target = private_functions.get(child.name)
                if target is None:
                    continue
                target_source = getattr(target, "_source_file", "")
                if caller_source and target_source and caller_source != target_source:
                    raise ValueError(
                        f"Private function '{child.name}' is not visible outside its module"
                    )

    def _resolve_cimport_path(self, raw_path: str, file_base_dir: Path) -> Path | None:
''',
)

path = "source/diagnostics/diagnostics_engine_symbols.py"
replace_one(
    path,
    '''    current_dir = os.path.dirname(self.filepath)

    for i, (ttype, _tval, _, _) in enumerate(tokens):
''',
    '''    current_dir = os.path.dirname(self.filepath)

    # Selective imports introduce the explicitly named declarations into the
    # namespace. The compiler still validates existence and visibility.
    for i, (ttype, _tval, _, _) in enumerate(tokens):
        if ttype != "FROM":
            continue
        j = i + 1
        while j < len(tokens) and token_type_at(tokens, j) != "IMPORT":
            j += 1
        if j >= len(tokens):
            continue
        j += 1
        while j < len(tokens) and token_type_at(tokens, j) != "NEWLINE":
            if token_type_at(tokens, j) == "IDENT":
                self.user_symbols.add(token_text_at(tokens, j))
            j += 1

    for i, (ttype, _tval, _, _) in enumerate(tokens):
''',
)

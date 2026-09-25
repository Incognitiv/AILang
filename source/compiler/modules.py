"""
AILang Module Loader - Resolves and loads imported .ail files

Handles:
- import module_name     → loads module_name.ail
- import path.to.module  → loads path/to/module.ail
- from module import x   → loads specific symbols
"""

import os
from parser.ast import (
    Assign,
    ASTNode,
    ClassDef,
    EnumDef,
    FromImport,
    Function,
    Import,
    Library,
    LinkDirective,
    RecordDef,
    VarDecl,
)
from parser.parser import Parser

from compiler.module_cache import ModuleCache
from compiler.module_loading import load_module_graph
from compiler.module_symbols import ModuleSymbols
from lexer.scan import tokenize


class Module:
    """Represents a loaded AILang module"""

    def __init__(self, name: str, path: str, ast: list[ASTNode]):
        self.name = name
        self.path = path
        self.ast = ast
        self.exports: ModuleSymbols[ASTNode] = ModuleSymbols()
        # Public interface and implementation closure are separate.
        self.implementation: ModuleSymbols[ASTNode] = ModuleSymbols()
        self.link_directives: list[LinkDirective] = []
        self.dependencies: list[str] = []
        self.is_library = False
        self.library_name: str | None = None

        self._extract_exports()

    def _extract_exports(self) -> None:
        """Extract exported symbols from AST.

        Each Function node also gets a `_source_path` attribute stamped with
        this module's file path so the profiler can resolve runtime function
        names back to source file:line for blame reports and crash frames.
        AST nodes are plain Python objects, so adding an attribute is safe.
        """
        for node in self.ast:
            if isinstance(node, Library):
                self.is_library = True
                self.library_name = node.name
            elif isinstance(node, Function):
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
            elif isinstance(node, LinkDirective):
                self.link_directives.append(node)

    def get_export(self, name: str) -> ASTNode | None:
        """Get an exported symbol by name"""
        return self.exports.get(name)

    def get_all_exports(self) -> dict[str, ASTNode]:
        """Get the public source-level interface of this module."""
        return self.exports.copy()

    def get_all_implementation(self) -> dict[str, ASTNode]:
        """Get declarations required to lower this module's implementation."""
        return self.implementation.copy()


def _has_link_directive(
    directives: list[LinkDirective], candidate: LinkDirective
) -> bool:
    """Return True if an equivalent link directive is already present."""
    return any(
        row.flags == candidate.flags
        and getattr(row, "target_os", None) == getattr(candidate, "target_os", None)
        for row in directives
    )


class ModuleLoader:
    """Loads and resolves AILang modules"""

    def __init__(self, search_paths: list[str] | None = None):
        self.cache = ModuleCache()
        # Canonical language-module root.  Standard-library imports must not
        # depend on the caller's CWD or on the source file living inside the
        # repository tree (native/JIT temporary files commonly do not).
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        requested = list(search_paths or [])
        self.search_paths = [
            repo_root,
            *[p for p in requested if os.path.abspath(p) != repo_root],
        ]
        self.current_file: str | None = None

    def set_current_file(self, path: str) -> None:
        """Set the current file being compiled (for relative imports)"""
        self.current_file = os.path.abspath(path)

    def resolve_module_path(self, module_name: str) -> str | None:
        """Resolve a module name to a file path

        Search order:
        1. Relative to current file
        2. In search paths
        3. In ./lib/ directory
        4. In ../lib/ directory (for tests/)
        5. In parent directories
        """
        # Convert dots to path separators: utils.helpers -> utils/helpers.ail
        rel_path = module_name.replace(".", os.sep) + ".ail"

        # 1. Relative to current file
        if self.current_file:
            current_dir = os.path.dirname(self.current_file)
            candidate = os.path.join(current_dir, rel_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        # 2. Search paths
        for search_path in self.search_paths:
            candidate = os.path.join(search_path, rel_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        # 3. lib/ directory relative to current file
        if self.current_file:
            current_dir = os.path.dirname(self.current_file)
            candidate = os.path.join(current_dir, "lib", rel_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        # 4. ../lib/ directory (for test files in tests/ folder)
        if self.current_file:
            current_dir = os.path.dirname(self.current_file)
            candidate = os.path.join(current_dir, "..", "lib", rel_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        # 5. Parent directory (sibling modules)
        if self.current_file:
            current_dir = os.path.dirname(self.current_file)
            candidate = os.path.join(current_dir, "..", rel_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        # 6. Grandparent directory (nested submodules, e.g. builder/emitters/ → builder/)
        if self.current_file:
            current_dir = os.path.dirname(self.current_file)
            candidate = os.path.join(current_dir, "..", "..", rel_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        return None

    def load_module(self, module_name: str) -> Module:
        """Load a dependency graph without recursive calls or copied closures."""
        return load_module_graph(self, module_name)

    def _load_file(self, name: str, path: str) -> Module:
        """Read and parse one module; graph traversal is owned by the loader."""
        with open(path, "r", encoding="utf-8") as source_file:
            source = source_file.read()
        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()
        return Module(name, path, ast)

    @staticmethod
    def _merge_dependency_exports(
        module: Module,
        imported_module: Module,
        module_path: str,
        *,
        requested_names: list[str] | None,
    ) -> None:
        """Merge the symbol closure required by a nested module import."""
        exports = imported_module.exports
        module.implementation.include(imported_module.implementation)
        if requested_names is None:
            module.exports.include(exports)
        else:
            selected: ModuleSymbols[ASTNode] = ModuleSymbols()
            for name in requested_names:
                if name not in exports:
                    raise ImportError(f"Cannot import '{name}' from '{module_path}'")
                selected[name] = exports[name]
            module.exports.include(selected)
        for link_directive in imported_module.link_directives:
            if not _has_link_directive(module.link_directives, link_directive):
                module.link_directives.append(link_directive)

    def process_imports(
        self, ast: list[ASTNode]
    ) -> tuple[list[ASTNode], dict[str, Module]]:
        """Process import statements in an AST

        Returns:
        - Modified AST with imports removed
        - Dict of imported modules (name -> Module)
        """
        imports: dict[str, Module] = {}
        remaining_ast: list[ASTNode] = []

        for node in ast:
            if isinstance(node, Import):
                module = self.load_module(node.module_path)
                # Use alias if provided, otherwise use module name
                key = node.alias or node.module_path.split(".")[-1]
                imports[key] = module
            elif isinstance(node, FromImport):
                module = self.load_module(node.module_path)
                # Import specific names into current namespace
                for name in node.names:
                    if name not in module.exports:
                        raise ImportError(
                            f"Cannot import '{name}' from '{node.module_path}'"
                        )
                imports[f"__from__{node.module_path}"] = module
                # We'll handle the actual symbol injection in codegen
                remaining_ast.append(node)  # Keep for codegen to process
            elif isinstance(node, Library):
                # Keep library declarations
                remaining_ast.append(node)
            else:
                remaining_ast.append(node)

        return remaining_ast, imports


# Module-level singleton holder (simple list avoids 'global' statement and class with too-few-methods)
_LOADER_INSTANCE: list[ModuleLoader] = []


def get_loader() -> ModuleLoader:
    """Get the global module loader"""
    if not _LOADER_INSTANCE:
        _LOADER_INSTANCE.append(ModuleLoader())
    return _LOADER_INSTANCE[0]


def set_search_paths(paths: list[str]) -> None:
    """Set the module search paths"""
    loader = get_loader()
    loader.search_paths = list(paths)
    loader.cache.clear()


def load_module(name: str) -> Module:
    """Load a module by name"""
    return get_loader().load_module(name)


def process_imports(
    ast: list[ASTNode], current_file: str | None = None
) -> tuple[list[ASTNode], dict[str, Module]]:
    """Process imports in an AST"""
    loader = get_loader()
    if current_file:
        loader.set_current_file(current_file)
    return loader.process_imports(ast)

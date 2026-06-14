"""
AILang Module Loader - Resolves and loads imported .ail files

Handles:
- import module_name     → loads module_name.ail
- import path.to.module  → loads path/to/module.ail
- from module import x   → loads specific symbols
"""

import contextlib
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
from typing import Dict, List, Optional, Set, Tuple

from lexer.scan import tokenize


class ModuleCache:
    """Caches loaded modules to avoid re-parsing"""

    def __init__(self):
        self.modules: Dict[str, "Module"] = {}
        self.loading: Set[str] = set()  # Detect circular imports
        self.mtimes: Dict[str, float] = {}  # L13 fix: Track file modification times

    @staticmethod
    def _cache_key(path: str) -> str:
        """Canonical filesystem identity for a module path."""
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def get(self, path: str) -> Optional["Module"]:
        return self.modules.get(self._cache_key(path))

    def put(self, path: str, module: "Module") -> None:
        key = self._cache_key(path)
        self.modules[key] = module
        # Store modification time for cache invalidation
        if module.path:
            with contextlib.suppress(OSError):
                self.mtimes[key] = os.path.getmtime(module.path)

    def is_stale(self, file_path: str) -> bool:
        """Check if cached module is stale (file was modified)."""
        key = self._cache_key(file_path)
        if key not in self.modules:
            return True
        if key not in self.mtimes:
            return False  # No mtime recorded, assume fresh
        try:
            current_mtime = os.path.getmtime(file_path)
            return current_mtime > self.mtimes[key]
        except OSError:
            return False

    def invalidate(self, path: str) -> None:
        """Remove a module from cache."""
        key = self._cache_key(path)
        self.modules.pop(key, None)
        self.mtimes.pop(key, None)

    def clear(self) -> None:
        """Clear entire cache (useful for REPL reload)."""
        self.modules.clear()
        self.loading.clear()
        self.mtimes.clear()

    def is_loading(self, path: str) -> bool:
        return self._cache_key(path) in self.loading

    def start_loading(self, path: str) -> None:
        self.loading.add(self._cache_key(path))

    def finish_loading(self, path: str) -> None:
        self.loading.discard(self._cache_key(path))


class Module:
    """Represents a loaded AILang module"""

    def __init__(self, name: str, path: str, ast: List[ASTNode]):
        self.name = name
        self.path = path
        self.ast = ast
        self.exports: Dict[str, ASTNode] = {}
        self.link_directives: List[LinkDirective] = []
        self.is_library = False
        self.library_name: Optional[str] = None

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
            elif isinstance(node, LinkDirective):
                self.link_directives.append(node)

    def get_export(self, name: str) -> Optional[ASTNode]:
        """Get an exported symbol by name"""
        return self.exports.get(name)

    def get_all_exports(self) -> Dict[str, ASTNode]:
        """Get all exported symbols"""
        return self.exports.copy()


def _has_link_directive(
    directives: List[LinkDirective], candidate: LinkDirective
) -> bool:
    """Return True if an equivalent link directive is already present."""
    return any(
        row.flags == candidate.flags
        and getattr(row, "target_os", None) == getattr(candidate, "target_os", None)
        for row in directives
    )


class ModuleLoader:
    """Loads and resolves AILang modules"""

    def __init__(self, search_paths: Optional[List[str]] = None):
        self.cache = ModuleCache()
        self.search_paths = search_paths or []
        self.current_file: Optional[str] = None

    def set_current_file(self, path: str) -> None:
        """Set the current file being compiled (for relative imports)"""
        self.current_file = os.path.abspath(path)

    def resolve_module_path(self, module_name: str) -> Optional[str]:
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
        """Load a module by name"""
        # Resolve path first to check staleness
        module_path = self.resolve_module_path(module_name)
        if not module_path:
            raise ImportError(f"Cannot find module '{module_name}'")

        # Check cache first, but invalidate if stale (L13 fix)
        if self.cache.is_stale(module_path):
            self.cache.invalidate(module_path)

        cached = self.cache.get(module_path)
        if cached:
            return cached

        # Check for circular imports
        if self.cache.is_loading(module_path):
            raise ImportError(f"Circular import detected: '{module_name}'")

        # Load the module
        self.cache.start_loading(module_path)
        try:
            module = self._load_file(module_name, module_path)
            self.cache.put(module_path, module)
            return module
        finally:
            self.cache.finish_loading(module_path)

    def _load_file(self, name: str, path: str) -> Module:
        """Load and parse an AILang file, recursively resolving its imports"""
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        # Tokenize and parse
        tokens = tokenize(source)
        parser = Parser(tokens)
        ast = parser.parse_program()

        # Create the module
        module = Module(name, path, ast)

        # Recursively load this module's imports and add their exports
        # This ensures transitive dependencies are available
        old_file = self.current_file
        self.current_file = path
        try:
            for node in ast:
                if isinstance(node, Import):
                    try:
                        imported_mod = self.load_module(node.module_path)
                        # Add all exports from the imported module to this module's exports
                        # (unless they conflict with local definitions)
                        for exp_name, exp_node in imported_mod.exports.items():
                            if exp_name not in module.exports:
                                module.exports[exp_name] = exp_node
                        for link_directive in imported_mod.link_directives:
                            if not _has_link_directive(
                                module.link_directives, link_directive
                            ):
                                module.link_directives.append(link_directive)
                    except ImportError as e:
                        # Don't silently swallow import errors - report them
                        import sys

                        print(
                            f"Warning: Failed to import '{node.module_path}': {e}",
                            file=sys.stderr,
                        )
        finally:
            self.current_file = old_file

        return module

    def process_imports(
        self, ast: List[ASTNode]
    ) -> Tuple[List[ASTNode], Dict[str, Module]]:
        """Process import statements in an AST

        Returns:
        - Modified AST with imports removed
        - Dict of imported modules (name -> Module)
        """
        imports: Dict[str, Module] = {}
        remaining_ast: List[ASTNode] = []

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
_LOADER_INSTANCE: List[ModuleLoader] = []


def get_loader() -> ModuleLoader:
    """Get the global module loader"""
    if not _LOADER_INSTANCE:
        _LOADER_INSTANCE.append(ModuleLoader())
    return _LOADER_INSTANCE[0]


def set_search_paths(paths: List[str]) -> None:
    """Set the module search paths"""
    get_loader().search_paths = paths


def load_module(name: str) -> Module:
    """Load a module by name"""
    return get_loader().load_module(name)


def process_imports(
    ast: List[ASTNode], current_file: Optional[str] = None
) -> Tuple[List[ASTNode], Dict[str, Module]]:
    """Process imports in an AST"""
    loader = get_loader()
    if current_file:
        loader.set_current_file(current_file)
    return loader.process_imports(ast)

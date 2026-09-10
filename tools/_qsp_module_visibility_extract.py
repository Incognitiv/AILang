from pathlib import Path


def replace_one(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one extraction site, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def replace_exact(path: str, old: str, new: str, expected: int) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} sites, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


path = "source/transpiler/import_resolver.py"
replace_one(
    path,
    "from transpiler.cbind_flags import headers_from_cflags\n",
    "from transpiler.cbind_flags import headers_from_cflags\n"
    "from transpiler.import_visibility import (\n"
    "    import_sort_key,\n"
    "    reject_private_selective_imports,\n"
    "    tag_source_file,\n"
    "    validate_private_function_boundaries,\n"
    ")\n",
)
replace_one(
    path,
    "        result.sort(key=self._sort_key)\n"
    "        self._validate_private_function_boundaries(result)\n",
    "        result.sort(key=import_sort_key)\n"
    "        validate_private_function_boundaries(result)\n",
)
replace_one(
    path,
    "                self._reject_private_selective_imports(\n"
    "                    imported_nodes, set(node.names), import_file_str\n"
    "                )\n",
    "                reject_private_selective_imports(\n"
    "                    imported_nodes, set(node.names), import_file_str\n"
    "                )\n",
)
replace_exact(path, "self._tag_source_file(", "tag_source_file(", expected=2)
replace_one(
    path,
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

''',
    "",
)
replace_one(
    path,
    '''    @staticmethod
    def _tag_source_file(nodes: list[A.ASTNode], filepath: str) -> None:
        """Attach source path metadata to parsed nodes for diagnostics/reports."""
        if not filepath:
            return
        for node in nodes:
            if not node._source_file:
                node._source_file = filepath

''',
    "",
)
replace_one(
    path,
    '''    @staticmethod
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
''',
    "",
)

"""Symbol collection helpers for DiagnosticEngine."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from token_access import token_text_at, token_type_at

from .diagnostics_catalog import TYPE_NAMES, TYPE_PREFIX_TOKENS, VECTOR_TYPE_NAMES
from .diagnostics_utils import tokenize


def _type_alias_symbol(tokens: List[Tuple], start: int) -> Optional[str]:
    """Return the alias name from `type`/`typedef` token streams."""
    if start + 1 >= len(tokens):
        return None
    ttype = token_type_at(tokens, start)
    if ttype == "TYPE":
        next_tok = tokens[start + 1]
        return next_tok[1] if next_tok[0] == "IDENT" else None
    if ttype != "TYPEDEF":
        return None

    first_ident: Optional[str] = None
    last_ident: Optional[str] = None
    j = start + 1
    while j < len(tokens):
        jtype, jval, *_ = tokens[j]
        if jtype == "NEWLINE":
            break
        if jtype == "ASSIGN":
            return first_ident
        if jtype == "IDENT":
            if first_ident is None:
                first_ident = jval
            last_ident = jval
        j += 1
    return last_ident


def _is_ident_type_name(value: str) -> bool:
    return value in (TYPE_NAMES | VECTOR_TYPE_NAMES)


def _collect_symbols(self, tokens: List[Tuple]) -> None:
    """Collect all user-defined symbols (functions, variables, parameters)."""
    i = 0
    while i < len(tokens):
        ttype, tval, _, _ = tokens[i]

        # Function definition: def name(params) OR def name[T](params)
        # The function name may be a keyword token (e.g. TEST, MATCH),
        # not just IDENT, so accept any token right after DEF.
        if ttype == "DEF" and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            # Add function name (works for IDENT and keyword tokens)
            self.user_symbols.add(next_tok[1])
            # Find the parameter list — may have [T] generics before (
            j = i + 2
            # Skip generic params: [T] or [K, V]
            if j < len(tokens) and token_type_at(tokens, j) == "LBRACKET":
                j += 1
                while j < len(tokens) and token_type_at(tokens, j) != "RBRACKET":
                    if token_type_at(tokens, j) == "IDENT":
                        self.user_symbols.add(token_text_at(tokens, j))
                    j += 1
                if j < len(tokens):
                    j += 1  # skip RBRACKET
            if j < len(tokens) and token_type_at(tokens, j) == "LPAREN":
                j += 1
                while j < len(tokens) and token_type_at(tokens, j) != "RPAREN":
                    if token_type_at(tokens, j) == "IDENT":
                        self.user_symbols.add(token_text_at(tokens, j))
                    j += 1
            # Also collect return-type idents: ): T
            if j < len(tokens) and token_type_at(tokens, j) == "RPAREN":
                j += 1
                if j < len(tokens) and token_type_at(tokens, j) == "COLON":
                    j += 1
                    if j < len(tokens) and token_type_at(tokens, j) == "IDENT":
                        self.user_symbols.add(token_text_at(tokens, j))

        # Type-prefix function definition: int/string/void/etc name(params):
        is_ident_type_prefix = ttype == "IDENT" and _is_ident_type_name(tval)
        if (ttype in TYPE_PREFIX_TOKENS or is_ident_type_prefix) and i + 2 < len(
            tokens
        ):
            next_tok = tokens[i + 1]
            after_tok = tokens[i + 2]
            # Pattern: TYPE IDENT LPAREN
            if next_tok[0] == "IDENT" and after_tok[0] == "LPAREN":
                self.user_symbols.add(next_tok[1])
                # Collect parameters
                j = i + 3
                while j < len(tokens) and token_type_at(tokens, j) != "RPAREN":
                    if token_type_at(tokens, j) == "IDENT":
                        self.user_symbols.add(token_text_at(tokens, j))
                    j += 1

        # Internal C-ABI boundary function:
        # internal size_t name(param: internal cstring):
        if ttype == "IDENT" and tval == "internal" and i + 3 < len(tokens):
            return_tok = tokens[i + 1]
            name_tok = tokens[i + 2]
            open_tok = tokens[i + 3]
            if (
                (return_tok[0] in TYPE_PREFIX_TOKENS or return_tok[0] == "IDENT")
                and name_tok[0] == "IDENT"
                and open_tok[0] == "LPAREN"
            ):
                self.user_symbols.add(name_tok[1])
                j = i + 4
                while j < len(tokens) and token_type_at(tokens, j) != "RPAREN":
                    if token_type_at(tokens, j) == "IDENT":
                        self.user_symbols.add(token_text_at(tokens, j))
                    j += 1

        # Variable assignment: name =
        if ttype == "IDENT" and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            if next_tok[0] == "ASSIGN":
                self.user_symbols.add(tval)

        # Const declaration: const [type] name = ...
        if ttype == "CONST" and i + 1 < len(tokens):
            j = i + 1
            # Skip optional type token
            if j < len(tokens) and token_type_at(tokens, j) in TYPE_PREFIX_TOKENS:
                j += 1
            if j < len(tokens) and token_type_at(tokens, j) == "IDENT":
                self.user_symbols.add(token_text_at(tokens, j))

        # Static declaration: static [type] name = ...
        if ttype == "STATIC" and i + 1 < len(tokens):
            j = i + 1
            # Skip optional type token
            if j < len(tokens) and token_type_at(tokens, j) in TYPE_PREFIX_TOKENS:
                j += 1
            if j < len(tokens) and token_type_at(tokens, j) == "IDENT":
                self.user_symbols.add(token_text_at(tokens, j))

        # Extern declaration: AILang accepts either the legacy
        # form `extern <name>(...)` or the explicit `extern fn
        # <name>(...)` / `extern var <name>: type`. The lexer
        # emits `fn`/`var` as plain IDENT tokens (no dedicated
        # keyword), so we have to check the token VALUE here, not
        # just the type. Skip the `fn`/`var` token and pick the
        # following IDENT as the externed symbol name.
        if ttype == "EXTERN" and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            if next_tok[0] == "IDENT" and next_tok[1] not in ("fn", "var"):
                self.user_symbols.add(next_tok[1])
            elif (
                next_tok[0] == "IDENT"
                and next_tok[1] in ("fn", "var")
                and i + 2 < len(tokens)
                and token_type_at(tokens, i + 2) == "IDENT"
            ):
                self.user_symbols.add(token_text_at(tokens, i + 2))

        # Type alias: type Name = ..., typedef Name = Type, typedef Type Name
        if ttype in ("TYPE", "TYPEDEF"):
            alias_name = _type_alias_symbol(tokens, i)
            if alias_name:
                self.user_symbols.add(alias_name)

        # Record/class definition — collect name and field/method names
        if ttype in ("RECORD", "CLASS", "UNION") and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            if next_tok[0] == "IDENT":
                self.user_symbols.add(next_tok[1])
                # Scan ahead for fields/methods until matching END
                j = i + 2
                depth = 1
                while j < len(tokens) and depth > 0:
                    jtype = token_type_at(tokens, j)
                    if jtype in (
                        "DEF",
                        "CLASS",
                        "RECORD",
                        "UNION",
                        "IF",
                        "WHILE",
                        "FOR",
                        "FOREACH",
                        "LOOP",
                    ):
                        depth += 1
                    elif jtype == "END":
                        depth -= 1
                    elif jtype == "IDENT" and depth == 1:
                        self.user_symbols.add(token_text_at(tokens, j))
                    j += 1

        # Enum definition — collect enum name AND variant names
        if ttype == "ENUM" and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            if next_tok[0] == "IDENT":
                self.user_symbols.add(next_tok[1])
                # Scan ahead for variant names until END
                j = i + 2
                while j < len(tokens) and token_type_at(tokens, j) != "END":
                    if token_type_at(tokens, j) == "IDENT":
                        self.user_symbols.add(token_text_at(tokens, j))
                    j += 1

        # Import: import name
        if (ttype == "IMPORT" and i + 1 < len(tokens)) and token_type_at(
            tokens, i + 1
        ) == "IDENT":
            self.user_symbols.add(token_text_at(tokens, i + 1))

        # For loop variable: for (i =
        if (ttype == "FOR" and i + 2 < len(tokens)) and (
            token_type_at(tokens, i + 1) == "LPAREN"
            and token_type_at(tokens, i + 2) == "IDENT"
        ):
            self.user_symbols.add(token_text_at(tokens, i + 2))

        # Foreach variable: foreach x in
        if (ttype == "FOREACH" and i + 1 < len(tokens)) and token_type_at(
            tokens, i + 1
        ) == "IDENT":
            self.user_symbols.add(token_text_at(tokens, i + 1))

        # Repeat variable: repeat N times
        if (ttype == "REPEAT" and i + 1 < len(tokens)) and token_type_at(
            tokens, i + 1
        ) == "IDENT":
            self.user_symbols.add(token_text_at(tokens, i + 1))

        # Ada-style range assign: name := low..high = init
        if (ttype == "IDENT" and i + 1 < len(tokens)) and token_type_at(
            tokens, i + 1
        ) == "COLON_ASSIGN":
            self.user_symbols.add(tval)

        # Error type names used in throw/catch: throw TypeError("msg")
        if (ttype == "THROW" and i + 1 < len(tokens)) and token_type_at(
            tokens, i + 1
        ) == "IDENT":
            self.user_symbols.add(token_text_at(tokens, i + 1))

        # Template blocks: the lexer emits a single TEMPLATE_BLOCK token
        # containing the raw C source.  Extract function names from it
        # so calls to template-generated functions aren't flagged.
        if ttype == "TEMPLATE_BLOCK":
            import re as _re

            # Match C-style function definitions: "type name("
            for m in _re.finditer(r"\b[a-zA-Z_]\w*\s+([a-zA-Z_]\w*)\s*\(", tval):
                self.user_symbols.add(m.group(1))

        i += 1


def _resolve_imports(self, tokens: List[Tuple]) -> None:
    """Resolve import statements and collect exported symbols from imported modules."""
    if not self.filepath:
        return

    current_dir = os.path.dirname(self.filepath)

    for i, (ttype, _tval, _, _) in enumerate(tokens):
        if ttype != "IMPORT":
            continue

        # Collect the dotted module name: import foo.bar.baz
        parts: List[str] = []
        j = i + 1
        while j < len(tokens):
            if token_type_at(tokens, j) == "IDENT":
                parts.append(token_text_at(tokens, j))
            elif token_type_at(tokens, j) == "DOT":
                j += 1
                continue
            else:
                break
            j += 1

        if not parts:
            continue

        module_name = ".".join(parts)
        rel_path = module_name.replace(".", os.sep) + ".ail"

        # Search for the module file in common locations
        candidates = [
            os.path.join(current_dir, rel_path),
            os.path.join(current_dir, "..", rel_path),
            os.path.join(current_dir, "lib", rel_path),
            os.path.join(current_dir, "..", "lib", rel_path),
            os.path.join(current_dir, "..", "..", rel_path),
            os.path.join(current_dir, "..", "..", "..", rel_path),
            os.path.join(current_dir, "..", "..", "..", "..", rel_path),
            os.path.join(os.getcwd(), rel_path),
        ]

        for candidate in candidates:
            if os.path.isfile(candidate):
                self._collect_symbols_from_file(candidate)
                break

        # Also add all parts as known symbols (the module segments)
        for part in parts:
            self.user_symbols.add(part)

    if any(ttype == "CIMPORT_LINE" for ttype, *_rest in tokens):
        _collect_cimport_symbols(self)


def _collect_cimport_symbols(self) -> None:
    """Collect symbols produced by `#cimport` binding expansion.

    The token-level diagnostic pass normally only sees the source file text. A
    `#cimport` line expands to generated constants, extern records, and extern
    functions through ImportResolver, so harvest those names before unknown-name
    checks run.
    """
    try:
        from parser import ast as A
        from parser.parser import Parser

        from transpiler.import_resolver import ImportResolver

        with open(self.filepath, "r", encoding="utf-8") as f:
            source = f.read()
        parser = Parser(tokenize(source))
        nodes = parser.parse_program()
        resolved_nodes = ImportResolver().run(nodes, self.filepath)
    except (OSError, RuntimeError, SyntaxError, ValueError, TypeError):
        return

    for node in resolved_nodes:
        if isinstance(node, A.Function):
            self.user_symbols.add(node.name)
            for param in getattr(node, "params", []) or []:
                if isinstance(param, tuple) and param:
                    self.user_symbols.add(str(param[0]))
        elif isinstance(node, A.VarDecl):
            self.user_symbols.add(node.var_name)
        elif isinstance(node, A.TypeAlias):
            self.user_symbols.add(node.name)
        elif isinstance(node, A.ExternFn):
            self.user_symbols.add(node.name)
            for param_name, _param_type in getattr(node, "params", []) or []:
                self.user_symbols.add(str(param_name))
        elif isinstance(node, A.ExternVar):
            self.user_symbols.add(node.name)
        elif isinstance(node, (A.RecordDef, A.ExternRecordDef, A.ClassDef, A.UnionDef)):
            self.user_symbols.add(node.name)
        elif isinstance(node, A.EnumDef):
            self.user_symbols.add(node.name)
            for variant in getattr(node, "variants", []) or []:
                self.user_symbols.add(getattr(variant, "name", ""))


def _collect_symbols_from_file(
    self, filepath: str, visited: Optional[set] = None
) -> None:
    """Tokenize an imported file and harvest its top-level symbols.

    Follows transitive imports (e.g. barrel files that re-export
    sub-modules) up to a reasonable depth, guarding against cycles.
    """
    resolved = os.path.normpath(os.path.abspath(filepath))
    if visited is None:
        visited = set()
    if resolved in visited:
        return  # already processed or circular
    visited.add(resolved)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()

        imported_tokens = tokenize(source)
        tok_list: List[Tuple[str, str, int, int]] = []
        for raw_tok in imported_tokens:
            parts = tuple(raw_tok)
            if len(parts) >= 4:
                tok_list.append(
                    (str(parts[0]), str(parts[1]), int(parts[2]), int(parts[3]))
                )
            elif len(parts) >= 2:
                tok_list.append((str(parts[0]), str(parts[1]), 1, 1))

        # Harvest all exported symbols: functions, classes, records,
        # enums, consts, externs, AND module-level variable assignments.
        func_depth = 0
        i = 0
        while i < len(tok_list):
            ttype = tok_list[i][0]

            # Track function depth so we only collect top-level symbols
            if ttype == "DEF":
                func_depth += 1
                # Collect function name (may be keyword token)
                if i + 1 < len(tok_list):
                    self.user_symbols.add(tok_list[i + 1][1])
            elif ttype == "END" and func_depth > 0:
                func_depth -= 1

            if (
                ttype in ("CLASS", "RECORD", "UNION", "ENUM") and i + 1 < len(tok_list)
            ) and tok_list[i + 1][0] == "IDENT":
                self.user_symbols.add(tok_list[i + 1][1])

            if ttype == "EXTERN" and i + 1 < len(tok_list):
                next_tok = tok_list[i + 1]
                if next_tok[0] == "IDENT" and next_tok[1] not in ("fn", "var"):
                    self.user_symbols.add(next_tok[1])
                elif (
                    next_tok[0] == "IDENT"
                    and next_tok[1] in ("fn", "var")
                    and i + 2 < len(tok_list)
                    and tok_list[i + 2][0] == "IDENT"
                ):
                    self.user_symbols.add(tok_list[i + 2][1])

            if ttype == "CONST" and i + 1 < len(tok_list):
                j = i + 1
                if j < len(tok_list) and tok_list[j][0] in TYPE_PREFIX_TOKENS:
                    j += 1
                if j < len(tok_list) and tok_list[j][0] == "IDENT":
                    self.user_symbols.add(tok_list[j][1])

            if ttype in ("TYPE", "TYPEDEF"):
                alias_name = _type_alias_symbol(tok_list, i)
                if alias_name:
                    self.user_symbols.add(alias_name)

            # Module-level variable assignments: NAME = value
            if (
                ttype == "IDENT"
                and func_depth == 0
                and i + 1 < len(tok_list)
                and tok_list[i + 1][0] == "ASSIGN"
            ):
                self.user_symbols.add(tok_list[i][1])

            if (ttype in TYPE_PREFIX_TOKENS and i + 2 < len(tok_list)) and (
                tok_list[i + 1][0] == "IDENT" and tok_list[i + 2][0] == "LPAREN"
            ):
                self.user_symbols.add(tok_list[i + 1][1])

            i += 1

        # Follow transitive imports in this file (barrel re-exports)
        imported_dir = os.path.dirname(resolved)
        i = 0
        while i < len(tok_list):
            if tok_list[i][0] == "IMPORT":
                sub_parts: List[str] = []
                j = i + 1
                while j < len(tok_list):
                    if tok_list[j][0] == "IDENT":
                        sub_parts.append(tok_list[j][1])
                    elif tok_list[j][0] == "DOT":
                        j += 1
                        continue
                    else:
                        break
                    j += 1
                if sub_parts:
                    sub_rel = os.sep.join(sub_parts) + ".ail"
                    sub_candidates = [
                        os.path.join(imported_dir, sub_rel),
                        os.path.join(imported_dir, "..", sub_rel),
                        os.path.join(imported_dir, "..", "..", sub_rel),
                        os.path.join(imported_dir, "..", "..", "..", sub_rel),
                        os.path.join(imported_dir, "..", "..", "..", "..", sub_rel),
                        os.path.join(os.getcwd(), sub_rel),
                    ]
                    for sc in sub_candidates:
                        if os.path.isfile(sc):
                            self._collect_symbols_from_file(sc, visited)
                            break
                    for sp in sub_parts:
                        self.user_symbols.add(sp)
            i += 1

    except (OSError, SyntaxError, ValueError):
        pass  # Can't read/parse imported file — skip silently

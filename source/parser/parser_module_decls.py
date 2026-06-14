"""Module/directive parsing helpers for Parser."""

from __future__ import annotations

from target_info import normalize_os_name
from token_access import token_type_at

from .ast import (
    ASTNode,
    CImport,
    CInclude,
    ExternFn,
    ExternRecordDef,
    ExternVar,
    FromImport,
    Import,
    Library,
    LinkDirective,
    Use,
)
from .parser_cabi_header_impl import parse_cabi_header


def parse_use(self) -> Use | list[Use]:
    """Parse use statement for standard library."""
    self.consume("USE")

    # Multi-line block form: use: ... end
    if self.peek_type() == "COLON":
        return self._parse_use_block()

    # Single-line form
    return self._parse_single_use()


def _consume_module_name_part(self) -> str:
    """Consume one module path token (identifier or keyword-like segment)."""
    token_type = self.peek_type()
    if token_type == "IDENT":
        return self.consume("IDENT")
    if token_type in ("STRING", "INT", "FLOAT_T", "DOUBLE", "BOOL", "VOID"):
        return self.consume()
    return self.consume("IDENT")


def _parse_single_use(self) -> Use:
    """Parse single-line use form."""
    module_parts = [self._consume_module_name_part()]
    while self.peek_type() == "DOT":
        self.consume("DOT")
        if self.peek_type() == "LBRACE":
            break
        module_parts.append(self._consume_module_name_part())

    module_path = ".".join(module_parts)

    names = None
    if self.peek_type() == "LBRACE":
        self.consume("LBRACE")
        names = [self.consume("IDENT")]
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            names.append(self.consume("IDENT"))
        self.consume("RBRACE")

    return Use(module_path, names)


def _parse_use_block(self) -> list[Use]:
    """Parse multi-line use block: use: ... end."""
    self.consume("COLON")
    self.skip_newlines()

    uses: list[Use] = []
    while self._not_block_end("END"):
        uses.append(self._parse_single_use())
        self.skip_newlines()

    self.consume("END")
    return uses


def parse_import(self) -> ASTNode | list[ASTNode]:
    """Parse import statement in single-line or block form."""
    self.consume("IMPORT")
    if self.peek_type() == "COLON":
        return self._parse_import_block()
    if _looks_like_inverse_from_import(self):
        return _parse_inverse_from_import_groups(self)
    return self._parse_single_import()


def _parse_single_import(self) -> Import:
    """Parse a single import statement."""
    target_os = self._parse_optional_import_target()
    module_parts = [self._consume_module_name_part()]
    while self.peek_type() == "DOT":
        self.consume("DOT")
        module_parts.append(self._consume_module_name_part())

    module_path = ".".join(module_parts)
    alias = None
    if self.peek_type() == "AS":
        self.consume("AS")
        alias = self.consume("IDENT")

    return Import(module_path, alias, target_os=target_os)


def _parse_optional_import_target(self) -> str | None:
    """Parse `import windows foo.bar` style target filters.

    `#cimport`, `#cinclude`, and `#link` already accept a leading target.
    UI backends need the same shape for normal AILang modules so one facade can
    select a pure backend without importing every platform implementation.
    """

    if self.peek_type() != "IDENT":
        return None
    if self.pos + 1 >= len(self.tokens):
        return None
    if token_type_at(self.tokens, self.pos + 1) not in {"IDENT", "STRING", "STRLIT"}:
        return None

    target = normalize_os_name(self.peek_text())
    if target not in {
        "windows",
        "linux",
        "freebsd",
        "macos",
        "wasm",
        "never",
    }:
        return None
    self.consume("IDENT")
    return target


def _parse_import_block(self) -> list[ASTNode]:
    """Parse multi-line import block: import: ... end."""
    self.consume("COLON")
    self.skip_newlines()

    imports: list[ASTNode] = []
    while self._not_block_end("END"):
        if _looks_like_inverse_from_import(self):
            imports.extend(_parse_inverse_from_import_groups(self))
        else:
            imports.append(self._parse_single_import())
        self.skip_newlines()

    self.consume("END")
    return imports


def parse_from_import(self) -> FromImport:
    """Parse from-import statement."""
    self.consume("FROM")

    module_parts = [self.consume("IDENT")]
    while self.peek_type() == "DOT":
        self.consume("DOT")
        module_parts.append(self.consume("IDENT"))
    module_path = ".".join(module_parts)

    self.consume("IMPORT")

    names = [self.consume("IDENT")]
    while self.peek_type() == "COMMA":
        self.consume("COMMA")
        names.append(self.consume("IDENT"))

    return FromImport(module_path, names)


def _looks_like_inverse_from_import(self) -> bool:
    """Detect `import a, b from module` without consuming tokens."""
    pos = self.pos
    while pos < len(self.tokens):
        token_type = token_type_at(self.tokens, pos)
        if token_type in {"NEWLINE", "END", "COLON"}:
            return False
        if token_type == "FROM":
            return True
        if token_type == "AS":
            return False
        pos += 1
    return False


def _parse_inverse_from_import_groups(self) -> list[ASTNode]:
    """Parse sugar: `import x, y from z and a, b from c`.

    The canonical AST stays `FromImport`, so module loading/resolution does not
    need a parallel import node.
    """
    groups: list[ASTNode] = []
    while True:
        names = [self.consume("IDENT")]
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            names.append(self.consume("IDENT"))

        self.consume("FROM")
        module_parts = [self._consume_module_name_part()]
        while self.peek_type() == "DOT":
            self.consume("DOT")
            module_parts.append(self._consume_module_name_part())
        groups.append(FromImport(".".join(module_parts), names))

        if self.peek_type() != "AND":
            break
        self.consume("AND")

    return groups


def parse_library_decl(self) -> Library:
    """Parse library declaration: @library(\"name\")."""
    self.consume("LIBRARY")
    self.consume("LPAREN")
    name_token = self.consume("STRLIT")
    name = name_token[1:-1]
    self.consume("RPAREN")
    return Library(name)


def parse_cinclude(self) -> ASTNode:
    """Parse C include directive."""
    token = self.peek()
    line = self._get_token_line(token)
    column = self._get_token_col(token)
    line_text = self.consume("CINCLUDE_LINE")
    rest = line_text[len("#cinclude") :].strip()
    target_os, rest = _split_directive_target(rest)
    if rest.startswith("<") and rest.endswith(">"):
        return CInclude(
            rest[1:-1],
            is_system=True,
            target_os=target_os,
            line=line,
            column=column,
            raw=line_text,
        )
    if rest.startswith('"') and rest.endswith('"'):
        return CInclude(
            rest[1:-1],
            is_system=False,
            target_os=target_os,
            line=line,
            column=column,
            raw=line_text,
        )
    return CInclude(
        rest,
        is_system=False,
        target_os=target_os,
        line=line,
        column=column,
        raw=line_text,
    )


def parse_link_directive(self) -> ASTNode:
    """Parse link directive."""
    token = self.peek()
    line = self._get_token_line(token)
    column = self._get_token_col(token)
    line_text = self.consume("LINK_LINE")
    rest = line_text[len("#link") :].strip()
    target_os, rest = _split_directive_target(rest)
    if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
        rest = rest[1:-1]
    return LinkDirective(
        rest, target_os=target_os, line=line, column=column, raw=line_text
    )


def parse_cimport(self) -> ASTNode:
    """Parse C import directive."""
    token = self.peek()
    line = self._get_token_line(token)
    column = self._get_token_col(token)
    line_text = self.consume("CIMPORT_LINE")
    rest = line_text[len("#cimport") :].strip()
    target_os, rest = _split_directive_target(rest)
    if rest.startswith('"') and rest.endswith('"'):
        rest = rest[1:-1]
    return CImport(rest, target_os=target_os, line=line, column=column, raw=line_text)


def _split_directive_target(rest: str) -> tuple[str | None, str]:
    """Split optional target prefix from a #link/#cinclude payload."""
    payload = rest.strip()
    if not payload or payload[0] in {'"', "<", "-"}:
        return None, payload
    parts = payload.split(None, 1)
    if len(parts) != 2:
        return None, payload
    target, remainder = parts
    if any(ch in target for ch in '/\\.:"<>'):
        return None, payload
    return normalize_os_name(target), remainder.strip()


def _parse_extern_decl(self) -> ExternFn | ExternVar | ExternRecordDef:
    """Dispatch extern declarations: extern fn/def or extern var."""
    pos = self.pos
    if pos + 1 < len(self.tokens):
        next_tok = self.tokens[pos + 1]
        if next_tok[0] == "IDENT" and next_tok[1] == "var":
            return self.parse_extern_var()
        if next_tok[0] == "RECORD":
            return self.parse_extern_record()
    return self.parse_extern_fn()


def parse_extern_var(self) -> ExternVar:
    """Parse external variable declaration: extern var name: type."""
    self.consume("EXTERN")
    var_text = self.consume("IDENT")
    if var_text != "var":
        self.error(f"Expected 'var' after 'extern', got {var_text!r}")

    name = self.consume("IDENT")
    var_type = "int"
    if self.peek_type() == "COLON":
        self.consume("COLON")
        var_type = self._parse_type_name()
    return ExternVar(name, var_type)


def parse_extern_record(self) -> ExternRecordDef:
    """Parse imported/incomplete C record declaration.

    Forms:
      extern record Name
      extern record Name = "struct native_name"
      extern record Name = "struct native_name" layout size 16 align 8 then
          field_name offset 0 size 8
      end
    """
    self.consume("EXTERN")
    self.consume("RECORD")
    name = self.consume("IDENT")
    c_name = name
    c_name_explicit = False
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        c_name_explicit = True
        token_type = self.peek_type()
        if token_type == "STRLIT":
            raw = self.consume("STRLIT")
            c_name = raw[1:-1]
        elif token_type == "IDENT":
            c_name = self.consume("IDENT")
        else:
            self.error("Expected C type name string after extern record '='")

    layout_size = None
    layout_align = None
    field_offsets: dict[str, int] = {}
    field_sizes: dict[str, int] = {}
    bitfields: dict[str, dict[str, int]] = {}

    if self.peek_type() == "IDENT" and self.peek_text() == "layout":
        self.consume("IDENT")
        if self.peek_type() != "IDENT" or self.peek_text() != "size":
            self.error("Expected 'size' in extern record layout")
        self.consume("IDENT")
        layout_size = _consume_layout_int(self)
        if self.peek_type() != "IDENT" or self.peek_text() != "align":
            self.error("Expected 'align' in extern record layout")
        self.consume("IDENT")
        layout_align = _consume_layout_int(self)
        self.consume("THEN")
        self.skip_newlines()

        while self._not_block_end("END"):
            field_name = self.consume("IDENT")
            if self.peek_type() != "IDENT" or self.peek_text() != "offset":
                self.error("Expected 'offset' in extern record field layout")
            self.consume("IDENT")
            field_offsets[field_name] = _consume_layout_int(self)
            if self.peek_type() != "IDENT" or self.peek_text() != "size":
                self.error("Expected 'size' in extern record field layout")
            self.consume("IDENT")
            field_sizes[field_name] = _consume_layout_int(self)
            if self.peek_type() == "IDENT" and self.peek_text() == "bit_width":
                self.consume("IDENT")
                bit_width = _consume_layout_int(self)
                bit_offset = 0
                if self.peek_type() == "IDENT" and self.peek_text() == "bit_offset":
                    self.consume("IDENT")
                    bit_offset = _consume_layout_int(self)
                bitfields[field_name] = {
                    "width": bit_width,
                    "bit_offset": bit_offset,
                }
            self.skip_newlines()

        self.consume("END")

    return ExternRecordDef(
        name,
        is_opaque=False,
        c_name=c_name,
        c_name_explicit=c_name_explicit,
        layout_size=layout_size,
        layout_align=layout_align,
        field_offsets=field_offsets,
        field_sizes=field_sizes,
        bitfields=bitfields,
    )


def _consume_layout_int(self) -> int:
    text = self.consume("NUMBER")
    return int(text.rstrip("lL"), 0)


def _consume_extern_symbol_name(self) -> str:
    """Consume a C symbol name, including names that tokenize as builtins."""
    if self.pos >= len(self.tokens):
        raise SyntaxError("Unexpected end of input, expected extern symbol name")
    token_type, text, line, col = self.tokens[self.pos]
    if token_type == "IDENT" or token_type in self._CALLABLE_TOKENS:
        self.pos += 1
        return text
    raise SyntaxError(f"Line {line}, Col {col}: Expected IDENT, got {token_type}")


def parse_extern_fn(self) -> ExternFn:
    """Parse foreign function declaration."""
    self.consume("EXTERN")
    if self.peek_type() == "IDENT" and self.peek_text() == "fn":
        self.consume("IDENT")
    elif self.peek_type() == "DEF":
        self.consume("DEF")
    else:
        self.error("Expected 'fn' or 'def' after 'extern'")

    name = self._consume_extern_symbol_name()
    self.consume("LPAREN")

    params: list[tuple[str, str]] = []
    variadic = False
    while self.peek_type() != "RPAREN":
        if params:
            self.consume("COMMA")
        if self.peek_type() == "RANGE_EXCL":
            self.consume("RANGE_EXCL")
            variadic = True
            break
        if self.peek_type() == "DOT":
            self.consume("DOT")
            self.consume("DOT")
            self.consume("DOT")
            variadic = True
            break
        param_name = self.consume("IDENT")
        param_type = "int"
        if self.peek_type() == "COLON":
            self.consume("COLON")
            param_type = self._parse_type_name()
        params.append((param_name, param_type))

    self.consume("RPAREN")

    ret_type = "void"
    if self.peek_type() == "COLON":
        self.consume("COLON")
        ret_type = self._parse_type_name()

    return ExternFn(name, params, ret_type, variadic=variadic)


_exported_parser_module_decl_helpers = (
    parse_use,
    _consume_module_name_part,
    _parse_single_use,
    _parse_use_block,
    parse_import,
    _parse_single_import,
    _parse_optional_import_target,
    _parse_import_block,
    parse_from_import,
    parse_library_decl,
    parse_cinclude,
    parse_link_directive,
    parse_cimport,
    parse_cabi_header,
    _parse_extern_decl,
    _consume_extern_symbol_name,
    parse_extern_var,
    parse_extern_record,
    parse_extern_fn,
)

"""Parser helper implementations extracted from parser.py."""

from __future__ import annotations

from typing import Optional

from token_access import token_type_at

from .ast import (
    ASTNode,
    ParsedType,
    RangeType,
    TypeAlias,
    VarDecl,
)


def _parse_import_statement(self, token_type: Optional[str]) -> Optional[list[ASTNode]]:
    """Parse a single import/use statement at the top of the file.
    Returns list of statements if parsed, None if not an import statement.
    """
    if token_type == "USE":
        use_result = self.parse_use()
        if isinstance(use_result, list):
            return list(use_result)  # Cast to list[ASTNode]
        return [use_result]
    if token_type == "IMPORT":
        import_result = self.parse_import()
        if isinstance(import_result, list):
            return list(import_result)  # Cast to list[ASTNode]
        return [import_result]
    if token_type == "FROM":
        return [self.parse_from_import()]
    if token_type == "LIBRARY":
        return [self.parse_library_decl()]
    if token_type == "CINCLUDE_LINE":
        return [self.parse_cinclude()]
    if token_type == "LINK_LINE":
        return [self.parse_link_directive()]
    if token_type == "CIMPORT_LINE":
        return [self.parse_cimport()]
    if token_type == "IDENT" and self.peek_text() in {"abi", "cabi"}:
        return [self.parse_cabi_header()]
    if token_type in ("IDENT", "UI_INCLUDE") and _is_ui_include(self):
        _skip_ui_include(self)
        return []
    return None


# ── UI DSL block skipping ──
_UI_BLOCK_TAGS: set[str] = {
    "window",
    "background",
    "font",
    "scrollable",
    "panel",
    "dock",
}


def _is_ui_block(self) -> bool:
    """Check if current position is a UI DSL block (tag: ... end or tag name: ... end)."""
    if not self.peek():
        return False
    text = self.peek_text().lower()
    if text not in _UI_BLOCK_TAGS:
        return False
    # Look ahead for COLON (tag:) or IDENT COLON (tag name:)
    for offset in range(1, 4):
        ahead = self.pos + offset
        if ahead >= len(self.tokens):
            return False
        token_type = token_type_at(self.tokens, ahead)
        if token_type == "COLON":
            return True
        if token_type not in ("IDENT", "STRING", "STRLIT"):
            return False
    return False


def _is_ui_include(self) -> bool:
    """Check for canonical UI DSL include lines: include "file.ail"."""
    return (
        self.peek_type() in ("IDENT", "UI_INCLUDE")
        and self.peek_text().lower() == "include"
        and self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) in ("STRING", "STRLIT")
    )


def _skip_ui_include(self) -> None:
    """Skip a canonical UI DSL include line."""
    if self.peek_type() == "UI_INCLUDE":
        self.consume("UI_INCLUDE")
    else:
        self.consume("IDENT")
    if self.peek_type() == "STRLIT":
        self.consume("STRLIT")
    else:
        self.consume("STRING")
    self.skip_newlines()


def _skip_ui_block(self) -> None:
    """Skip a UI DSL block: advances past the matching 'end' token."""
    depth = 0
    while self.peek():
        tt = self.peek_type()
        if tt == "COLON":
            depth += 1
        elif tt == "END" or (tt == "IDENT" and self.peek_text() == "end"):
            depth -= 1
            self.consume()
            self.skip_newlines()
            if depth <= 0:
                return
            continue
        self.consume()


def _parse_definition(self, token_type: Optional[str]) -> Optional[ASTNode]:
    """Parse a single top-level definition (function, class, record, etc).
    Returns the parsed AST node, or None if not a definition token.
    """
    if token_type == "TEMPLATE_BLOCK":
        return self.parse_template_block()
    if token_type == "EXTERN":
        return self._parse_extern_decl()
    if token_type == "OPAQUE":
        return self.parse_opaque_record()
    if token_type == "CINCLUDE_LINE":
        return self.parse_cinclude()
    if token_type == "LINK_LINE":
        return self.parse_link_directive()
    if token_type == "CIMPORT_LINE":
        return self.parse_cimport()
    if token_type == "IDENT" and self.peek_text() in {"abi", "cabi"}:
        return self.parse_cabi_header()
    if token_type == "SECTION":
        self.consume("SECTION")
        if self.peek_type() == "STRLIT":
            section_name = self.consume("STRLIT")[1:-1]
        else:
            section_name = self.consume("IDENT")
        decorators = [f"section({section_name})"]
        self.skip_newlines()
        next_type = self.peek_type()
        if next_type == "EXTERN":
            extern_node = self._parse_extern_decl()
            extern_node.decorators = decorators
            return extern_node
        if next_type == "RECORD":
            rec_node = self.parse_record()
            rec_node.decorators = decorators
            return rec_node
        if next_type == "UNION":
            union_node = self.parse_union()
            union_node.decorators = decorators
            return union_node
        if next_type == "CLASS":
            cls_node = self.parse_class()
            cls_node.decorators = decorators
            return cls_node
        if next_type in ("DEF", "PUBLIC", "PRIVATE", "VOID", "ASYNC", "TEST"):
            return self.parse_function(decorators=decorators)
        self.error(
            f"section attribute must be followed by a declaration, got {next_type}"
        )
    # Handle decorators (both @name style and Fortran-style like @noalias)
    fortran_decorators = {
        "NOALIAS",
        "PURE",
        "INLINE",
        "FASTMATH",
        "UNCHECKED",
        "SYNCHRONIZED",
    }
    if token_type == "AT" or token_type in fortran_decorators:
        decorators = self._parse_decorators()
        # Decorators can precede functions, extern fn, records, unions, or classes
        next_type = self.peek_type()
        if next_type == "EXTERN":
            extern_node = self._parse_extern_decl()
            extern_node.decorators = decorators
            return extern_node
        if next_type == "RECORD":
            rec_node = self.parse_record()
            rec_node.decorators = decorators
            return rec_node
        if next_type == "UNION":
            union_node = self.parse_union()
            union_node.decorators = decorators
            return union_node
        if next_type == "CLASS":
            cls_node = self.parse_class()
            cls_node.decorators = decorators
            return cls_node
        return self.parse_function(decorators=decorators)
    if token_type in ("DEF", "PUBLIC", "PRIVATE", "VOID", "ASYNC", "TEST"):
        # Check if PUBLIC/PRIVATE is followed by a type (variable) or DEF (function)
        if token_type in ("PUBLIC", "PRIVATE"):
            # Lookahead to determine if this is a variable or function
            next_type = (
                token_type_at(self.tokens, self.pos + 1)
                if self.pos + 1 < len(self.tokens)
                else None
            )
            if next_type in self._TYPE_TOKENS or next_type == "CONST":
                # This is a public/private variable declaration
                return self._parse_global_var()
        return self.parse_function()
    if token_type == "RECORD":
        return self.parse_record()
    if token_type == "UNION":
        return self.parse_union()
    if token_type == "ENUM":
        return self.parse_enum()
    if token_type == "CLASS":
        return self.parse_class()
    if token_type in ("TYPE", "TYPEDEF"):
        return self._parse_type_alias()
    is_slice_like_global = (
        token_type == "IDENT"
        and self.peek_text() in ("slice", "view")
        and self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "LBRACKET"
    )
    is_ident_type_prefix_function = (
        token_type == "IDENT"
        and self._is_type_ident(self.peek_text())
        and self.pos + 2 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "IDENT"
        and token_type_at(self.tokens, self.pos + 2) == "LPAREN"
    )
    if is_ident_type_prefix_function:
        return self.parse_function()
    is_internal_function = (
        token_type == "IDENT"
        and self.peek_text() == "internal"
        and self.pos + 3 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1)
        in (self._TYPE_TOKENS | {"IDENT", "VOID", "PTR"})
        and token_type_at(self.tokens, self.pos + 2) == "IDENT"
        and token_type_at(self.tokens, self.pos + 3) == "LPAREN"
    )
    if is_internal_function:
        return self.parse_function()
    if (
        token_type == "CONST"
        or token_type == "STATIC"
        or token_type in self._TYPE_TOKENS
        or is_slice_like_global
    ):
        # Check if this is a type-prefix function: TYPE IDENT LPAREN
        if (
            (token_type in self._TYPE_TOKENS or is_slice_like_global)
            and self.pos + 2 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "IDENT"
            and token_type_at(self.tokens, self.pos + 2) == "LPAREN"
        ):
            # This is a type-prefix function definition (e.g., int add():)
            return self.parse_function()
        return self._parse_global_var()
    # Support bare top-level constants: MY_CONST = 42
    if token_type == "IDENT":
        return self._parse_bare_global_const()
    return None


def parse_program(self) -> list[ASTNode]:
    """Parse entire program"""
    try:
        return self._parse_program_impl()
    except RecursionError:
        # Convert Python's RecursionError to a friendlier SyntaxError
        line = self.peek_line() if self.peek() else 0
        col = self.peek_col() if self.peek() else 0
        raise SyntaxError(
            f"Line {line}, Col {col}: Expression too deeply nested. "
            f"Maximum nesting depth exceeded (limit: {self.MAX_PARSE_DEPTH}). "
            "This may indicate malicious input or malformed code."
        ) from None


def _parse_program_impl(self) -> list[ASTNode]:
    """Internal implementation of parse_program."""
    statements: list[ASTNode] = []
    self.skip_newlines()

    # Parse imports/use at the top of the file
    while self.peek():
        token_type = self.peek_type()
        result = self._parse_import_statement(token_type)
        if result is None:
            break
        statements.extend(result)
        self.skip_newlines()

    # Parse the rest of the program
    while self.peek():
        token_type = self.peek_type()
        if token_type in ("IDENT", "UI_INCLUDE") and _is_ui_include(self):
            _skip_ui_include(self)
            continue
        # Skip UI DSL blocks at top level (background:, font:, window:, etc.)
        if token_type == "IDENT" and self._is_ui_block():
            self._skip_ui_block()
            self.skip_newlines()
            continue
        node = self._parse_definition(token_type)
        if node is None:
            # Don't silently truncate - raise an error for unrecognized code
            line = self.peek_line() if self.peek() else 0
            col = self.peek_col() if self.peek() else 0
            token_text = self.peek_text() if self.peek() else "EOF"
            raise SyntaxError(
                f"Line {line}, Col {col}: Unexpected token '{token_text}' at top level. "
                f"Expected function, class, record, enum, or variable declaration."
            )
        statements.append(node)
        self.skip_newlines()
    return statements


def _parse_global_var(self) -> VarDecl:
    """Parse a global variable declaration at module level.

    Supports:
        int GLOBAL_VAR = 42
        const int MAX_SIZE = 100
        float PI = 3.14159
        public int EXPORTED = 1
    """
    is_const = False
    is_public = False

    # Check for modifiers
    while self.peek_type() in ("CONST", "STATIC", "PUBLIC", "PRIVATE"):
        modifier = self.consume()
        if modifier.lower() == "const":
            is_const = True
        elif modifier.lower() == "static":
            # static = explicit mutable global (is_const stays False)
            pass
        elif modifier.lower() == "public":
            is_public = True
        # PRIVATE is default, no action needed

    # Parse the type
    var_type = self.parse_type()

    # Parse the variable name
    var_name = self.consume("IDENT")

    # Require initialization for globals
    self.consume("ASSIGN")
    init_value = self.parse_expression()

    return VarDecl(
        var_type, var_name, init_value, is_const=is_const, is_public=is_public
    )


def _parse_bare_global_const(self) -> Optional[VarDecl]:
    """Parse a bare top-level constant: MY_CONST = 42

    This allows Python/Ruby style constants without explicit type.
    The type is inferred from the value.
    Only allows if next token after IDENT is ASSIGN (to distinguish from calls).
    Arrays are mutable by default, scalars are const.
    """
    # Peek ahead to check if this is really an assignment
    if self.pos + 1 >= len(self.tokens):
        return None
    if token_type_at(self.tokens, self.pos + 1) != "ASSIGN":
        return None

    # It's an assignment - parse as constant
    var_name = self.consume("IDENT")
    self.consume("ASSIGN")
    init_value = self.parse_expression()

    # Infer type from value
    from parser.ast import ArrayLit, Bool, Number, StringLit

    # Arrays are mutable by default (you usually want to modify them)
    # Scalars are const by default (Python/Ruby style constants)
    # Exception: names starting with _ are private mutable state (not constants)
    is_const = not isinstance(init_value, ArrayLit) and not var_name.startswith("_")

    if isinstance(init_value, Number):
        var_type = "double" if isinstance(init_value.value, float) else "i64"
    elif isinstance(init_value, StringLit):
        var_type = "string"
    elif isinstance(init_value, Bool):
        var_type = "bool"
    elif isinstance(init_value, ArrayLit):
        var_type = "array"  # Will be handled specially in codegen
    else:
        var_type = "i64"  # Default

    return VarDecl(var_type, var_name, init_value, is_const=is_const, is_public=False)


def _parse_type_alias_target(self) -> ASTNode | ParsedType:
    """Parse the right-hand side of a type alias."""
    if self._lookahead_is_range_decl(self.pos):
        low = self._parse_range_bound()
        exclusive = self.peek_type() == "RANGE_EXCL"
        if exclusive:
            self.consume("RANGE_EXCL")
        else:
            self.consume("RANGE")
        high = self._parse_range_bound()
        return RangeType(low, high, exclusive)
    return self.parse_type()


def _parse_type_alias(self) -> TypeAlias:
    """Parse type aliases.

    Supported forms:
        type Percent = 0..100
        typedef Count = int
        typedef int Count
    """
    token_type = self.peek_type()
    if token_type == "TYPE":
        self.consume("TYPE")
        type_name = self.consume("IDENT")
        self.consume("ASSIGN")
        target_type = self._parse_type_alias_target()
        return TypeAlias(type_name, target_type)

    self.consume("TYPEDEF")
    if (
        self.peek_type() == "IDENT"
        and self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "ASSIGN"
    ):
        type_name = self.consume("IDENT")
        self.consume("ASSIGN")
        target_type = self._parse_type_alias_target()
        return TypeAlias(type_name, target_type)

    target_type = self._parse_type_alias_target()
    type_name = self.consume("IDENT")

    return TypeAlias(type_name, target_type)

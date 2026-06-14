"""Type and parameter parsing helpers for Parser."""

from __future__ import annotations

from parser.ast import ASTNode, ParsedType
from typing import Optional

from token_access import token_line_at, token_type_at


def _parse_single_param(self) -> tuple[str, ParsedType, Optional[ASTNode]]:
    """Parse a single function parameter with optional default value.

    Supports:
        name              -> (name, i64, None)
        name: type        -> (name, type, None)
        type name         -> (name, type, None)
        name = default    -> (name, i64, default)
        name: type = default -> (name, type, default)
    """
    param_type: ParsedType = "i64"
    param_name: str
    default_value: Optional[ASTNode] = None

    # Check if type comes first (builtin types or custom type identifiers)
    is_type_first = self.peek_type() in (
        "UNSIGNED",
        "TINY",
        "BYTE",
        "SMALL",
        "USMALL",
        "SHORT",
        "USHORT",
        "INT",
        "UINT",
        "LONG",
        "ULONG",
        "WIDE",
        "UWIDE",
        "VAST",
        "UVAST",
        "GRAND",
        "UGRAND",
        "GIANT",
        "UGIANT",
        "TITAN",
        "UTITAN",
        "COLOS",
        "UCOLOS",
        "UNBOUNDED",
        "DICT",
        "MAP",
        "VEC16B",
        "VEC32B",
        "VEC64B",
        "VEC4I",
        "VEC8I",
        "VEC16I",
        "VEC2L",
        "VEC4L",
        "VEC8L",
        "VEC4F",
        "VEC8F",
        "VEC2D",
        "VEC4D",
        "FLOAT_T",
        "DOUBLE",
        "QUAD",
        "BOOL",
        "STRING",
        "LBRACKET",  # Array type: [int]
    )
    if (
        not is_type_first
        and self.peek_type() == "IDENT"
        and self._is_type_ident(self.peek_text())
        and self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "IDENT"
    ):
        is_type_first = True
    if (
        not is_type_first
        and self.peek_type() == "IDENT"
        and self.peek_text()
        in (
            "slice",
            "view",
        )
    ):
        if (
            self.pos + 1 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "LBRACKET"
        ):
            is_type_first = True

    # Also check for PascalCase/UPPERCASE identifiers followed by another IDENT
    # This handles: AST node, MyClass obj, Point p, etc.
    if not is_type_first and self.peek_type() == "IDENT":
        text = self.peek_text()
        # Look ahead to see if next token is also IDENT (type name pattern)
        # Allow PascalCase (MyClass) or all-caps (AST) as type names
        if text and text[0].isupper():
            # Save position
            saved_pos = self.pos
            self.consume("IDENT")  # consume potential type
            if self.peek_type() == "IDENT":
                # Yes, it's "Type name" pattern
                self.pos = saved_pos  # restore
                is_type_first = True
            else:
                # Not a type, restore position
                self.pos = saved_pos

    if is_type_first:
        param_type = self.parse_type()
        param_name = self.consume("IDENT")
    else:
        param_name = self.consume("IDENT")
        # Check for "name type" or "name: type" syntax
        if self.peek_type() == "COLON":
            self.consume("COLON")
            param_type = self.parse_type()
        elif self.peek_type() in (
            "UNSIGNED",
            "TINY",
            "BYTE",
            "SMALL",
            "USMALL",
            "SHORT",
            "USHORT",
            "INT",
            "UINT",
            "LONG",
            "ULONG",
            "WIDE",
            "UWIDE",
            "VAST",
            "UVAST",
            "GRAND",
            "UGRAND",
            "GIANT",
            "UGIANT",
            "TITAN",
            "UTITAN",
            "COLOS",
            "UCOLOS",
            "UNBOUNDED",
            "DICT",
            "MAP",
            "VEC16B",
            "VEC32B",
            "VEC64B",
            "VEC4I",
            "VEC8I",
            "VEC16I",
            "VEC2L",
            "VEC4L",
            "VEC8L",
            "VEC4F",
            "VEC8F",
            "VEC2D",
            "VEC4D",
            "FLOAT_T",
            "DOUBLE",
            "QUAD",
            "BOOL",
            "STRING",
            "LBRACKET",
        ) or (self.peek_type() == "IDENT" and self._is_type_ident(self.peek_text())):
            param_type = self.parse_type()

    # Check for default value
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        default_value = self.parse_expression()

    return (param_name, param_type, default_value)


def _parse_type_name(self) -> str:
    """Parse a simple type name for extern fn declarations.

    Returns the AILang type name as a string.
    """
    token_type = self.peek_type()
    if token_type in self._TYPE_TOKENS:
        return self.consume().lower()
    if token_type == "VOID":
        return self.consume().lower()
    if token_type == "PTR":
        return self.consume().lower()
    if token_type == "IDENT" and self.peek_text() == "pointer":
        self.consume("IDENT")
        return "ptr"
    if token_type == "IDENT":
        return self.consume()
    self.error(f"Expected type name, got {self.peek_text()!r}")
    return "int"  # Unreachable, but makes mypy happy


def _is_type_token(self) -> bool:
    """Check if current token is a type token."""
    pt = self.peek_type()
    if pt == "LBRACKET":
        return True
    if pt == "IDENT" and self.peek_text() in ("slice", "view"):
        return (
            self.pos + 1 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "LBRACKET"
        )
    return pt in (
        "UNSIGNED",
        "TINY",
        "BYTE",
        "SMALL",
        "USMALL",
        "SHORT",
        "USHORT",
        "INT",
        "UINT",
        "LONG",
        "ULONG",
        "WIDE",
        "UWIDE",
        "VAST",
        "UVAST",
        "GRAND",
        "UGRAND",
        "GIANT",
        "UGIANT",
        "TITAN",
        "UTITAN",
        "COLOS",
        "UCOLOS",
        "UNBOUNDED",
        "DICT",
        "MAP",
        "VEC16B",
        "VEC32B",
        "VEC64B",
        "VEC4I",
        "VEC8I",
        "VEC16I",
        "VEC2L",
        "VEC4L",
        "VEC8L",
        "VEC4F",
        "VEC8F",
        "VEC2D",
        "VEC4D",
        "FLOAT_T",
        "DOUBLE",
        "QUAD",
        "BOOL",
        "STRING",
        "VOID",
    ) or (self.peek_type() == "IDENT" and self._is_type_ident(self.peek_text()))


def parse_type(self) -> ParsedType:
    """Parse type specification with optional 'unsigned' and aliases."""
    token_type = self.peek_type()
    if not token_type:
        self.error("Expected type")

    # Callback/function-pointer type:
    #   fn(x: int, ctx: ptr): int @stdcall
    if token_type == "IDENT" and self.peek_text() == "fn":
        self.consume("IDENT")
        self.consume("LPAREN")
        params: list[tuple[str, ParsedType]] = []
        while self.peek_type() != "RPAREN":
            if params:
                self.consume("COMMA")
            param_name = self.consume("IDENT")
            param_type: ParsedType = "i64"
            if self.peek_type() == "COLON":
                self.consume("COLON")
                param_type = self.parse_type()
            params.append((param_name, param_type))
        self.consume("RPAREN")
        ret_type: ParsedType = "void"
        if self.peek_type() == "COLON":
            self.consume("COLON")
            ret_type = self.parse_type()
        decorator_line = token_line_at(self.tokens, self.pos - 1) if self.pos > 0 else 0
        decorators: list[str] = []
        while self.peek_type() == "AT" and self.peek_line() == decorator_line:
            self.consume("AT")
            decorators.append(self.consume("IDENT").lstrip("@").lower())
        return ("fn", params, ret_type, tuple(decorators))

    # Array type: [type] or fixed-size [type;N]
    if token_type == "LBRACKET":
        self.consume("LBRACKET")
        elem_type = self.parse_type()
        if self.peek_type() == "SEMICOLON":
            self.consume("SEMICOLON")
            if self.peek_type() != "NUMBER":
                self.error("Expected fixed array size after ';'")
            size_token = self.consume("NUMBER")
            try:
                fixed_size = int(size_token, 0)
            except ValueError:
                self.error(f"Invalid fixed array size: {size_token!r}")
            if fixed_size <= 0:
                self.error("Fixed array size must be positive")
            self.consume("RBRACKET")
            return ("fixed_array", elem_type, fixed_size)
        self.consume("RBRACKET")
        return ("array", elem_type)

    # Slice/view type: slice[T], view[T]
    if token_type == "IDENT" and self.peek_text() in ("slice", "view"):
        is_bracketed = (
            self.pos + 1 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "LBRACKET"
        )
        if is_bracketed:
            self.consume("IDENT")
            self.consume("LBRACKET")
            elem_type = self.parse_type()
            self.consume("RBRACKET")
            return ("slice", elem_type)

    # Handle optional 'unsigned'
    is_unsigned = False
    if token_type == "UNSIGNED":
        is_unsigned = True
        self.consume("UNSIGNED")
        token_type = self.peek_type() or ""

        # If nothing follows, default to unsigned 128-bit
        if token_type not in (
            "INT",
            "LONG",
            "SHORT",
            "USHORT",
            "UINT",
            "ULONG",
            "FLOAT_T",
            "DOUBLE",
            "QUAD",
            "BOOL",
            "STRING",
            "VOID",
            "IDENT",
            # Extended type keywords
            "TINY",
            "BYTE",
            "SMALL",
            "USMALL",
            "WIDE",
            "UWIDE",
            "VAST",
            "UVAST",
            "GRAND",
            "UGRAND",
            "GIANT",
            "UGIANT",
            "TITAN",
            "UTITAN",
            "COLOS",
            "UCOLOS",
            "UNBOUNDED",
            "DICT",
            "MAP",
            "VEC16B",
            "VEC32B",
            "VEC64B",
            "VEC4I",
            "VEC8I",
            "VEC16I",
            "VEC2L",
            "VEC4L",
            "VEC8L",
            "VEC4F",
            "VEC8F",
            "VEC2D",
            "VEC4D",
            "PTR",
        ):
            return "u128"

    alias_map = {
        # 8-bit
        "TINY": "i8",  # signed 8-bit
        "BYTE": "u8",  # unsigned 8-bit (special case)
        # 16-bit
        "SMALL": "i16",  # signed 16-bit
        "USMALL": "u16",  # unsigned 16-bit
        # 32-bit
        "SHORT": "i32",
        "USHORT": "u32",
        # 64-bit
        "INT": "i64",
        "UINT": "u64",
        # 128-bit
        "LONG": "i128",
        "ULONG": "u128",
        # 256-bit
        "WIDE": "i256",
        "UWIDE": "u256",
        # 512-bit
        "VAST": "i512",
        "UVAST": "u512",
        # 1024-bit
        "GRAND": "i1024",
        "UGRAND": "u1024",
        # 2048-bit
        "GIANT": "i2048",
        "UGIANT": "u2048",
        # 4096-bit
        "TITAN": "i4096",
        "UTITAN": "u4096",
        # 8192-bit (1KB integers!)
        "COLOS": "i8192",
        "UCOLOS": "u8192",
        "UNBOUNDED": "unbounded",
        "DICT": "dict",
        "MAP": "dict",
        "VEC16B": "vec16b",
        "VEC32B": "vec32b",
        "VEC64B": "vec64b",
        "VEC4I": "vec4i",
        "VEC8I": "vec8i",
        "VEC16I": "vec16i",
        "VEC2L": "vec2l",
        "VEC4L": "vec4l",
        "VEC8L": "vec8l",
        "VEC4F": "vec4f",
        "VEC8F": "vec8f",
        "VEC2D": "vec2d",
        "VEC4D": "vec4d",
        # Floats
        "FLOAT_T": "f32",
        "DOUBLE": "f64",
        "QUAD": "f128",
        "BOOL": "i1",
        "STRING": "string",
        "VOID": "void",
        "PTR": "ptr",
        "NULLPTR": "ptr",
        "ARRAY": "array",  # Dynamic array type
    }

    if token_type in (
        "TINY",
        "BYTE",
        "SMALL",
        "USMALL",
        "SHORT",
        "USHORT",
        "INT",
        "UINT",
        "LONG",
        "ULONG",
        "WIDE",
        "UWIDE",
        "VAST",
        "UVAST",
        "GRAND",
        "UGRAND",
        "GIANT",
        "UGIANT",
        "TITAN",
        "UTITAN",
        "COLOS",
        "UCOLOS",
        "UNBOUNDED",
        "DICT",
        "MAP",
        "VEC16B",
        "VEC32B",
        "VEC64B",
        "VEC4I",
        "VEC8I",
        "VEC16I",
        "VEC2L",
        "VEC4L",
        "VEC8L",
        "VEC4F",
        "VEC8F",
        "VEC2D",
        "VEC4D",
        "FLOAT_T",
        "DOUBLE",
        "QUAD",
        "BOOL",
        "STRING",
        "VOID",
        "PTR",
        "ARRAY",
    ):
        type_token = token_type
        self.consume(type_token)
        base = alias_map.get(type_token, type_token.lower())
        if is_unsigned:
            if base.startswith("i"):
                base = "u" + base[1:]
            elif base.startswith("u") or base.startswith("f"):
                pass
            elif base in ("string", "void", "ptr", "ptrptr", "array"):
                pass
            else:
                base = "u" + base
        return base

    if token_type == "IDENT":
        ident_name = self.peek_text()
        custom_aliases = {
            # All cute names as lowercase identifiers
            "tiny": "i8",
            "byte": "u8",
            "small": "i16",
            "usmall": "u16",
            "short": "i32",
            "ushort": "u32",
            "int": "i64",
            "uint": "u64",
            "long": "i128",
            "ulong": "u128",
            "wide": "i256",
            "uwide": "u256",
            "vast": "i512",
            "uvast": "u512",
            "grand": "i1024",
            "ugrand": "u1024",
            "giant": "i2048",
            "ugiant": "u2048",
            "titan": "i4096",
            "utitan": "u4096",
            "colos": "i8192",
            "ucolos": "u8192",
            "unbounded": "unbounded",
            "dict": "dict",
            "map": "dict",
            "vec16b": "vec16b",
            "vec32b": "vec32b",
            "vec64b": "vec64b",
            "vec4i": "vec4i",
            "vec8i": "vec8i",
            "vec16i": "vec16i",
            "vec2l": "vec2l",
            "vec4l": "vec4l",
            "vec8l": "vec8l",
            "vec4f": "vec4f",
            "vec8f": "vec8f",
            "vec2d": "vec2d",
            "vec4d": "vec4d",
            "ptrptr": "ptrptr",
            "charpp": "charpp",
            "fileptr": "fileptr",
            "size_tp": "size_tp",
            "pointer": "ptr",
            "str_array": "str_array",
        }
        # Treat as type only if it's a known alias or a type-like ident
        if ident_name in custom_aliases or self._is_type_ident(ident_name):
            type_name = self.consume("IDENT")
            base = custom_aliases.get(type_name, type_name)
            if is_unsigned and base.startswith("i"):
                base = "u" + base[1:]
            return base
        # Otherwise, if we had 'unsigned' with no explicit type, default to u128
        if is_unsigned:
            return "u128"
        # Fallback: treat as custom type name
        type_name = self.consume("IDENT")
        return type_name

    self.error(f"Expected type, got {token_type}")
    raise AssertionError("unreachable")


# Keep strict static checks from reporting this module as dead code when
# methods are consumed through Parser delegation wrappers.
_exported_parser_type_helpers = (
    _parse_single_param,
    _parse_type_name,
    _is_type_token,
    parse_type,
)

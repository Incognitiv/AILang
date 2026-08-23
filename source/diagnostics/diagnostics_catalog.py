"""Pattern and builtin catalogs used by diagnostics."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from diagnostics.diagnostics_catalog_builtins import BUILTINS

# Token sequence patterns: (pattern) -> (message, suggested_fix)
PATTERN_FIXES: Dict[Tuple[str, ...], Tuple[Optional[str], Optional[str]]] = {
    # Assignment in condition (should be ==)
    ("IF", "IDENT", "ASSIGN", "NUMBER", "THEN"): (
        "Assignment in condition. Use '==' for comparison.",
        "if x == 5 then",
    ),
    ("IF", "IDENT", "ASSIGN", "IDENT", "THEN"): (
        "Assignment in condition. Use '==' for comparison.",
        "if x == y then",
    ),
    ("WHILE", "IDENT", "ASSIGN", "NUMBER", "THEN"): (
        "Assignment in condition. Use '==' for comparison.",
        "while x == 5 then",
    ),
    # Missing 'then' after condition
    ("IF", "IDENT", "EQ", "NUMBER", "NEWLINE"): (
        "Missing 'then' after condition.",
        "if x == 5 then",
    ),
    ("IF", "IDENT", "GT", "NUMBER", "NEWLINE"): (
        "Missing 'then' after condition.",
        "if x > 5 then",
    ),
    # (for x in range is valid in AILang — no false positive here)
    # Missing 'end' patterns (detected by unbalanced blocks)
    ("DEF", "IDENT", "LPAREN"): (None, None),  # Not an error, just track block start
}

# Single token mistakes
TOKEN_HINTS: Dict[str, Tuple[str, Optional[str]]] = {
    "elif": ("AILang uses 'elsif', not 'elif'.", "elsif"),
    "else if": ("AILang uses 'elsif', not 'else if'.", "elsif"),
    "elseif": ("AILang uses 'elsif', not 'elseif'.", "elsif"),
    "func": ("AILang uses 'def', not 'func'.", "def"),
    "function": ("AILang uses 'def', not 'function'.", "def"),
    "fn": ("AILang uses 'def', not 'fn'.", "def"),
    "let": ("AILang doesn't need 'let'. Just use: x = 5", "x = 5"),
    "var": ("AILang doesn't need 'var'. Just use: x = 5", "x = 5"),
    "none": ("AILang uses 'null', not 'none'.", "null"),
    "None": ("AILang uses 'null', not 'None'.", "null"),
    "True": ("AILang uses 'true', not 'True'.", "true"),
    "False": ("AILang uses 'false', not 'False'.", "false"),
    "&&": ("AILang uses 'and', not '&&'.", "and"),
    "||": ("AILang uses 'or', not '||'.", "or"),
    "!": ("AILang uses 'not', not '!'.", "not"),
}


# -----------------------------------------------------------------------------
# Built-in symbols - what's always available
# -----------------------------------------------------------------------------


# Non-callable language surface symbols.
RESERVED_KEYWORDS = frozenset(
    {
        "if",
        "then",
        "elsif",
        "otherwise",
        "else",
        "end",
        "while",
        "do",
        "until",
        "unless",
        "for",
        "foreach",
        "in",
        "loop",
        "repeat",
        "times",
        "def",
        "return",
        "class",
        "record",
        "enum",
        "new",
        "this",
        "public",
        "private",
        "const",
        "static",
        "and",
        "or",
        "not",
        "is",
        "band",
        "bor",
        "bxor",
        "bnot",
        "nand",
        "nor",
        "xnor",
        "shl",
        "shr",
        "ushr",
        "true",
        "false",
        "null",
        "nil",
        "try",
        "catch",
        "except",
        "finally",
        "throw",
        "import",
        "from",
        "as",
        "use",
        "break",
        "continue",
        "match",
        "case",
        "default",
        "spawn",
        "join",
        "atomic",
        "channel",
        "async",
        "await",
        "unsafe",
        "type",
        "typedef",
        "where",
        "comptime",
        "static_assert",
        "extern",
        "test",
        "assert",
        "asm",
        "section",
        "infinity",
        "internal",
    }
)

TYPE_NAMES = frozenset(
    {
        "int",
        "uint",
        "float",
        "double",
        "quad",
        "bool",
        "string",
        "void",
        "ptr",
        "pointer",
        "ptrptr",
        "tiny",
        "byte",
        "small",
        "usmall",
        "short",
        "ushort",
        "long",
        "ulong",
        "wide",
        "uwide",
        "vast",
        "uvast",
        "grand",
        "ugrand",
        "giant",
        "ugiant",
        "titan",
        "utitan",
        "colos",
        "ucolos",
        "unbounded",
        "unsigned",
        "fn",
        "array",
        "dict",
        "map",
    }
)

VECTOR_TYPE_NAMES = frozenset(
    {
        "vec16b",
        "vec32b",
        "vec64b",
        "vec4i",
        "vec8i",
        "vec16i",
        "vec2l",
        "vec4l",
        "vec8l",
        "vec4f",
        "vec8f",
        "vec2d",
        "vec4d",
    }
)

DECORATOR_IDENTIFIERS = frozenset(
    {
        "noinline",
        "export",
        "effect",
        "abi",
        "cabi",
        "c_abi",
        "header_declared",
        "packed",
        "stdcall",
        "fastcall",
    }
)

LANGUAGE_SURFACE = frozenset(BUILTINS)
CALLABLE_BUILTINS = frozenset(
    BUILTINS
    - RESERVED_KEYWORDS
    - TYPE_NAMES
    - VECTOR_TYPE_NAMES
    - DECORATOR_IDENTIFIERS
)

# Type tokens that can prefix function definitions (e.g., "int add():")
TYPE_PREFIX_TOKENS = frozenset(
    {
        "VOID",
        "INT",
        "UINT",
        "STRING",
        "BOOL",
        "FLOAT_T",
        "DOUBLE",
        "ARRAY",
        "TINY",
        "BYTE",
        "SMALL",
        "USMALL",
        "SHORT",
        "USHORT",
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
        "QUAD",
        "UNBOUNDED",
        "DICT",
        "MAP",
        "UNSIGNED",
    }
)


# -----------------------------------------------------------------------------
# Diagnostic - represents a single issue with suggestion
# -----------------------------------------------------------------------------

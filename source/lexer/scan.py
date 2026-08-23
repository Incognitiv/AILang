"""
AILang Lexer - Tokenization
Converts AILang source code into tokens
"""

import re
import string
from typing import Pattern

from token_access import token_type_at

from .unicode_security import check_unicode_security

# Contextual keywords: These are only treated as keywords when followed by '('
# Otherwise they become regular IDENT tokens (can be used as variable names)
CONTEXTUAL_KEYWORDS: set[str] = {
    "LEN",
    "STRLEN",
    "PRINT",
    "PUTS",
    "PUTC",
    "INPUT",
    "CHAR_AT",
    "ORD",
    "CHR",
    "SUBSTR",
    "READ_FILE",
    "WRITE_FILE",
    "SQL_OPEN",
    "SQL_EXEC",
    "SQL_QUERY",
    "SQL_CLOSE",
    "ALLOC",
    "DEALLOC",
    "PEEK64",
    "POKE64",
    "PEEK32",
    "POKE32",
    "PEEK8",
    "POKE8",
    "CHANNEL",
    "CHAN_SEND",
    "CHAN_RECV",
    "CHAN_TRY_SEND",
    "CHAN_TRY_RECV",
    "CHAN_CLOSE",
    "SPAWN",
    "JOIN",
}

TOKEN_PATTERNS = [
    ("COMMENT_BLOCK", r"/\*.*?\*/"),  # Block comments /* */
    ("COMMENT", r"//[^\n]*"),  # Line comments //
    # Template system (must be before HASH_COMMENT - L5 fix)
    ("TEMPLATE_START", r"#template\b"),
    ("TEMPLATE_END", r"#end\b"),
    # FFI directives (must be before HASH_COMMENT)
    ("CINCLUDE", r"#cinclude\b"),
    ("CIMPORT", r"#cimport\b"),
    ("LINK_DIR", r"#link\b"),
    ("HASH_COMMENT", r"#[^\n]*"),  # Line comments starting with # (non-template)
    ("FLOAT", r"\d+\.\d+([eE][+-]?\d+)?[fFdDqQ]?"),  # Floats before int
    # Integers: hex 0x.., bin 0b.., oct 0o.., decimal, optional long suffix
    ("NUMBER", r"0[xX][0-9A-Fa-f]+[lL]?|0[bB][01]+[lL]?|0[oO][0-7]+[lL]?|\d+[lL]?"),
    ("EXTERN", r"\bextern\b"),  # Foreign function/variable declaration
    ("REINTERPRET", r"\breinterpret\b"),  # Reinterpret cast
    ("BITCAST", r"\bbitcast\b"),  # Bit-level cast
    ("DEF", r"\bdef\b"),
    ("ASYNC", r"\basync\b"),  # Async function declaration
    ("AWAIT", r"\bawait\b"),  # Await expression
    ("TEST", r"\btest\b"),  # Test function declaration
    ("ASSERT", r"\bassert\b"),  # Assert statement
    ("END", r"\bend\b"),
    ("IF", r"\bif\b"),
    ("THEN", r"\bthen\b"),
    ("ELSIF", r"\belsif\b"),
    ("OTHERWISE", r"\botherwise\b"),  # British-style elsif alias
    ("ELSE", r"\belse\b"),
    ("WHILE", r"\bwhile\b"),
    ("DO", r"\bdo\b"),  # do-while loop (body-first, LLVM-optimal)
    ("UNTIL", r"\buntil\b"),  # Ruby-style negative while
    ("UNLESS", r"\bunless\b"),  # Ruby-style negative if
    ("FOR", r"\bfor\b"),
    ("LOOP", r"\bloop\b"),
    ("FOREACH", r"\bforeach\b"),
    ("IN", r"\bin\b"),
    ("REPEAT", r"\brepeat\b"),
    ("TIMES", r"\btimes\b"),
    ("BREAK", r"\bbreak\b"),
    ("CONTINUE", r"\bcontinue\b"),
    ("RETURN", r"\breturn\b"),
    # Threading/Concurrency
    ("SPAWN", r"\bspawn\b"),  # Create new thread
    ("JOIN", r"\bjoin\b"),  # Wait for thread completion
    ("ATOMIC", r"\batomic\b"),  # Atomic operations
    # Channels (Go-style message passing)
    ("CHANNEL", r"\bchannel\b"),  # Create channel
    ("CHAN_SEND", r"\bchan_send\b"),  # Send to channel
    ("CHAN_RECV", r"\bchan_recv\b"),  # Receive from channel
    ("CHAN_TRY_SEND", r"\bchan_try_send\b"),  # Non-blocking send
    ("CHAN_TRY_RECV", r"\bchan_try_recv\b"),  # Non-blocking receive
    ("CHAN_CLOSE", r"\bchan_close\b"),  # Close channel
    ("TRY", r"\btry\b"),
    ("CATCH", r"\bcatch\b"),
    ("EXCEPT", r"\bexcept\b"),
    ("FINALLY", r"\bfinally\b"),
    ("THROW", r"\bthrow\b"),
    ("PRINT", r"\bprint\b"),
    ("PUTS", r"\bputs\b"),
    ("PUTC", r"\bputc\b"),  # Print single character (no newline)
    ("LEN", r"\blen\b"),
    ("CHAR_AT", r"\bchar_at\b"),
    ("ORD", r"\bord\b"),
    ("CHR", r"\bchr\b"),
    ("SUBSTR", r"\bsubstr\b"),
    ("STRLEN", r"\bstrlen\b"),
    ("INPUT", r"\binput\b"),
    ("READ_FILE", r"\bread_file\b"),
    ("WRITE_FILE", r"\bwrite_file\b"),
    ("SQL_OPEN", r"\bsql_open\b"),
    ("SQL_EXEC", r"\bsql_exec\b"),
    ("SQL_QUERY", r"\bsql_query\b"),
    ("SQL_CLOSE", r"\bsql_close\b"),
    # Memory allocation (RAII-compatible)
    ("ALLOC", r"\balloc\b"),  # Allocate bytes, returns ptr
    ("DEALLOC", r"\bdealloc\b"),  # Free allocated memory
    ("PEEK64", r"\bpeek64\b"),  # Read i64 from ptr+offset
    ("POKE64", r"\bpoke64\b"),  # Write i64 to ptr+offset
    ("PEEK32", r"\bpeek32\b"),  # Read u32 from ptr+offset
    ("POKE32", r"\bpoke32\b"),  # Write u32 to ptr+offset
    ("PEEK8", r"\bpeek8\b"),  # Read u8 from ptr+offset
    ("POKE8", r"\bpoke8\b"),  # Write u8 to ptr+offset
    ("ASM", r"\basm\b"),  # Inline assembly
    ("SECTION", r"\bsection\b"),  # Section attribute
    ("UNSAFE", r"\bunsafe\b"),  # Explicit unsafe operation marker
    ("ARRAY", r"\barray\b"),  # Array type
    ("CONST", r"\bconst\b"),
    ("PUBLIC", r"\bpublic\b"),
    ("PRIVATE", r"\bprivate\b"),
    ("TRUE", r"\btrue\b"),
    ("FALSE", r"\bfalse\b"),
    ("NULL", r"\bnull\b"),
    ("NULL", r"\bnil\b"),  # Ruby-style alias
    ("NULLPTR", r"\bnullptr\b"),
    ("AND", r"\band\b"),
    ("OR", r"\bor\b"),
    ("NOT", r"\bnot\b"),
    (
        "IS_NOT",
        r"\bis[ \t]+not\b",
    ),  # is not (must come before IS) - M23 fix: no newlines
    ("IS", r"\bis\b"),  # is (equality alias)
    ("INFINITY", r"\binfinity\b"),  # Unbounded loop marker
    # Note: 'max' is NOT a keyword - it's handled contextually in parser
    # to avoid breaking code that uses 'max' as a function/variable name
    ("BOUNDED", r"@bounded"),  # Decorator: @bounded(N)
    ("BOUND", r"@bound"),  # Auto-infer bound from condition
    # Fortran-style optimization decorators
    ("NOALIAS", r"@noalias"),  # Arrays don't overlap - enables vectorization
    ("PURE", r"@pure"),  # No side effects - enables more optimizations
    ("SYNCHRONIZED", r"@synchronized"),  # Auto mutex lock/unlock (Ada protected)
    ("INLINE", r"@inline"),  # Suggest inlining
    ("FASTMATH", r"@fastmath"),  # Allow fast-math optimizations (less precise)
    ("UNCHECKED", r"@unchecked"),  # Disable overflow/bounds checks for max speed
    ("MATCH", r"\bmatch\b"),
    ("CASE", r"\bcase\b"),
    ("OPAQUE", r"\bopaque\b"),
    ("USE", r"\buse\b"),
    ("IMPORT", r"\bimport\b"),
    ("UI_INCLUDE", r"\binclude\b"),
    ("FROM", r"\bfrom\b"),
    ("AS", r"\bas\b"),
    ("LIBRARY", r"@library"),
    ("RECORD", r"\brecord\b"),
    ("UNION", r"\bunion\b"),
    ("CLASS", r"\bclass\b"),
    ("NEW", r"\bnew\b"),
    ("THIS", r"\bthis\b"),
    ("ENUM", r"\benum\b"),
    ("TYPEDEF", r"\btypedef\b"),  # C-style alias: typedef int Count
    ("TYPE", r"\btype\b"),  # Type alias: type Percent = 0..100
    # Generics
    ("WHERE", r"\bwhere\b"),  # Generic constraint: where T: Comparable
    # Compile-time evaluation
    ("COMPTIME", r"\bcomptime\b"),  # Compile-time expression/block
    ("STATIC_ASSERT", r"\bstatic_assert\b"),  # Compile-time assertion
    ("STATIC", r"\bstatic\b"),  # Mutable module-level variable
    # Complete integer type ladder (8-8192 bit)
    # 8-bit
    ("TINY", r"\btiny\b"),  # i8 - signed 8-bit
    ("BYTE", r"\bbyte\b"),  # u8 - unsigned 8-bit (special case)
    ("SMALL", r"\bsmall\b"),  # i16 - signed 16-bit
    ("USMALL", r"\busmall\b"),  # u16 - unsigned 16-bit
    # 32-bit
    ("USHORT", r"\bushort\b"),
    ("SHORT", r"\bshort\b"),
    # 64-bit
    ("UINT", r"\buint\b"),
    ("INT", r"\bint\b"),
    # 128-bit
    ("ULONG", r"\bulong\b"),
    ("LONG", r"\blong\b"),
    ("UNSIGNED", r"\bunsigned\b"),
    # 256-bit
    ("UWIDE", r"\buwide\b"),
    ("WIDE", r"\bwide\b"),
    # 512-bit
    ("UVAST", r"\buvast\b"),
    ("VAST", r"\bvast\b"),
    # 1024-bit
    ("UGRAND", r"\bugrand\b"),
    ("GRAND", r"\bgrand\b"),
    # 2048-bit
    ("UGIANT", r"\bugiant\b"),
    ("GIANT", r"\bgiant\b"),
    # 4096-bit
    ("UTITAN", r"\butitan\b"),
    ("TITAN", r"\btitan\b"),
    # 8192-bit (1KB integers!)
    ("UCOLOS", r"\bucolos\b"),
    ("COLOS", r"\bcolos\b"),
    # Arbitrary precision (unbounded)
    ("UNBOUNDED", r"\bunbounded\b"),
    ("FLOAT_T", r"\bfloat\b"),
    ("DOUBLE", r"\bdouble\b"),
    ("QUAD", r"\bquad\b"),
    ("BOOL", r"\bbool\b"),
    ("STRING", r"\bstring\b"),
    ("VOID", r"\bvoid\b"),
    ("PTR", r"\bptr\b"),
    ("DICT", r"\bdict\b"),
    ("MAP", r"\bmap\b"),
    # SIMD vector types (SSE/AVX/AVX-512)
    ("VEC16B", r"\bvec16b\b"),  # 16 x i8 (SSE, 128-bit)
    ("VEC32B", r"\bvec32b\b"),  # 32 x i8 (AVX2, 256-bit)
    ("VEC64B", r"\bvec64b\b"),  # 64 x i8 (AVX-512, 512-bit)
    ("VEC4I", r"\bvec4i\b"),  # 4 x i32 (SSE, 128-bit)
    ("VEC8I", r"\bvec8i\b"),  # 8 x i32 (AVX2, 256-bit)
    ("VEC16I", r"\bvec16i\b"),  # 16 x i32 (AVX-512, 512-bit)
    ("VEC2L", r"\bvec2l\b"),  # 2 x i64 (SSE, 128-bit)
    ("VEC4L", r"\bvec4l\b"),  # 4 x i64 (AVX2, 256-bit)
    ("VEC8L", r"\bvec8l\b"),  # 8 x i64 (AVX-512, 512-bit)
    ("VEC4F", r"\bvec4f\b"),  # 4 x f32 (SSE, 128-bit)
    ("VEC8F", r"\bvec8f\b"),  # 8 x f32 (AVX2, 256-bit)
    ("VEC2D", r"\bvec2d\b"),  # 2 x f64 (SSE, 128-bit)
    ("VEC4D", r"\bvec4d\b"),  # 4 x f64 (AVX2, 256-bit)
    # Bitwise/Logic Gate operators (uppercase keywords for clarity)
    # These are the 16 boolean functions - proper gate names from digital logic
    ("GATE_AND", r"\bAND\b"),  # Bitwise AND gate
    ("GATE_OR", r"\bOR\b"),  # Bitwise OR gate
    ("GATE_XOR", r"\bXOR\b"),  # Bitwise XOR (exclusive or) gate
    ("GATE_NOT", r"\bNOT\b"),  # Bitwise NOT (inverter) gate
    ("GATE_NAND", r"\bNAND\b"),  # Bitwise NAND (universal gate)
    ("GATE_NOR", r"\bNOR\b"),  # Bitwise NOR (universal gate)
    ("GATE_XNOR", r"\bXNOR\b"),  # Bitwise XNOR (equality gate)
    # Legacy lowercase versions (deprecated but kept for compatibility)
    ("GATE_AND", r"\bband\b"),  # Bitwise AND (old)
    ("GATE_OR", r"\bbor\b"),  # Bitwise OR (old)
    ("GATE_XOR", r"\bbxor\b"),  # Bitwise XOR (old)
    ("GATE_NOT", r"\bbnot\b"),  # Bitwise NOT (old)
    ("GATE_NAND", r"\bnand\b"),  # Bitwise NAND (old)
    ("GATE_NOR", r"\bnor\b"),  # Bitwise NOR (old)
    ("GATE_XNOR", r"\bxnor\b"),  # Bitwise XNOR (old)
    # Shift operators
    ("SHL", r"\bSHL\b"),  # Shift left
    ("SHR", r"\bSHR\b"),  # Shift right (arithmetic)
    ("USHR", r"\bUSHR\b"),  # Unsigned shift right (logical)
    ("SHL", r"\bshl\b"),  # Shift left (lowercase)
    ("SHR", r"\bshr\b"),  # Shift right (lowercase)
    ("USHR", r"\bushr\b"),  # Unsigned shift right (lowercase)
    ("IDENT", r"[a-zA-Z_][a-zA-Z0-9_]*"),
    # Multi-line string (heredoc): """...""" (must come before other strings)
    ("HEREDOC", r'"""[\s\S]*?"""'),
    # Interpolated string: "...#{expr}..." with multiple interpolations supported
    # Pattern matches: text with one or more #{...} blocks
    ("INTERP_STRLIT", r'"(?:[^"#]|#(?!\{))*(?:#\{[^}]*\}(?:[^"#]|#(?!\{))*)+\"'),
    ("STRLIT", r'"([^"\\]|\\.)*"'),  # String with escape sequences
    ("CHARLIT", r"'([^'\\]|\\.)?'"),  # Character literal: 'a', '\n', '\x41'
    ("TILDE", r"~"),  # For destructors: ~ClassName
    # Symbol-based operators
    ("LSHIFT", r"<<"),  # Left shift
    ("RSHIFT", r">>"),  # Right shift
    ("POWER", r"\*\*"),  # Power operator (must be before STAR)
    ("AMPERSAND", r"&"),  # Bitwise AND (symbol)
    ("PIPE", r"\|"),  # Bitwise OR (symbol)
    ("CARET", r"\^"),  # Bitwise XOR (symbol)
    ("AT", r"@"),  # Decorator prefix
    # Compound-assign and increment/decrement operators. ORDER MATTERS:
    # the longest patterns must appear before their single-char prefixes
    # (e.g. PLUSPLUS before PLUS) so the lexer's first-match rule picks
    # them. The parser lowers each to existing Assign + BinaryOp nodes
    # (no new AST types). Added 30-04-2026 by author overrule of the
    # frozen-language rule because the verbose `position = position + 1`
    # in lexer/parser ports is too dense to read at scale.
    ("PLUSPLUS", r"\+\+"),
    ("MINUSMINUS", r"--"),
    ("PLUSEQ", r"\+="),
    ("MINUSEQ", r"-="),
    ("UI_ARROW", r"->"),
    ("STAREQ", r"\*="),
    ("SLASHEQ", r"/="),
    ("MODEQ", r"%="),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("MOD", r"%"),
    ("LTEQ", r"<="),
    ("GTEQ", r">="),
    ("EQ", r"=="),
    ("NEQ", r"!="),
    ("LT", r"<"),
    ("GT", r">"),
    ("ASSIGN", r"="),
    ("SLASH", r"/"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("COLON_ASSIGN", r":="),  # Ada-style range assign: x := 0..100
    ("COLON", r":"),
    ("SEMICOLON", r";"),
    ("COMMA", r","),
    ("RANGE_EXCL", r"\.\.\."),  # Exclusive range: 1...10 (Ruby-style)
    ("RANGE", r"\.\."),  # Inclusive range: 1..10 (Ruby-style)
    ("SAFE_DOT", r"\?\."),  # Safe navigation: user?.name
    ("DOT", r"\."),
    ("QUESTION", r"\?"),
    ("NEWLINE", r"\n"),
    ("SKIP", r"[ \t]+"),
]
# Pre-compile all regex patterns for MASSIVE performance boost
# This avoids recompiling 50+ patterns for every single token!
COMPILED_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (token_type, re.compile(pattern, re.DOTALL if token_type == "COMMENT_BLOCK" else 0))
    for token_type, pattern in TOKEN_PATTERNS
]

# First-character dispatch: map code[pos][0] -> the subset of compiled
# patterns that could possibly match. Slashes the per-token regex attempts
# from ~170 (try every pattern) to ~1-5 (try only patterns whose first
# char matches). Empirically ~25x speedup on real .ail files
# (perf_jit_driver/test_lexer_equiv.py has the byte-identical proof).

_LETTERS = set(string.ascii_lowercase) | set(string.ascii_uppercase) | {"_"}
_DIGITS = set(string.digits)


def _first_chars_for_pattern(pattern: str) -> set[str]:
    """Best-effort: which first chars could let `pattern` match?

    Recognized cases cover all current TOKEN_PATTERNS shapes. Anything not
    recognized returns the empty set, falling through to the always-tried
    fallback bucket -- safe but slow. The equivalence test verifies that
    no real input gets mis-routed.
    """
    p = pattern
    if p.startswith(r"\b"):  # word boundary -- look at what follows
        p = p[2:]
    if p and p[0] in _LETTERS:
        return {p[0]}
    if p.startswith(r"\d"):
        return _DIGITS
    if p == r"\n":
        return {"\n"}
    if p.startswith(r"[ \t]"):
        return {" ", "\t"}
    if p.startswith(r"[a-zA-Z_]"):
        return _LETTERS
    if p.startswith('"'):
        return {'"'}
    if p.startswith("'"):
        return {"'"}
    if p.startswith(r"/\*") or p.startswith(r"//") or p.startswith(r"/"):
        return {"/"}
    if p.startswith("#"):
        return {"#"}
    if p.startswith("0["):  # number alternation 0x.. | 0b.. | 0o.. | \d+
        return _DIGITS
    if p.startswith("\\") and len(p) > 1 and not p[1].isalpha():
        # Escaped literal: \(, \), \[, \], \{, \}, \+, \*, \., \|, \^, \?
        return {p[1]}
    if p and p[0] not in r"\\[(?":
        # Bare-literal operator: =, ==, ., :, ;, ~, @, etc.
        return {p[0]}
    return set()


def _build_dispatch() -> tuple[
    dict[str, list[tuple[str, Pattern[str]]]],
    list[tuple[str, Pattern[str]]],
]:
    """Group COMPILED_PATTERNS by candidate first-char.

    Returns (dispatch_dict, fallback_list). Patterns whose first-char set
    couldn't be determined go in the fallback and are appended to every
    bucket (so they're still tried). Within each bucket, original
    declaration order is preserved -- precedence rules intact.
    """
    dispatch: dict[str, list[tuple[str, Pattern[str]]]] = {}
    fallback: list[tuple[str, Pattern[str]]] = []
    for (token_type, pattern), (_, compiled) in zip(
        TOKEN_PATTERNS, COMPILED_PATTERNS, strict=False
    ):
        starts = _first_chars_for_pattern(pattern)
        if not starts:
            fallback.append((token_type, compiled))
            continue
        for ch in starts:
            dispatch.setdefault(ch, []).append((token_type, compiled))
    for ch in list(dispatch.keys()):
        dispatch[ch].extend(fallback)
    return dispatch, fallback


_DISPATCH, _FALLBACK = _build_dispatch()

UI_COLOR_PATTERN = re.compile(
    r"#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})(?![A-Za-z0-9_])"
)


def _match_ui_color_after_arrow(
    code: str, pos: int, tokens: list[tuple[str, str, int, int]]
) -> re.Match[str] | None:
    """Match canonical UI color literals only in `property -> #rrggbb` context."""
    if not tokens or token_type_at(tokens, -1) != "UI_ARROW":
        return None
    return UI_COLOR_PATTERN.match(code, pos)


def tokenize(code: str) -> list[tuple[str, str, int, int]]:
    """
    Convert AILang source code into tokens

    Args:
        code: Source code string

    Returns:
        List of (token_type, text, line_number, column) tuples
        Column is 1-based (first character of line is column 1)

    Raises:
        SyntaxError: If dangerous Unicode characters are detected (Trojan Source)
    """
    # Security check: detect Trojan Source attack vectors
    check_unicode_security(code)

    tokens: list[tuple[str, str, int, int]] = []
    pos = 0
    line = 1
    line_start = 0  # Position of the start of the current line
    code_len = len(code)  # Cache length for performance

    while pos < code_len:
        # Capture #cinclude directives as single-line tokens
        if code.startswith("#cinclude", pos):
            eol = code.find("\n", pos)
            if eol == -1:
                eol = code_len
            directive_text = code[pos:eol].strip()
            col = pos - line_start + 1
            tokens.append(("CINCLUDE_LINE", directive_text, line, col))
            pos = eol
            continue

        # Capture #cimport directives as single-line tokens
        if code.startswith("#cimport", pos):
            eol = code.find("\n", pos)
            if eol == -1:
                eol = code_len
            directive_text = code[pos:eol].strip()
            col = pos - line_start + 1
            tokens.append(("CIMPORT_LINE", directive_text, line, col))
            pos = eol
            continue

        # Capture #link directives as single-line tokens
        if code.startswith("#link", pos):
            eol = code.find("\n", pos)
            if eol == -1:
                eol = code_len
            directive_text = code[pos:eol].strip()
            col = pos - line_start + 1
            tokens.append(("LINK_LINE", directive_text, line, col))
            pos = eol
            continue

        # Capture template blocks as TEMPLATE_BLOCK tokens
        if code.startswith("#template", pos):
            end_idx = code.find("#end", pos)
            if end_idx == -1:
                raise SyntaxError(f"Line {line}: Unterminated template block")
            # Extract: #template lang\n...code...\n#end
            block_text = code[pos : end_idx + 4]
            col = pos - line_start + 1
            tokens.append(("TEMPLATE_BLOCK", block_text, line, col))
            line += block_text.count("\n")
            # Update line_start to after the last newline in the block
            last_nl = block_text.rfind("\n")
            if last_nl >= 0:
                line_start = pos + last_nl + 1
            pos = end_idx + 4
            continue

        ui_color = _match_ui_color_after_arrow(code, pos, tokens)
        if ui_color:
            col = pos - line_start + 1
            tokens.append(("UI_COLOR", ui_color.group(0), line, col))
            pos = ui_color.end()
            continue

        # First-character dispatch: only try patterns whose first char
        # matches code[pos]. Falls back to the always-tried bucket if the
        # current character has no specialised entry.
        bucket = _DISPATCH.get(code[pos], _FALLBACK)
        match = None
        for token_type, regex in bucket:
            match = regex.match(code, pos)
            if match:
                text = match.group(0)
                if token_type == "NEWLINE":
                    line += 1
                    line_start = match.end()
                elif token_type == "COMMENT_BLOCK":
                    # Count newlines in block comment
                    nl_count = text.count("\n")
                    line += nl_count
                    if nl_count > 0:
                        line_start = pos + text.rfind("\n") + 1
                elif token_type not in ("SKIP", "COMMENT", "HASH_COMMENT"):
                    col = pos - line_start + 1
                    tokens.append((token_type, text, line, col))
                pos = match.end()
                break
        if not match:
            col = pos - line_start + 1
            raise SyntaxError(
                f"Line {line}, Col {col}: Unexpected character: {code[pos]}"
            )

    # Post-process: Convert contextual keywords to IDENT if not followed by '('
    # This allows using 'len', 'print', etc. as variable names
    return _apply_contextual_keywords(tokens)


def _apply_contextual_keywords(
    tokens: list[tuple[str, str, int, int]],
) -> list[tuple[str, str, int, int]]:
    """
    Convert contextual keywords to IDENT when not followed by LPAREN.

    Special case: PRINT and PUTS can also be followed by expressions (no parens syntax).

    Examples:
        len(x)       -> LEN token (function call)
        int len = 5  -> IDENT token (variable name)
        print(x)     -> PRINT token (function call)
        print "hi"   -> PRINT token (statement, no parens)
        print foo()  -> PRINT token (statement, no parens)
        int print = 1 -> IDENT token (variable name)
    """
    if not tokens:
        return tokens

    result: list[tuple[str, str, int, int]] = []
    num_tokens = len(tokens)

    # Keywords that can be followed by expressions without parens
    STATEMENT_KEYWORDS = {"PRINT", "PUTS", "SPAWN"}
    # Tokens that can start an expression after PRINT/PUTS/SPAWN
    # Include contextual keywords that are valid function calls
    EXPR_START_TOKENS = {
        "STRLIT",
        "INTERP_STRLIT",
        "IDENT",
        "NUMBER",
        "FLOAT",
        "LPAREN",
        "TRUE",
        "FALSE",
        "LBRACKET",
        "MINUS",
        "NOT",
        # Contextual keywords that are function calls
        "LEN",
        "STRLEN",
        "ORD",
        "CHR",
        "CHAR_AT",
        "SUBSTR",
        "INPUT",
        "ALLOC",
        "DEALLOC",
        "PEEK64",
        "POKE64",
        "READ_FILE",
        "WRITE_FILE",
        "SQL_OPEN",
        "SQL_EXEC",
        "SQL_QUERY",
        "SQL_CLOSE",
        "CHANNEL",
        "CHAN_SEND",
        "CHAN_RECV",
        "CHAN_TRY_SEND",
        "CHAN_TRY_RECV",
        "CHAN_CLOSE",
        "SPAWN",
        "JOIN",
    }

    for i, (token_type, text, line_num, col_num) in enumerate(tokens):
        if token_type == "UI_INCLUDE":
            next_idx = i + 1
            while (
                next_idx < num_tokens and token_type_at(tokens, next_idx) == "NEWLINE"
            ):
                next_idx += 1

            if next_idx < num_tokens and token_type_at(tokens, next_idx) == "STRLIT":
                result.append((token_type, text, line_num, col_num))
            else:
                result.append(("IDENT", text, line_num, col_num))
            continue

        if token_type in CONTEXTUAL_KEYWORDS:
            # Check if next non-newline token is LPAREN or (for PRINT/PUTS) expression
            next_idx = i + 1
            while (
                next_idx < num_tokens and token_type_at(tokens, next_idx) == "NEWLINE"
            ):
                next_idx += 1

            if next_idx < num_tokens:
                next_token_type = token_type_at(tokens, next_idx)
                if next_token_type == "LPAREN":
                    # Keep as keyword (function call)
                    result.append((token_type, text, line_num, col_num))
                elif (
                    token_type in STATEMENT_KEYWORDS
                    and next_token_type in EXPR_START_TOKENS
                ):
                    # Keep PRINT/PUTS as keyword when followed by expression (no parens syntax)
                    result.append((token_type, text, line_num, col_num))
                else:
                    # Convert to IDENT (variable name)
                    result.append(("IDENT", text, line_num, col_num))
            else:
                # No next token, convert to IDENT
                result.append(("IDENT", text, line_num, col_num))
        else:
            result.append((token_type, text, line_num, col_num))

    return result


def unescape_string(s: str) -> str:
    """
    Process escape sequences in a string literal.
    Converts \\n to newline, \\t to tab, \\xNN to hex byte, \\uXXXX to unicode.
    """
    import re

    # Remove surrounding quotes
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]

    # L2 fix: Process hex escapes \xNN -> byte
    def hex_escape(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    s = re.sub(r"\\x([0-9A-Fa-f]{2})", hex_escape, s)

    # L2 fix: Process unicode escapes \uXXXX -> unicode char
    def unicode_escape(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    s = re.sub(r"\\u([0-9A-Fa-f]{4})", unicode_escape, s)

    # Process simple escape sequences
    escape_map = {
        "\\n": "\n",
        "\\t": "\t",
        "\\r": "\r",
        "\\\\": "\\",
        '\\"': '"',
        "\\0": "\0",
    }

    for escaped, unescaped in escape_map.items():
        s = s.replace(escaped, unescaped)

    return s


def char_literal_to_int(s: str) -> int:
    """
    Convert a character literal to its ASCII/Unicode integer value.
    Handles: 'a' -> 97, '\\n' -> 10, '\\x41' -> 65
    """
    # Remove surrounding single quotes
    if s.startswith("'") and s.endswith("'"):
        s = s[1:-1]

    if not s:
        return 0  # Empty char literal '' -> 0

    # Handle escape sequences
    if s.startswith("\\"):
        escape_map = {
            "\\n": 10,
            "\\t": 9,
            "\\r": 13,
            "\\\\": 92,
            "\\'": 39,
            "\\0": 0,
        }
        if s in escape_map:
            return escape_map[s]
        # Handle hex escape: \x41 -> 65
        if s.startswith("\\x") and len(s) >= 3:
            return int(s[2:], 16)
        # Handle octal escape: \101 -> 65
        if len(s) >= 2 and s[1].isdigit():
            return int(s[1:], 8)
        # Unknown escape, return the escaped character
        return ord(s[1]) if len(s) > 1 else 0

    # Regular character
    return ord(s[0])

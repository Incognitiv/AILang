#!/usr/bin/env python3
"""Audit executable coverage for AILang's syntax surface.

This is intentionally not a parser fuzzer. It answers a narrower question:
which lexer-level keywords/directives/operators have any executable evidence in
tests, examples, corpus programs, or validation-generated programs?
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "source"
TOOLS_ROOT = REPO_ROOT / "tools"
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(TOOLS_ROOT))

from lexer.scan import TOKEN_PATTERNS  # noqa: E402

try:
    from validation_programs import generated_cases  # noqa: E402
except Exception:  # pragma: no cover - audit should still work without generators
    generated_cases = None


TOKEN_IGNORE = {
    "COMMENT",
    "NEWLINE",
    "WHITESPACE",
    "NUMBER",
    "FLOATLIT",
    "STRLIT",
    "HEREDOC_STR",
    "INTERP_STR",
    "CHARLIT",
    "IDENT",
    "EOF",
}


TOKEN_ALIASES = {
    "LPAREN": "(",
    "RPAREN": ")",
    "LBRACE": "{",
    "RBRACE": "}",
    "LBRACKET": "[",
    "RBRACKET": "]",
    "ELLIPSIS": "...",
    "RANGE": "..",
    "SAFE_DOT": "?.",
    "DOT": ".",
    "QUESTION": "?",
    "COLON_ASSIGN": ":=",
    "COLON": ":",
    "SEMI": ";",
    "COMMA": ",",
    "INCREMENT": "++",
    "DECREMENT": "--",
    "PLUS_ASSIGN": "+=",
    "MINUS_ASSIGN": "-=",
    "ARROW": "->",
    "STAR_ASSIGN": "*=",
    "SLASH_ASSIGN": "/=",
    "PERCENT_ASSIGN": "%=",
    "EQ": "==",
    "NE": "!=",
    "LE": "<=",
    "GE": ">=",
    "LT": "<",
    "GT": ">",
    "ASSIGN": "=",
    "PLUS": "+",
    "MINUS": "-",
    "STAR": "*",
    "SLASH": "/",
    "PERCENT": "%",
}


FEATURE_NOTES = {
    "CINCLUDE": "C backend include directive; needs compile smoke and diagnostic coverage.",
    "CIMPORT": "Generated C binding directive; should be covered by cbind tests and generated bindings.",
    "LINK": "Backend linker directive; needs CLI/C backend/link-report coverage.",
    "ASM": "Unsafe/backend-specific; evidence should stay explicit and small.",
    "SPAWN": "Concurrency semantics require more than parse coverage.",
    "JOIN": "Concurrency semantics require more than parse coverage.",
    "ATOMIC": "Concurrency/memory-order semantics require targeted runtime tests.",
    "CHANNEL": "Channel lifecycle and leak checks need dedicated runtime tests.",
    "SQL_OPEN": "Host-resource feature; leak checks must close SQLite handles.",
    "READ_FILE": "Host-resource feature; evidence should use temporary files.",
    "WRITE_FILE": "Host-resource feature; evidence should use temporary files.",
    "INPUT": "Interactive feature; usually excluded from automated runtime suites.",
    "VOID": "Can appear as a return type or builtin type.",
    "PTR": "Pointer syntax should be validated through memory-safe smoke tests.",
    "OPAQUE": "ABI feature; by-value diagnostics matter.",
    "EXTERN": "ABI feature; requires C and LLVM parity where possible.",
    "STDCALL": "ABI feature; Windows callback smoke should cover this.",
    "FASTCALL": "ABI feature; Windows callback smoke should cover this.",
}


@dataclass(frozen=True)
class Evidence:
    kind: str
    name: str


@dataclass(frozen=True)
class SurfaceEntry:
    token: str
    spellings: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    note: str

    @property
    def covered(self) -> bool:
        return bool(self.evidence)


@dataclass(frozen=True)
class KeywordSmokeCase:
    token: str
    spelling: str
    source: str
    category: str


@dataclass(frozen=True)
class KeywordSmokeResult:
    token: str
    spelling: str
    status: str
    category: str
    error: str = ""


def _literal_from_word_pattern(pattern: str) -> str | None:
    match = re.fullmatch(r"\\b([A-Za-z_][A-Za-z0-9_]*)(?:\\\[ \\t\]\+([A-Za-z_][A-Za-z0-9_]*))?\\b", pattern)
    if match:
        first, second = match.groups()
        return first if second is None else f"{first} {second}"
    return None


def _literal_from_directive_pattern(pattern: str) -> str | None:
    match = re.fullmatch(r"([#@][A-Za-z_][A-Za-z0-9_]*)(?:\\b)?", pattern)
    if match:
        return match.group(1)
    return None


def _spellings_from_pattern(token: str, pattern: str) -> tuple[str, ...]:
    if token in TOKEN_ALIASES:
        return (TOKEN_ALIASES[token],)

    word = _literal_from_word_pattern(pattern)
    if word is not None:
        return (word,)

    directive = _literal_from_directive_pattern(pattern)
    if directive is not None:
        return (directive,)

    return ()


def _surface_tokens() -> dict[str, set[str]]:
    tokens: dict[str, set[str]] = {}
    for token, pattern in TOKEN_PATTERNS:
        if token in TOKEN_IGNORE:
            continue
        spellings = _spellings_from_pattern(token, pattern)
        if not spellings:
            continue
        tokens.setdefault(token, set()).update(spellings)
    return tokens


def _iter_source_files() -> Iterable[Path]:
    roots = [
        REPO_ROOT / "tests",
        REPO_ROOT / "examples",
        REPO_ROOT / "core",
        REPO_ROOT / "stdlib",
    ]
    suffixes = {".ail", ".py"}
    skipped_dirs = {"__pycache__", ".pytest_cache", "out", "build", "dist", ".git"}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if any(part in skipped_dirs for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() in suffixes:
                yield path
    for path in REPO_ROOT.glob("*.ail"):
        if path.is_file():
            yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _generated_sources() -> dict[str, str]:
    if generated_cases is None:
        return {}
    generated: dict[str, str] = {}
    try:
        for case in generated_cases(24, seed=166):
            generated[case.name] = case.source
    except Exception:
        return {}
    return generated


def _contains_spelling(text: str, spelling: str) -> bool:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?: [A-Za-z_][A-Za-z0-9_]*)?", spelling):
        parts = [re.escape(part) for part in spelling.split()]
        pattern = r"\b" + r"\s+".join(parts) + r"\b"
        return re.search(pattern, text) is not None
    return spelling in text


def _evidence_for_spellings(spellings: Iterable[str]) -> tuple[Evidence, ...]:
    evidence: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    spellings = tuple(spellings)

    generated = _generated_sources()
    for name, text in generated.items():
        if any(_contains_spelling(text, spelling) for spelling in spellings):
            key = ("generated", name)
            if key not in seen:
                evidence.append(Evidence("generated", name))
                seen.add(key)

    for path in _iter_source_files():
        text = _read_text(path)
        if any(_contains_spelling(text, spelling) for spelling in spellings):
            rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            kind = "test" if rel.startswith("tests/") else "source"
            key = (kind, rel)
            if key not in seen:
                evidence.append(Evidence(kind, rel))
                seen.add(key)

    for token, token_spellings in sorted(_surface_tokens().items()):
        for spelling in token_spellings:
            if spelling not in spellings:
                continue
            source, category = _keyword_case_source(token, spelling)
            if not source:
                continue
            key = ("keyword-smoke", f"{token}:{spelling}")
            if key not in seen:
                evidence.append(Evidence("keyword-smoke", f"{token}:{category}"))
                seen.add(key)

    return tuple(evidence[:8])


def build_surface_entries() -> list[SurfaceEntry]:
    entries: list[SurfaceEntry] = []
    for token, spellings in sorted(_surface_tokens().items()):
        ordered_spellings = tuple(sorted(spellings, key=lambda item: (len(item), item)))
        entries.append(
            SurfaceEntry(
                token=token,
                spellings=ordered_spellings,
                evidence=_evidence_for_spellings(ordered_spellings),
                note=FEATURE_NOTES.get(token, ""),
            )
        )
    return entries


NUMERIC_TYPE_TOKENS = {
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
}

FLOAT_TYPE_TOKENS = {"FLOAT_T", "DOUBLE", "QUAD"}

VECTOR_TYPE_TOKENS = {
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
}


def _is_keyword_spelling(spelling: str) -> bool:
    if spelling.startswith(("#", "@")):
        return True
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?: [A-Za-z_][A-Za-z0-9_]*)?", spelling) is not None


def _main(body: str) -> str:
    lines = ["def main(): int"]
    for line in body.rstrip().splitlines():
        lines.append(f"    {line}" if line else "")
    lines.append("end")
    return "\n".join(lines) + "\n"


def _with_main(prefix: str, body: str = "return 0") -> str:
    return prefix.rstrip() + "\n\n" + _main(body)


def _simple_expr(spelling: str) -> str:
    return _main(f"value = {spelling}\nreturn 0")


def _type_decl(spelling: str, value: str = "1") -> str:
    return _main(f"{spelling} value = {value}\nreturn 0")


def _decorated_function(spelling: str) -> str:
    return f"""\
{spelling}
def decorated(value: int): int
    return value
end

def main(): int
    return decorated(1)
end
"""


def _keyword_case_source(token: str, spelling: str) -> tuple[str, str]:
    if token in NUMERIC_TYPE_TOKENS:
        return _type_decl(spelling), "type-parse"
    if token in FLOAT_TYPE_TOKENS:
        return _type_decl(spelling, "1.0"), "type-parse"
    if token in VECTOR_TYPE_TOKENS:
        return _type_decl(spelling, f'vec_broadcast(1, "{spelling}")'), "type-parse"

    special: dict[str, tuple[str, str]] = {
        "ALLOC": (_main("p = alloc(8)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "AND": (_main("value = true and false\nreturn 0"), "expr-parse"),
        "ARRAY": (_type_decl("array", "[1, 2, 3]"), "type-parse"),
        "AS": ("import smoke.module as alias\n" + _main("return 0"), "module-parse"),
        "ASM": (_main('asm("nop")\nreturn 0'), "unsafe-parse"),
        "ASSERT": (_main("assert true, \"ok\"\nreturn 0"), "stmt-parse"),
        "ASYNC": ("async def fetch(): int\n    return 1\nend\n", "decl-parse"),
        "ATOMIC": (_main("p = alloc(8)\nvalue = atomic add(p, 1)\ndealloc(p)\nreturn 0"), "concurrency-parse"),
        "AWAIT": (
            "async def fetch(): int\n    return 1\nend\n\n"
            + _main("value = await fetch()\nreturn 0"),
            "async-parse",
        ),
        "BITCAST": (_main("p = bitcast(ptr, 0)\nreturn 0"), "cast-parse"),
        "BOOL": (_type_decl("bool", "true"), "type-parse"),
        "BOUND": (_main("i = 0\n@bound\nwhile i < 1 then\n    i = i + 1\nend\nreturn 0"), "loop-parse"),
        "BOUNDED": (_main("i = 0\n@bounded(4)\nwhile i < 1 then\n    i = i + 1\nend\nreturn 0"), "loop-parse"),
        "BREAK": (_main("while true then\n    break\nend\nreturn 0"), "loop-parse"),
        "CASE": (_main("match 1 then\ncase 1:\n    print(1)\nelse:\n    print(0)\nend\nreturn 0"), "match-parse"),
        "CATCH": (_main("try then\n    throw \"x\"\ncatch Error then\n    print(1)\nend\nreturn 0"), "exception-parse"),
        "CHANNEL": (_main("ch = channel(int, 1)\nreturn 0"), "channel-parse"),
        "CHAN_CLOSE": (_main("ch = channel(int, 1)\nchan_close(ch)\nreturn 0"), "channel-parse"),
        "CHAN_RECV": (_main("ch = channel(int, 1)\nvalue = chan_recv(ch)\nreturn 0"), "channel-parse"),
        "CHAN_SEND": (_main("ch = channel(int, 1)\nchan_send(ch, 1)\nreturn 0"), "channel-parse"),
        "CHAN_TRY_RECV": (_main("ch = channel(int, 1)\nvalue = chan_try_recv(ch)\nreturn 0"), "channel-parse"),
        "CHAN_TRY_SEND": (_main("ch = channel(int, 1)\nvalue = chan_try_send(ch, 1)\nreturn 0"), "channel-parse"),
        "CHAR_AT": (_main('value = char_at("abc", 0)\nreturn 0'), "builtin-parse"),
        "CHR": (_main("value = chr(65)\nreturn 0"), "builtin-parse"),
        "CIMPORT": ('#cimport "stdint.h"\n' + _main("return 0"), "directive-parse"),
        "CINCLUDE": ("#cinclude <stdint.h>\n" + _main("return 0"), "directive-parse"),
        "CLASS": (
            "class Box then\n"
            "    public int value = 1\n"
            "    def get(): int\n"
            "        return this.value\n"
            "    end\n"
            "end\n",
            "decl-parse",
        ),
        "COMPTIME": (_main("value = comptime 1\nreturn 0"), "compile-time-parse"),
        "CONST": ("const int VALUE = 1\n" + _main("return VALUE"), "decl-parse"),
        "CONTINUE": (_main("i = 0\nwhile i < 1 then\n    i = i + 1\n    continue\nend\nreturn 0"), "loop-parse"),
        "DEALLOC": (_main("p = alloc(8)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "DEF": (_main("return 0"), "decl-parse"),
        "DICT": (_type_decl("dict", '{"a": 1}'), "type-parse"),
        "DO": (_main("i = 0\ndo then\n    i = i + 1\nend while i < 1\nreturn 0"), "loop-parse"),
        "ELSE": (_main("if false then\n    print(0)\nelse\n    print(1)\nend\nreturn 0"), "branch-parse"),
        "ELSIF": (_main("if false then\n    print(0)\nelsif true then\n    print(1)\nend\nreturn 0"), "branch-parse"),
        "END": (_main("return 0"), "decl-parse"),
        "ENUM": ("enum Mode then\n    Off, On = 7\nend\n" + _main("return 0"), "decl-parse"),
        "EXCEPT": (_main("try then\n    throw \"x\"\nexcept err then\n    print(1)\nend\nreturn 0"), "exception-parse"),
        "EXTERN": ("extern fn native_answer(): int\n" + _main("return 0"), "abi-parse"),
        "FALSE": (_simple_expr("false"), "literal-parse"),
        "FASTMATH": (_decorated_function("@fastmath"), "decorator-parse"),
        "FINALLY": (_main("try then\n    print(1)\nfinally then\n    print(2)\nend\nreturn 0"), "exception-parse"),
        "FOR": (_main("i = 0\nfor (i = 0; i < 1; i = i + 1) then\n    print(i)\nend\nreturn 0"), "loop-parse"),
        "FOREACH": (_main("foreach item in [1, 2] then\n    print(item)\nend\nreturn 0"), "loop-parse"),
        "FROM": ("from smoke.module import thing\n" + _main("return 0"), "module-parse"),
        "IF": (_main("if true then\n    print(1)\nend\nreturn 0"), "branch-parse"),
        "IMPORT": ("import smoke.module\n" + _main("return 0"), "module-parse"),
        "IN": (_main("for item in [1, 2] then\n    print(item)\nend\nreturn 0"), "loop-parse"),
        "INFINITY": (_main("loop max: infinity then\n    break\nend\nreturn 0"), "loop-parse"),
        "INLINE": (_decorated_function("@inline"), "decorator-parse"),
        "INPUT": (_main("value = input()\nreturn 0"), "builtin-parse"),
        "IS": (_main("value = 1 is 1\nreturn 0"), "expr-parse"),
        "JOIN": (
            "def worker(value: int): int\n    return value\nend\n\n"
            + _main("h = spawn worker(1)\nvalue = join(h)\nreturn 0"),
            "concurrency-parse",
        ),
        "LEN": (_main("value = len([1, 2])\nreturn 0"), "builtin-parse"),
        "LIBRARY": ('@library("smoke")\n' + _main("return 0"), "module-parse"),
        "LINK_DIR": ('#link "-lm"\n' + _main("return 0"), "directive-parse"),
        "LOOP": (_main("loop max: 1 then\n    break\nend\nreturn 0"), "loop-parse"),
        "MAP": (_type_decl("map", '{"a": 1}'), "type-parse"),
        "MATCH": (_main("match 1 then\ncase 1:\n    print(1)\nend\nreturn 0"), "match-parse"),
        "NEW": (
            "record Pair then\n    int value\nend\n\n" + _main("Pair p = new Pair(1)\nreturn 0"),
            "construct-parse",
        ),
        "NOALIAS": (_decorated_function("@noalias"), "decorator-parse"),
        "NOT": (_main("value = not false\nreturn 0"), "expr-parse"),
        "NULLPTR": (_main("value = nullptr\nreturn 0"), "literal-parse"),
        "OPAQUE": ("opaque record NativeHandle\n" + _main("return 0"), "abi-parse"),
        "OR": (_main("value = true or false\nreturn 0"), "expr-parse"),
        "ORD": (_main('value = ord("A")\nreturn 0'), "builtin-parse"),
        "OTHERWISE": (_main("if false then\n    print(0)\notherwise true then\n    print(1)\nend\nreturn 0"), "branch-parse"),
        "PEEK32": (_main("p = alloc(8)\nvalue = peek32(p, 0)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "PEEK64": (_main("p = alloc(8)\nvalue = peek64(p, 0)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "PEEK8": (_main("p = alloc(8)\nvalue = peek8(p, 0)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "POKE32": (_main("p = alloc(8)\npoke32(p, 0, 1)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "POKE64": (_main("p = alloc(8)\npoke64(p, 0, 1)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "POKE8": (_main("p = alloc(8)\npoke8(p, 0, 1)\ndealloc(p)\nreturn 0"), "memory-parse"),
        "PRINT": (_main("print(1)\nreturn 0"), "builtin-parse"),
        "PRIVATE": ("private def hidden(): int\n    return 1\nend\n", "decl-parse"),
        "PTR": (_type_decl("ptr", "nullptr"), "type-parse"),
        "PUBLIC": ("public def exported(): int\n    return 1\nend\n", "decl-parse"),
        "PURE": (_decorated_function("@pure"), "decorator-parse"),
        "PUTC": (_main('putc("x")\nreturn 0'), "builtin-parse"),
        "PUTS": (_main('puts("x")\nreturn 0'), "builtin-parse"),
        "READ_FILE": (_main('value = read_file("missing.txt")\nreturn 0'), "host-parse"),
        "RECORD": ("record Pair then\n    int value\nend\n" + _main("return 0"), "decl-parse"),
        "REINTERPRET": (_main("p = reinterpret(ptr, 0)\nreturn 0"), "cast-parse"),
        "REPEAT": (_main("repeat 1 times then\n    print(1)\nend\nreturn 0"), "loop-parse"),
        "RETURN": (_main("return 0"), "stmt-parse"),
        "SECTION": ('section ".hot"\n' + _main("return 0"), "reserved-parse"),
        "SPAWN": (
            "def worker(value: int): int\n    return value\nend\n\n"
            + _main("h = spawn worker(1)\nreturn 0"),
            "concurrency-parse",
        ),
        "SQL_CLOSE": (_main('db = sql_open(":memory:")\nsql_close(db)\nreturn 0'), "host-parse"),
        "SQL_EXEC": (_main('db = sql_open(":memory:")\nsql_exec(db, "select 1")\nsql_close(db)\nreturn 0'), "host-parse"),
        "SQL_OPEN": (_main('db = sql_open(":memory:")\nsql_close(db)\nreturn 0'), "host-parse"),
        "SQL_QUERY": (_main('db = sql_open(":memory:")\nrows = sql_query(db, "select 1")\nsql_close(db)\nreturn 0'), "host-parse"),
        "STATIC": ("static int counter = 0\n" + _main("return 0"), "decl-parse"),
        "STATIC_ASSERT": (_main('static_assert true, "ok"\nreturn 0'), "compile-time-parse"),
        "STRING": (_type_decl("string", '"x"'), "type-parse"),
        "STRLEN": (_main('value = strlen("abc")\nreturn 0'), "builtin-parse"),
        "SUBSTR": (_main('value = substr("abc", 0, 1)\nreturn 0'), "builtin-parse"),
        "SYNCHRONIZED": (_decorated_function("@synchronized"), "decorator-parse"),
        "TEMPLATE_END": ("#template ansi_c\nstatic int smoke_template;\n#end\n" + _main("return 0"), "template-parse"),
        "TEMPLATE_START": ("#template ansi_c\nstatic int smoke_template;\n#end\n" + _main("return 0"), "template-parse"),
        "TEST": ('test "keyword smoke"\n    assert true\nend\n', "decl-parse"),
        "THEN": (_main("if true then\n    print(1)\nend\nreturn 0"), "branch-parse"),
        "THIS": (
            "class Box then\n"
            "    int value = 1\n"
            "    def get(): int\n"
            "        return this.value\n"
            "    end\n"
            "end\n",
            "class-parse",
        ),
        "THROW": (_main('throw "x"\nreturn 0'), "exception-parse"),
        "TIMES": (_main("repeat 1 times then\n    print(1)\nend\nreturn 0"), "loop-parse"),
        "TRUE": (_simple_expr("true"), "literal-parse"),
        "TRY": (_main("try then\n    print(1)\nend\nreturn 0"), "exception-parse"),
        "TYPE": ("type Percent = 0..100\n" + _main("return 0"), "decl-parse"),
        "TYPEDEF": ("typedef int Count\n" + _main("return 0"), "decl-parse"),
        "UI_INCLUDE": ('include "layout.ail"\n' + _main("return 0"), "module-parse"),
        "UNCHECKED": (_decorated_function("@unchecked"), "decorator-parse"),
        "UNION": ("union Word then\n    int value\nend\n" + _main("return 0"), "decl-parse"),
        "UNLESS": (_main("unless false then\n    print(1)\nend\nreturn 0"), "branch-parse"),
        "UNSAFE": (_main("arr = [1]\nvalue = arr[0, unsafe]\nreturn 0"), "unsafe-parse"),
        "UNTIL": (_main("i = 0\nuntil i == 1 then\n    i = i + 1\nend\nreturn 0"), "loop-parse"),
        "USE": ("use smoke.module\n" + _main("return 0"), "module-parse"),
        "VOID": ("void noop():\n    return\nend\n", "decl-parse"),
        "WHERE": ("def id[T](value: T): T where T: Number\n    return value\nend\n", "generic-parse"),
        "WHILE": (_main("i = 0\nwhile i < 1 then\n    i = i + 1\nend\nreturn 0"), "loop-parse"),
        "WRITE_FILE": (_main('write_file("tmp.txt", "x")\nreturn 0'), "host-parse"),
    }
    if token == "NULL":
        return _simple_expr(spelling), "literal-parse"
    if token == "GATE_NOT":
        return _main(f"value = {spelling} 7\nreturn 0"), "expr-parse"
    if token.startswith("GATE_"):
        return _main(f"value = 7 {spelling} 3\nreturn 0"), "expr-parse"
    if token in {"SHL", "SHR", "USHR"}:
        return _main(f"value = 8 {spelling} 1\nreturn 0"), "expr-parse"
    if token == "IS_NOT":
        return _main("value = 1 is not 2\nreturn 0"), "expr-parse"
    if token in special:
        return special[token]
    return "", "missing-case"


def build_keyword_smoke_cases(entries: list[SurfaceEntry] | None = None) -> list[KeywordSmokeCase]:
    cases: list[KeywordSmokeCase] = []
    source_entries = entries if entries is not None else build_surface_entries()
    for entry in source_entries:
        for spelling in entry.spellings:
            if not _is_keyword_spelling(spelling):
                continue
            source, category = _keyword_case_source(entry.token, spelling)
            cases.append(KeywordSmokeCase(entry.token, spelling, source, category))
    return cases


def run_keyword_smoke(cases: list[KeywordSmokeCase] | None = None) -> list[KeywordSmokeResult]:
    from lexer.scan import tokenize
    from parser.parser import Parser

    smoke_cases = cases if cases is not None else build_keyword_smoke_cases()
    results: list[KeywordSmokeResult] = []
    for case in smoke_cases:
        if not case.source:
            results.append(
                KeywordSmokeResult(
                    case.token,
                    case.spelling,
                    "missing-case",
                    case.category,
                    "no parser smoke source is defined",
                )
            )
            continue
        try:
            Parser(tokenize(case.source)).parse_program()
        except Exception as exc:  # noqa: BLE001 - this is a diagnostic tool
            results.append(
                KeywordSmokeResult(
                    case.token,
                    case.spelling,
                    "parse-fail",
                    case.category,
                    str(exc).splitlines()[0],
                )
            )
            continue
        results.append(
            KeywordSmokeResult(case.token, case.spelling, "parse-pass", case.category)
        )
    return results


def _format_evidence(evidence: tuple[Evidence, ...]) -> str:
    if not evidence:
        return ""
    return ", ".join(f"{item.kind}:{item.name}" for item in evidence)


def print_text_report(entries: list[SurfaceEntry]) -> None:
    covered = [entry for entry in entries if entry.covered]
    missing = [entry for entry in entries if not entry.covered]
    print(f"Syntax surface entries: {len(entries)}")
    print(f"Covered by source/parser-smoke evidence: {len(covered)}")
    print(f"No evidence found: {len(missing)}")
    if missing:
        print("")
        print("Missing evidence:")
        for entry in missing:
            spellings = ", ".join(entry.spellings)
            note = f" -- {entry.note}" if entry.note else ""
            print(f"- {entry.token}: {spellings}{note}")


def print_keyword_smoke_report(results: list[KeywordSmokeResult]) -> None:
    passed = [result for result in results if result.status == "parse-pass"]
    failed = [result for result in results if result.status == "parse-fail"]
    missing = [result for result in results if result.status == "missing-case"]
    print("")
    print(f"Keyword smoke cases: {len(results)}")
    print(f"Parse passed: {len(passed)}")
    print(f"Parse failed: {len(failed)}")
    print(f"Missing smoke source: {len(missing)}")
    if failed:
        print("")
        print("Parse failures:")
        for result in failed:
            print(
                f"- {result.token} `{result.spelling}` "
                f"({result.category}): {result.error}"
            )
    if missing:
        print("")
        print("Missing smoke sources:")
        for result in missing:
            print(f"- {result.token} `{result.spelling}`")


def write_markdown_report(entries: list[SurfaceEntry], path: Path) -> None:
    covered = sum(1 for entry in entries if entry.covered)
    lines = [
        "# AILang Syntax Surface Audit",
        "",
        "This file is generated by `python tools/syntax_surface_audit.py --markdown SYNTAX_SURFACE.md`.",
        "Coverage here means the spelling appears in executable/source evidence or a parser-smoke source; it is not a proof of semantic correctness.",
        "",
        f"- Entries: {len(entries)}",
        f"- Covered by evidence: {covered}",
        f"- No evidence found: {len(entries) - covered}",
        "",
        "| Status | Token | Spelling(s) | Evidence | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        status = "[x]" if entry.covered else "[ ]"
        spellings = ", ".join(f"`{spelling}`" for spelling in entry.spellings)
        evidence = _format_evidence(entry.evidence).replace("|", "\\|")
        note = entry.note.replace("|", "\\|")
        lines.append(f"| {status} | `{entry.token}` | {spellings} | {evidence} | {note} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_keyword_markdown_report(results: list[KeywordSmokeResult], path: Path) -> None:
    passed = sum(1 for result in results if result.status == "parse-pass")
    lines = [
        "# AILang Keyword Smoke Check",
        "",
        "This file is generated by `python tools/syntax_surface_audit.py --check-keywords --keywords-markdown KEYWORD_SURFACE.md`.",
        "Each row uses one lexer keyword/directive/decorator spelling in a minimal AILang parser smoke program.",
        "",
        f"- Cases: {len(results)}",
        f"- Parse passed: {passed}",
        f"- Parse failed: {len(results) - passed}",
        "",
        "| Status | Token | Spelling | Category | Error |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in results:
        status = "[x]" if result.status == "parse-pass" else "[ ]"
        error = result.error.replace("|", "\\|")
        lines.append(
            f"| {status} | `{result.token}` | `{result.spelling}` | "
            f"{result.category} | {error} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="Write a Markdown checklist report")
    parser.add_argument("--fail-on-missing", action="store_true", help="Return non-zero if any syntax entry has no evidence")
    parser.add_argument("--check-keywords", action="store_true", help="Parse-check one minimal smoke source per lexer keyword spelling")
    parser.add_argument("--keywords-markdown", type=Path, help="Write a Markdown report for --check-keywords")
    parser.add_argument("--fail-on-keyword-fail", action="store_true", help="Return non-zero if any keyword smoke parse check fails")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    entries = build_surface_entries()
    print_text_report(entries)
    if args.markdown:
        write_markdown_report(entries, args.markdown)
    keyword_results: list[KeywordSmokeResult] = []
    if args.check_keywords or args.keywords_markdown:
        keyword_results = run_keyword_smoke(build_keyword_smoke_cases(entries))
        print_keyword_smoke_report(keyword_results)
        if args.keywords_markdown:
            write_keyword_markdown_report(keyword_results, args.keywords_markdown)
    if args.fail_on_missing and any(not entry.covered for entry in entries):
        return 1
    if args.fail_on_keyword_fail and any(result.status != "parse-pass" for result in keyword_results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

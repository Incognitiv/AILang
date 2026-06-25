"""ABI header block parsing helpers for Parser."""

from __future__ import annotations

import re

from .ast import (
    ASTNode,
    CAbiConditional,
    CAbiDefine,
    CAbiField,
    CAbiHeader,
    CAbiInclude,
    CAbiInlineFunction,
    CAbiMacro,
    CAbiPrototype,
    CAbiStruct,
    CAbiTypedef,
)


def parse_cabi_header(self) -> CAbiHeader:
    """Parse an AILang-owned ABI header block.

    Example:
      abi header "sys/event.h":
          guard AILANG_SYS_EVENT_H
          define EV_ADD = 0x0001
          struct kevent:
              ident: uintptr_t
          end
      end
    """
    _consume_abi_header_start(self)
    _consume_cabi_keyword(self, "header")
    path_token = self.consume("STRLIT")
    path = path_token[1:-1]
    self.consume("COLON")
    self.skip_newlines()

    guard: str | None = None
    entries, guard = _parse_cabi_entries(self, allow_guard=True, guard=guard)

    self.consume("END")
    return CAbiHeader(path=path, guard=guard, entries=entries)


def _parse_cabi_entries(
    self,
    *,
    allow_guard: bool = False,
    guard: str | None = None,
    stop_on_else: bool = False,
) -> tuple[list[ASTNode], str | None]:
    entries: list[ASTNode] = []
    while self.peek() and self.peek_type() != "END":
        if stop_on_else and self.peek_text() == "else":
            break
        word = _peek_cabi_keyword(self)
        if word == "guard":
            if not allow_guard:
                self.error("abi guard is only valid at abi header top level")
            self.consume()
            guard = self.consume("IDENT")
        elif word == "include":
            entries.append(_parse_cabi_include(self, include_next=False))
        elif word == "include_next":
            entries.append(_parse_cabi_include(self, include_next=True))
        elif word == "define":
            entries.append(_parse_cabi_define(self))
        elif word == "typedef":
            entries.append(_parse_cabi_typedef(self))
        elif word == "struct":
            entries.append(_parse_cabi_struct(self))
        elif word == "prototype":
            entries.append(_parse_cabi_prototype(self))
        elif word in {"static", "inline"}:
            entries.append(_parse_cabi_inline(self))
        elif word in {"if", "ifdef", "ifndef"}:
            entries.append(_parse_cabi_conditional(self))
        elif word == "macro":
            entries.append(_parse_cabi_macro(self))
        else:
            self.error(
                "Expected abi header entry: guard, include, include_next, "
                "define, typedef, struct, prototype, static inline, if, "
                "ifdef, ifndef, or macro"
            )
        self.skip_newlines()
    return entries, guard


def _peek_cabi_keyword(self) -> str:
    if self.peek_type() not in {"IDENT", "UI_INCLUDE", "TYPEDEF", "IF", "STATIC"}:
        self.error("Expected abi header keyword")
    return self.peek_text()


def _consume_abi_header_start(self) -> None:
    if self.peek_type() != "IDENT" or self.peek_text() not in {"abi", "cabi"}:
        self.error("Expected 'abi'")
    self.consume("IDENT")


def _consume_cabi_keyword(self, expected: str) -> None:
    if (
        self.peek_type() not in {"IDENT", "UI_INCLUDE", "TYPEDEF", "IF", "STATIC"}
        or self.peek_text() != expected
    ):
        self.error(f"Expected '{expected}'")
    self.consume()


def _consume_cabi_identifier(self, context: str) -> str:
    text = self.peek_text()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", text):
        return self.consume()
    self.error(f"Expected ABI header {context} identifier")
    raise AssertionError("unreachable")


def _token_text_until_line_end(self) -> str:
    pieces: list[str] = []
    line = self.peek_line() if self.peek() else 0
    while self.peek() and self.peek_type() != "NEWLINE" and self.peek_line() == line:
        pieces.append(self.consume())
    return " ".join(pieces).strip()


def _parse_cabi_define(self) -> CAbiDefine:
    _consume_cabi_keyword(self, "define")
    name = _consume_cabi_identifier(self, "define")
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
    value = _token_text_until_line_end(self)
    if not value:
        self.error("abi define requires a value")
    return CAbiDefine(name, value)


def _parse_cabi_include(self, *, include_next: bool = False) -> CAbiInclude:
    _consume_cabi_keyword(self, "include_next" if include_next else "include")
    if self.peek_type() == "STRLIT":
        return CAbiInclude(
            self.consume("STRLIT")[1:-1], is_system=False, include_next=include_next
        )
    if self.peek_type() == "LT":
        self.consume("LT")
        pieces: list[str] = []
        while self.peek_type() != "GT":
            pieces.append(self.consume())
        self.consume("GT")
        return CAbiInclude("".join(pieces), is_system=True, include_next=include_next)
    self.error('Expected abi include path, e.g. include <errno.h> or include "x.h"')
    raise AssertionError("unreachable")


def _parse_cabi_typedef(self) -> CAbiTypedef:
    _consume_cabi_keyword(self, "typedef")
    name = _consume_cabi_identifier(self, "typedef")
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
    c_type = _consume_cabi_type_until(self, {"NEWLINE", "END"})
    if not c_type:
        self.error("abi typedef requires a target type")
    return CAbiTypedef(name, c_type)


def _parse_cabi_struct(self) -> CAbiStruct:
    _consume_cabi_keyword(self, "struct")
    name = _consume_cabi_identifier(self, "struct")
    self.consume("COLON")
    self.skip_newlines()

    fields: list[CAbiField] = []
    while self._not_block_end("END"):
        field_name = _consume_cabi_identifier(self, "field")
        self.consume("COLON")
        c_type = _consume_cabi_type_until(self, {"NEWLINE", "END"})
        if not c_type:
            self.error("abi struct field requires a type")
        fields.append(CAbiField(field_name, c_type))
        self.skip_newlines()

    self.consume("END")
    return CAbiStruct(name, fields)


def _parse_cabi_prototype(self) -> CAbiPrototype:
    _consume_cabi_keyword(self, "prototype")
    return_type = _consume_cabi_type_atom(self)
    name = _consume_cabi_identifier(self, "prototype")
    params, variadic = _parse_cabi_param_list(self)
    return CAbiPrototype(name, return_type, params, variadic=variadic)


def _parse_cabi_param_list(self) -> tuple[list[tuple[str, str]], bool]:
    self.consume("LPAREN")
    self.skip_newlines()
    params: list[tuple[str, str]] = []
    variadic = False
    while self.peek_type() != "RPAREN":
        if params:
            self.consume("COMMA")
            self.skip_newlines()
        if self.peek_type() == "RANGE_EXCL":
            self.consume("RANGE_EXCL")
            variadic = True
            self.skip_newlines()
            break
        param_name = _consume_cabi_identifier(self, "parameter")
        self.consume("COLON")
        param_type = _consume_cabi_type_until(self, {"COMMA", "RPAREN"})
        params.append((param_name, param_type))
        self.skip_newlines()

    self.consume("RPAREN")
    return params, variadic


def _parse_cabi_inline(self) -> CAbiInlineFunction:
    if self.peek_text() == "static":
        self.consume()
        _consume_cabi_keyword(self, "inline")
    else:
        _consume_cabi_keyword(self, "inline")
    return_type = _consume_cabi_type_atom(self)
    name = _consume_cabi_identifier(self, "inline function")
    params, variadic = _parse_cabi_param_list(self)
    self.consume("COLON")
    self.skip_newlines()

    _consume_cabi_keyword(self, "c_emit")
    if self.peek_type() != "HEREDOC":
        self.error("abi static inline requires c_emit heredoc body")
    raw = self.consume("HEREDOC")
    body = raw[3:-3]
    self.skip_newlines()
    self.consume("END")
    return CAbiInlineFunction(name, return_type, params, body, variadic=variadic)


def _parse_cabi_conditional(self) -> CAbiConditional:
    directive = self.peek_text()
    if directive == "if":
        self.consume()
        if self.peek_type() == "STRLIT":
            expression = self.consume("STRLIT")[1:-1]
        else:
            expression = _token_text_until_line_end(self)
            if not expression:
                self.error("abi if requires an expression")
    elif directive in {"ifdef", "ifndef"}:
        self.consume()
        expression = _consume_cabi_identifier(self, directive)
    else:
        self.error("Expected abi conditional")
        raise AssertionError("unreachable")
    self.consume("COLON")
    self.skip_newlines()

    entries, _unused_guard = _parse_cabi_entries(self, stop_on_else=True)
    else_entries: list[ASTNode] = []
    if self.peek() and self.peek_text() == "else":
        self.consume()
        self.consume("COLON")
        self.skip_newlines()
        else_entries, _unused_guard = _parse_cabi_entries(self)
    self.consume("END")
    return CAbiConditional(directive, expression, entries, else_entries)


def _parse_cabi_macro(self) -> CAbiMacro:
    _consume_cabi_keyword(self, "macro")
    name = _consume_cabi_identifier(self, "macro")
    self.consume("LPAREN")
    params: list[str] = []
    if self.peek_type() != "RPAREN":
        if self.peek_type() == "RANGE_EXCL":
            self.consume("RANGE_EXCL")
            params.append("...")
        else:
            params.append(_consume_cabi_identifier(self, "macro parameter"))
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            if self.peek_type() == "RANGE_EXCL":
                self.consume("RANGE_EXCL")
                params.append("...")
                break
            params.append(_consume_cabi_identifier(self, "macro parameter"))
    self.consume("RPAREN")
    self.consume("COLON")
    self.skip_newlines()

    _consume_cabi_keyword(self, "c_emit")
    if self.peek_type() != "HEREDOC":
        self.error("abi macro requires c_emit heredoc body")
    raw = self.consume("HEREDOC")
    body = raw[3:-3]
    self.skip_newlines()
    self.consume("END")
    return CAbiMacro(name, params, body)


def _consume_cabi_type_atom(self) -> str:
    if self.peek_type() == "STRLIT":
        return self.consume("STRLIT")[1:-1]
    if self.peek_type() is None:
        self.error("Expected C ABI type")
    return self.consume()


def _consume_cabi_type_until(self, terminators: set[str]) -> str:
    if self.peek_type() == "STRLIT":
        return self.consume("STRLIT")[1:-1]
    pieces: list[str] = []
    depth = 0
    start_line = self.peek_line() if self.peek() else 0
    while self.peek():
        token_type = self.peek_type()
        if depth == 0 and token_type in terminators:
            break
        if depth == 0 and pieces and self.peek_line() != start_line:
            break
        if token_type == "LPAREN":
            depth += 1
        elif token_type == "RPAREN":
            if depth == 0 and "RPAREN" in terminators:
                break
            depth -= 1
        pieces.append(self.consume())
    return " ".join(pieces).strip()

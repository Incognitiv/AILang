"""Class-declaration parser helpers extracted from parser_declarations_impl."""

from __future__ import annotations

from typing import Optional

from token_access import token_text_at, token_type_at

from .ast import ASTNode, ClassDef, Function, GenericClass, ParsedType


def _parse_class_destructor(self, class_name: str, visibility: str) -> Function:
    """Parse one-liner empty destructor: ~ClassName()."""
    start_line = self.peek_line()  # for the source-map
    self.consume("TILDE")
    destructor_name = self.consume("IDENT")
    if destructor_name != class_name:
        self.error(f"Destructor must be named ~{class_name}, got ~{destructor_name}")
    self.consume("LPAREN")
    self.consume("RPAREN")
    dtor = Function(
        name=f"~{class_name}",
        params=[],
        return_type="void",
        body=[],
        is_public=(visibility == "public"),
    )
    dtor.set_pos(start_line)
    return dtor


def _parse_init_params(self) -> list[tuple[str, ParsedType, Optional[ASTNode]]]:
    """Parse legacy ``init(...)`` parameters using normal function rules."""
    params: list[tuple[str, ParsedType, Optional[ASTNode]]] = []
    if self.peek_type() == "RPAREN":
        return params
    params.append(self._parse_single_param())
    while self.peek_type() == "COMMA":
        self.consume("COMMA")
        params.append(self._parse_single_param())
    return params


def _parse_class_init(self, visibility: str) -> Function:
    """Parse legacy bare ``init(...)`` method with optional body.

    Canonical class code should spell this as ``void init(...):`` so every
    method uses the same type-name-parameter/body contract as a free function.
    """
    start_line = self.peek_line()  # for the source-map
    self.consume("IDENT")  # consume 'init'
    self.consume("LPAREN")
    params = self._parse_init_params()
    self.consume("RPAREN")
    if self.peek_type() == "COLON":
        self.consume("COLON")
    self.skip_newlines()
    # Tokens that indicate the init has no body (next definition follows)
    # Note: IDENT removed - a body CAN start with an identifier (assignment)
    no_body_tokens = ("END", "PUBLIC", "PRIVATE", "DEF", "VOID", "TILDE")
    if self.peek_type() in no_body_tokens:
        empty_init = Function(
            name="init",
            params=params,
            return_type="void",
            body=[],
            is_public=(visibility == "public"),
        )
        empty_init.set_pos(start_line)
        return empty_init
    body: list[ASTNode] = []
    while self.peek() and self.peek_type() != "END":
        stmt = self.parse_statement()
        if stmt is not None:
            body.append(stmt)
        self.skip_newlines()
    self.consume("END")
    init_fn = Function(
        name="init",
        params=params,
        return_type="void",
        body=body,
        is_public=(visibility == "public"),
    )
    init_fn.set_pos(start_line)
    return init_fn


def _parse_class_field(
    self, visibility: str
) -> tuple[str, str, ParsedType, Optional[ASTNode]]:
    """Parse a class field: type fieldName [= initialValue]."""
    field_type = self.parse_type()
    field_name = self.consume("IDENT")
    init_value: Optional[ASTNode] = None
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        init_value = self.parse_expression()
    if self.peek_type() == "SEMICOLON":
        self.consume("SEMICOLON")
    return (visibility, field_name, field_type, init_value)


def parse_class(self) -> ClassDef | GenericClass:
    """Parse class definition with optional generic type parameters.

    Canonical and only syntax uses ``class Name:``.
    """
    from parser import ast as A

    self.consume("CLASS")
    class_name = self.consume("IDENT")

    # Check for generic type parameters
    type_params = self._parse_generic_params()

    if self.peek_type() != "COLON":
        self.error("Expected ':' after class name; 'then' is not valid for class declarations")
    self.consume("COLON")
    self.skip_newlines()

    fields: list[tuple[str, str, ParsedType, Optional[ASTNode]]] = []
    methods: list[Function] = []

    while self.peek() and self.peek_type() != "END":
        visibility = self._parse_visibility()
        if self.peek_type() == "TILDE":
            methods.append(self._parse_class_destructor(class_name, visibility))
        elif _is_class_destructor_method_start(self):
            methods.append(self._parse_method(visibility, class_name))
        elif self._is_init_method():
            methods.append(self._parse_class_init(visibility))
        elif _is_class_method_start(self):
            method = self.parse_function()
            if not isinstance(method, Function):
                self.error("Generic methods are not supported inside classes yet")
            method.is_public = visibility == "public"
            methods.append(method)
        else:
            fields.append(self._parse_class_field(visibility))
        self.skip_newlines()

    self.consume("END")

    if type_params:
        return A.GenericClass(class_name, type_params, fields, methods)
    return ClassDef(class_name, fields, methods)


def _parse_visibility(self) -> str:
    """Parse visibility modifier (public/private)."""
    if self.peek_type() == "PUBLIC":
        self.consume("PUBLIC")
        return "public"
    if self.peek_type() == "PRIVATE":
        self.consume("PRIVATE")
        return "private"
    return "public"


def _is_init_method(self) -> bool:
    """Check if current position is an init() method."""
    if self.peek_type() != "IDENT" or token_text_at(self.tokens, self.pos) != "init":
        return False
    return (
        self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "LPAREN"
    )


def _is_class_method_start(self) -> bool:
    """Return whether the current token starts a normal class method.

    Methods deliberately reuse the free-function parser so ``int get():``,
    ``pointer data():`` and legacy ``def get(): int`` have one grammar and one
    return-type model.
    """
    token_type = self.peek_type()
    if token_type in ("DEF", "VOID", "ASYNC"):
        return True
    if token_type in self._TYPE_TOKENS:
        return (
            self.pos + 2 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "IDENT"
            and token_type_at(self.tokens, self.pos + 2) == "LPAREN"
        )
    if token_type == "IDENT" and self._is_type_ident(self.peek_text()):
        return (
            self.pos + 2 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "IDENT"
            and token_type_at(self.tokens, self.pos + 2) == "LPAREN"
        )
    return False


def _is_class_destructor_method_start(self) -> bool:
    """Recognize body-bearing destructor declarations.

    Both legacy ``def ~Class():`` and canonical ``void ~Class():`` are
    accepted while class syntax is being normalized.
    """
    if self.peek_type() not in ("DEF", "VOID"):
        return False
    return (
        self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "TILDE"
    )


def _parse_method(self, visibility: str, class_name: str = "") -> Function:
    """Parse a method inside a class."""
    start_line = self.peek_line()  # for the source-map / debug tooling
    is_void_prefix = False
    is_destructor = False

    if self.peek_type() == "VOID":
        self.consume("VOID")
        is_void_prefix = True
        if self.peek_type() == "TILDE":
            self.consume("TILDE")
            destructor_name = self.consume("IDENT")
            if destructor_name != class_name:
                self.error(
                    f"Destructor must be named ~{class_name}, got ~{destructor_name}"
                )
            name = f"~{destructor_name}"
            is_destructor = True
        else:
            name = self.consume("IDENT")
    else:
        self.consume("DEF")
        if self.peek_type() == "TILDE":
            self.consume("TILDE")
            destructor_name = self.consume("IDENT")
            if destructor_name != class_name:
                self.error(
                    f"Destructor must be named ~{class_name}, got ~{destructor_name}"
                )
            name = f"~{destructor_name}"
            is_destructor = True
        else:
            name = self.consume("IDENT")

    self.consume("LPAREN")

    params: list[tuple[str, ParsedType, Optional[ASTNode]]] = []
    if is_destructor and self.peek_type() != "RPAREN":
        self.error("Destructor cannot have parameters")
        raise AssertionError("unreachable")

    if self.peek_type() != "RPAREN":
        params.append(self._parse_single_param())
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            params.append(self._parse_single_param())

    self.consume("RPAREN")

    return_type: ParsedType
    if is_void_prefix or is_destructor:
        return_type = "void"
        if self.peek_type() == "COLON":
            self.consume("COLON")
    else:
        return_type = "i64"
        if self.peek_type() == "COLON":
            self.consume("COLON")
            self.skip_newlines()
            if self._is_type_token():
                return_type = self.parse_type()

    self.skip_newlines()

    body: list[ASTNode] = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")

    is_public = visibility == "public"
    func = Function(name, params, return_type, body, is_public=is_public)
    func.set_pos(start_line)
    return func

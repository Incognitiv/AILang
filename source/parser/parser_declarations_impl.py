"""Parser helper implementations extracted from parser.py."""

from __future__ import annotations

from typing import Optional

from token_access import token_type_at

from .ast import (
    ASTNode,
    BinaryOp,
    Call,
    ExternRecordDef,
    Function,
    GenericFunction,
    GenericRecord,
    ParsedType,
    RecordDef,
    Return,
    StringLit,
    TemplateBlock,
    UnionDef,
    Variable,
    parsed_type_to_str,
)
from .parser_internal_abi_impl import (
    _is_internal_keyword,
    _parse_internal_function,
)


def _parse_decorators(self) -> list[str]:
    """Parse one or more decorators.

    Supported forms:
      - ``@name``
      - ``@effect(fs, sqlite)``
      - tokenized forms like ``@noalias`` / ``@pure``
    """
    decorators: list[str] = []
    # Fortran-style optimization decorators (single tokens)
    fortran_decorators = {
        "NOALIAS",
        "PURE",
        "INLINE",
        "FASTMATH",
        "UNCHECKED",
        "SYNCHRONIZED",
    }
    while self.peek_type() == "AT" or self.peek_type() in fortran_decorators:
        if self.peek_type() in fortran_decorators:
            dec_type = self.consume()
            # Strip @ prefix and convert to lowercase
            dec_name = dec_type.lstrip("@").lower()
            decorators.append(dec_name)
        else:
            self.consume("AT")
            name = self.consume("IDENT")
            if self.peek_type() == "LPAREN":
                self.consume("LPAREN")
                args: list[str] = []
                while self.peek_type() != "RPAREN":
                    arg_tok = self.peek_type()
                    if arg_tok in {"IDENT", "STRING", "STRLIT"}:
                        raw = self.consume(arg_tok)
                        arg = raw[1:-1] if arg_tok == "STRLIT" else raw
                        args.append(arg.strip())
                    else:
                        # Keep parser strict and point directly at the bad token.
                        self.error(
                            f"Expected decorator argument, got {self.peek_type()}"
                        )
                    if self.peek_type() == "COMMA":
                        self.consume("COMMA")
                    elif self.peek_type() != "RPAREN":
                        self.error("Expected ',' or ')' in decorator argument list")
                self.consume("RPAREN")
                decorators.append(f"{name}({','.join(args)})")
            else:
                decorators.append(name)
        self.skip_newlines()
    return decorators


def _parse_generic_params(self) -> list:
    """Parse generic type parameters: [T], [T, U], [T: Comparable], etc.

    Syntax:
        [T]                    - Single type parameter
        [T, U]                 - Multiple type parameters
        [T: Comparable]        - Type parameter with constraint
        [T = int]              - Type parameter with default
        [T: Number = int]      - Constraint and default

    Returns:
        List of GenericParam AST nodes.
    """
    from parser import ast as A

    if self.peek_type() != "LBRACKET":
        return []

    self.consume("LBRACKET")
    params: list[A.GenericParam] = []

    while self.peek_type() != "RBRACKET":
        # Parse type parameter name
        param_name = self.consume("IDENT")
        constraint = None
        default = None

        # Parse optional constraint: T: Comparable
        if self.peek_type() == "COLON":
            self.consume("COLON")
            constraint = self.consume("IDENT")

        # Parse optional default: T = int
        if self.peek_type() == "ASSIGN":
            self.consume("ASSIGN")
            default = self.consume("IDENT")

        params.append(A.GenericParam(param_name, constraint, default))

        # Check for comma (more params) or end
        if self.peek_type() == "COMMA":
            self.consume("COMMA")
        else:
            break

    self.consume("RBRACKET")
    return params


def _parse_where_constraints(self, generic_params: list) -> None:
    """Parse postfix generic constraints: where T: Comparable, U: Numeric."""
    if self.peek_type() != "WHERE":
        return
    self.consume("WHERE")

    by_name = {getattr(param, "name", ""): param for param in generic_params}
    while True:
        param_name = self.consume("IDENT")
        self.consume("COLON")
        constraint = self.consume("IDENT")
        if param_name in by_name:
            by_name[param_name].constraint = constraint
        if self.peek_type() != "COMMA":
            break
        self.consume("COMMA")


def _parse_generic_args(self) -> list[str]:
    """Parse generic type arguments: [int], [string, int], etc."""
    if self.peek_type() != "LBRACKET":
        return []

    self.consume("LBRACKET")
    args: list[str] = []

    while self.peek_type() != "RBRACKET":
        # Parse type argument
        type_arg = self.parse_type()
        if isinstance(type_arg, tuple):
            args.append(str(type_arg))
        else:
            args.append(type_arg)

        if self.peek_type() == "COMMA":
            self.consume("COMMA")
        else:
            break

    self.consume("RBRACKET")
    return args


def _is_generic_call(self) -> bool:
    """Lookahead to check if name[...] is a generic call name[Type](args).

    Scans forward from the current LBRACKET to find the matching RBRACKET,
    then checks if a LPAREN follows.  Doesn't consume any tokens.
    """
    # Current token should be LBRACKET
    if self.peek_type() != "LBRACKET":
        return False

    depth = 0
    scan = self.pos
    while scan < len(self.tokens):
        tok_type = token_type_at(self.tokens, scan)
        if tok_type == "LBRACKET":
            depth += 1
        elif tok_type == "RBRACKET":
            depth -= 1
            if depth == 0:
                # Check what follows the closing bracket
                if scan + 1 < len(self.tokens):
                    return token_type_at(self.tokens, scan + 1) == "LPAREN"
                return False
        scan += 1
    return False


def _parse_generic_call(self, name: str) -> ASTNode:
    """Parse generic function call: name[type_args](args).

    Returns a Call node to the mangled function name.
    The monomorphizer will create the specialized function during codegen.
    """
    from .naming import mangle_generic_name

    type_args = self._parse_generic_args()

    # Now parse the call arguments
    args, is_unsafe = self._parse_arg_list()

    # Create a GenericCall node that carries the type args
    # We use a Call with the mangled name + attach type_args for codegen
    mangled_name = mangle_generic_name(name, type_args)
    call_node = Call(mangled_name, args, unsafe=is_unsafe)

    # Store the original generic info so codegen can trigger instantiation
    call_node.generic_base = name
    call_node.generic_type_args = type_args

    return self._parse_postfix_ops(call_node)


def parse_function(
    self, decorators: list[str] | None = None
) -> Function | GenericFunction:
    """Parse function with multiple syntaxes:

    Standard:     [public|private] def name(params): [return_type] ... end
    Async:        [public|private] async def name(params): [return_type] ... end
    Test:         test "description" ... end
    Void prefix:  [public|private] void name(params): ... end
    Type prefix:  [public|private] int/string/etc name(params): ... end
    With decorators: @decorator def name(params): ... end
    """
    # Capture the source line BEFORE consuming any tokens - used by the
    # profiler / debug tooling to render `func @ file.ail:42`. Stamped
    # onto every return path via the wrapper helper at the bottom.
    start_line = self.peek_line()
    is_public = True
    is_async = False
    is_test = False
    decorators = decorators or []

    # Check for visibility modifier
    if self.peek_type() in ("PUBLIC", "PRIVATE"):
        is_public = self.peek_type() == "PUBLIC"
        self.consume()

    # Check for async modifier
    if self.peek_type() == "ASYNC":
        self.consume("ASYNC")
        is_async = True

    if _is_internal_keyword(self):
        return _parse_internal_function(
            self, decorators, start_line, is_public, is_async
        )

    # Check for test function: test "description" ... end
    if self.peek_type() == "TEST":
        self.consume("TEST")
        is_test = True
        # Test name can be a string literal or identifier
        if self.peek_type() == "STRLIT":
            test_name = self.consume("STRLIT")
            # Remove quotes and convert to valid identifier
            name = "test_" + test_name.strip('"').replace(" ", "_").replace("-", "_")
        else:
            name = "test_" + self.consume("IDENT")
        # Tests have no params and return void
        self.skip_newlines()
        test_body: list[ASTNode] = []
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                test_body.append(stmt)
            self.skip_newlines()
        self.consume("END")
        test_func = Function(
            name,
            [],  # No params
            "void",  # Return void
            test_body,
            is_public=False,
            decorators=decorators,
            is_async=False,
            is_test=True,
        )
        test_func.set_pos(start_line)
        return test_func

    # Type tokens that can be used as prefix (e.g., "int func():")
    TYPE_PREFIX_TOKENS = (
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
        "QUAD",
        "PTR",
    )

    is_type_prefix = False
    type_prefix_type: ParsedType = "i64"

    # Check for type prefix syntax: int/string/void/etc function_name(args):
    is_ident_type_prefix = (
        self.peek_type() == "IDENT"
        and self._is_type_ident(self.peek_text())
        and self.pos + 2 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "IDENT"
        and token_type_at(self.tokens, self.pos + 2) == "LPAREN"
    )
    if self.peek_type() in TYPE_PREFIX_TOKENS or is_ident_type_prefix:
        # Check if this is actually a type-prefix function (TYPE IDENT LPAREN)
        if (
            self.pos + 2 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "IDENT"
            and token_type_at(self.tokens, self.pos + 2) == "LPAREN"
        ):
            # This is type-prefix syntax: int func():
            type_prefix_type = self.parse_type()
            is_type_prefix = True
            name = self.consume("IDENT")
        else:
            # Not a type-prefix function, must be def
            self.consume("DEF")
            if self.peek_type() == "TEST":
                name = "test"
                self.consume("TEST")
            else:
                name = self.consume("IDENT")
    else:
        # Standard syntax: def function_name(args):
        self.consume("DEF")
        # Allow TEST keyword as function name (e.g., def test():)
        if self.peek_type() == "TEST":
            name = "test"
            self.consume("TEST")
        else:
            name = self.consume("IDENT")

    # Check for generic type parameters: def foo[T](...)
    generic_params = self._parse_generic_params()

    self.consume("LPAREN")

    # Parse parameters using helper method
    params: list[tuple[str, ParsedType, Optional[ASTNode]]] = []
    if self.peek_type() != "RPAREN":
        params.append(self._parse_single_param())
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            params.append(self._parse_single_param())

    self.consume("RPAREN")

    # Determine return type
    return_type: ParsedType
    if is_type_prefix:
        # Type prefix syntax (int/string/void/etc func():) - type already known
        return_type = type_prefix_type
        # Colon is required
        self.consume("COLON")
    else:
        # Standard def syntax - colon required after closing paren
        return_type = "i64"
        colon_tok = self.peek()
        colon_line = self._get_token_line(colon_tok)
        self.consume("COLON")
        self.skip_newlines()
        pt = self.peek_type()
        type_token = self.peek()
        type_token_line = self._get_token_line(type_token)

        # If the type token is on the SAME line as the colon, it's a return type
        # Variable declaration pattern only applies after a newline
        is_same_line = colon_line == type_token_line and colon_line != -1

        # Check if this looks like a return type annotation
        # NOT a return type if pattern is: TYPE IDENT ASSIGN (variable decl)
        # But only check this if we're on a different line
        is_var_decl_pattern = (
            not is_same_line
            and self.pos + 2 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "IDENT"
            and token_type_at(self.tokens, self.pos + 2) == "ASSIGN"
        )
        is_slice_like_type = (
            pt == "IDENT"
            and self.peek_text() in ("slice", "view")
            and self.pos + 1 < len(self.tokens)
            and token_type_at(self.tokens, self.pos + 1) == "LBRACKET"
        )
        is_type_token = (
            pt
            in (
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
                "ARRAY",
                "PTR",
                "LBRACKET",
            )
            or is_slice_like_type
            or (type_token is not None and self._is_type_ident(self.peek_text()))
        )
        # Only parse as return type if it's a type token AND not a var decl
        if is_type_token and not is_var_decl_pattern:
            return_type = self.parse_type()
        else:
            return_type = "i64"

    if generic_params:
        self._parse_where_constraints(generic_params)

    self.skip_newlines()

    body: list[ASTNode] = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    # Skip string type inference for generic functions (types are abstract)
    if not generic_params:
        params, return_type = self._infer_string_types(params, return_type, body)

    self.consume("END")

    # Return GenericFunction if type params were present
    if generic_params:
        generic_fn = GenericFunction(
            name,
            generic_params,
            params,
            return_type,
            body,
            decorators=decorators,
        )
        generic_fn.set_pos(start_line)
        return generic_fn

    plain_fn = Function(
        name,
        params,
        return_type,
        body,
        is_public=is_public,
        decorators=decorators,
        is_async=is_async,
        is_test=is_test,
    )
    plain_fn.set_pos(start_line)
    return plain_fn


def _infer_string_types(
    self,
    params: list[tuple[str, ParsedType, Optional[ASTNode]]],
    return_type: ParsedType,
    body: list[ASTNode],
) -> tuple[list[tuple[str, ParsedType, Optional[ASTNode]]], ParsedType]:
    # Extract name->type mapping, handling 2 or 3-tuple params
    param_names: dict[str, ParsedType] = {}
    for p in params:
        if len(p) == 2:
            param_names[p[0]] = p[1]
        else:
            param_names[p[0]] = p[1]

    def expr_is_string(expr: ASTNode) -> bool:
        if isinstance(expr, StringLit):
            return True
        if isinstance(expr, BinaryOp) and expr.op == "+":
            return expr_is_string(expr.left) or expr_is_string(expr.right)
        return False

    def get_type_str(ptype: ParsedType) -> str:
        """Extract canonical string from ParsedType."""
        return parsed_type_to_str(ptype)

    def mark_param_strings(expr: ASTNode) -> None:
        if isinstance(expr, Call):
            for arg in expr.args:
                mark_param_strings(arg)
        elif isinstance(expr, BinaryOp) and expr.op == "+":
            if isinstance(expr.left, Variable) and isinstance(expr.right, StringLit):
                name = expr.left.name
                if get_type_str(param_names.get(name, "")).upper() == "INT":
                    param_names[name] = "string"
            if isinstance(expr.right, Variable) and isinstance(expr.left, StringLit):
                name = expr.right.name
                if get_type_str(param_names.get(name, "")).upper() == "INT":
                    param_names[name] = "string"
            mark_param_strings(expr.left)
            mark_param_strings(expr.right)

    for stmt in body:
        if isinstance(stmt, Return) and stmt.value:
            rt_str = get_type_str(return_type)
            if expr_is_string(stmt.value) and rt_str.lower() == "i64":
                return_type = "string"
            mark_param_strings(stmt.value)
        elif isinstance(stmt, (Call, BinaryOp)):
            mark_param_strings(stmt)

    # Rebuild params preserving defaults (3-tuple format)
    new_params: list[tuple[str, ParsedType, Optional[ASTNode]]] = []
    for p in params:
        name = p[0]
        default = p[2] if len(p) == 3 else None
        new_params.append((name, param_names[name], default))
    return new_params, return_type


def parse_record(self) -> RecordDef | GenericRecord:
    """Parse: record Name[T, U] then type field1; type field2; ... end

    Supports generic type parameters in brackets.
    """
    from parser import ast as A

    self.consume("RECORD")
    name = self.consume("IDENT")

    # Check for generic type parameters
    type_params = self._parse_generic_params()

    self.consume("THEN")
    self.skip_newlines()

    fields: list[tuple[str, str]] = []
    while self.peek() and self.peek_type() != "END":
        # Parse field: type name
        field_type = self.parse_type()
        field_name = self.consume("IDENT")
        # Convert type to string (handle tuple for arrays)
        field_type_str = parsed_type_to_str(field_type)
        fields.append((field_name, field_type_str))

        # Optional semicolon
        if self.peek() and self.peek_type() == "SEMICOLON":
            self.consume("SEMICOLON")

        self.skip_newlines()

    self.consume("END")

    # Return generic or regular record
    if type_params:
        return A.GenericRecord(name, type_params, fields)
    return RecordDef(name, fields)


def parse_opaque_record(self) -> ExternRecordDef:
    """Parse: opaque record Name [= "native C type"]."""
    from parser import ast as A

    self.consume("OPAQUE")
    self.consume("RECORD")
    name = self.consume("IDENT")
    c_name = name
    c_name_explicit = False
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        c_name_explicit = True
        if self.peek_type() == "STRLIT":
            raw = self.consume("STRLIT")
            c_name = raw[1:-1]
        elif self.peek_type() == "IDENT":
            c_name = self.consume("IDENT")
        else:
            self.error("Expected C type name string after opaque record '='")
    return A.ExternRecordDef(
        name, is_opaque=True, c_name=c_name, c_name_explicit=c_name_explicit
    )


def parse_union(self) -> UnionDef:
    """Parse: union Name then type field1; type field2; ... end"""
    from parser import ast as A

    self.consume("UNION")
    name = self.consume("IDENT")
    self.consume("THEN")
    self.skip_newlines()

    fields: list[tuple[str, str]] = []
    while self.peek() and self.peek_type() != "END":
        field_type = self.parse_type()
        field_name = self.consume("IDENT")
        field_type_str = parsed_type_to_str(field_type)
        fields.append((field_name, field_type_str))
        if self.peek() and self.peek_type() == "SEMICOLON":
            self.consume("SEMICOLON")
        self.skip_newlines()

    self.consume("END")
    return A.UnionDef(name, fields)


def parse_template_block(self) -> TemplateBlock:
    """Parse template block: #template lang\n...code...\n#end"""
    # The TEMPLATE_BLOCK token contains the entire block text
    block_text = self.consume("TEMPLATE_BLOCK")
    # Parse: #template lang\n...code...\n#end
    lines = block_text.split("\n")
    # First line: #template lang
    first_line = lines[0].strip()
    if first_line.startswith("#template"):
        lang = first_line[9:].strip()  # Extract language after "#template"
    else:
        lang = "ansi_c"
    # Last line: #end
    # Code is everything between first and last line
    if len(lines) > 2:
        code = "\n".join(lines[1:-1])
    elif len(lines) == 2:
        code = ""
    else:
        code = ""
    return TemplateBlock(lang, code)

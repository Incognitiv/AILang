"""Parser helper implementations extracted from parser.py."""

from __future__ import annotations

from typing import Optional

from token_access import token_type_at

from .ast import (
    ArrayAccess,
    Assert,
    Assign,
    ASTNode,
    BinaryOp,
    Block,
    BlockCall,
    Break,
    Call,
    Continue,
    DictAssign,
    FieldAccess,
    FieldAssign,
    If,
    MethodCall,
    Number,
    Return,
    TupleAssign,
    UnaryOp,
    VarDecl,
    Variable,
    While,
)


def _parse_reinterpret_cast(self) -> ASTNode:
    """Parse: reinterpret(target_type, expr) or bitcast(target_type, expr)"""
    from parser import ast as A

    self.consume()  # consume REINTERPRET or BITCAST
    self.consume("LPAREN")
    target_type = self._parse_type_name()
    self.consume("COMMA")
    value = self.parse_expression()
    self.consume("RPAREN")
    return A.ReinterpretCast(target_type, value)


# ========================================================================
# Statement parsing
# ========================================================================


def _parse_prefix_increment_stmt(self) -> ASTNode:
    """Parse prefix increment/decrement as statement sugar.

    AILang currently treats ++/-- as statement-only mutation syntax, matching
    the existing postfix `i++` / `i--` support. Expression-valued C semantics
    like `x = ++i` are intentionally not introduced here.
    """
    token_type = self.peek_type()
    if token_type == "PLUSPLUS":
        self.consume("PLUSPLUS")
        op = "+"
    else:
        self.consume("MINUSMINUS")
        op = "-"

    var_name = self.consume("IDENT")
    one = Number("1", is_long=False, is_float=False)
    return Assign(var_name, BinaryOp(op, Variable(var_name), one))


def _parse_return_stmt(self) -> ASTNode:
    """Parse return statement, possibly with postfix conditional

    Handles both:
        return expr      -- return with value
        return           -- void return (no expression)
    """
    self.consume("RETURN")

    # Check if this is a void return (next token is END, NEWLINE, or statement keyword)
    next_type = self.peek_type()
    if next_type in ("END", "NEWLINE", "ELSIF", "ELSE", "EOF", None):
        stmt = Return(None)
    else:
        value = self.parse_expression()
        stmt = Return(value)
    return self._wrap_postfix_conditional(stmt)


def _parse_break_stmt(self) -> ASTNode:
    """Parse break statement, possibly with postfix conditional"""
    self.consume("BREAK")
    stmt = Break()
    return self._wrap_postfix_conditional(stmt)


def _parse_continue_stmt(self) -> ASTNode:
    """Parse continue statement, possibly with postfix conditional"""
    self.consume("CONTINUE")
    stmt = Continue()
    return self._wrap_postfix_conditional(stmt)


def _parse_assert_stmt(self) -> ASTNode:
    """Parse assert statement: assert condition [, message]"""
    self.consume("ASSERT")
    condition = self.parse_expression()

    # Check for optional message
    message = None
    if self.peek_type() == "COMMA":
        self.consume("COMMA")
        message = self.parse_expression()

    return Assert(condition, message)


def _parse_comptime(self) -> ASTNode:
    """Parse compile-time expression or block.

    Syntax:
        comptime expr              - Evaluate expr at compile time
        comptime then ... end      - Execute block at compile time
        comptime if cond then ... end - Conditional compilation
    """
    from parser import ast as A

    self.consume("COMPTIME")

    # Check for comptime if (conditional compilation)
    if self.peek_type() == "IF":
        self.consume("IF")
        cond = self.parse_expression()
        self.consume("THEN")
        self.skip_newlines()

        then_body: list[ASTNode] = []
        while self._not_block_end("ELSE", "END"):
            stmt = self.parse_statement()
            if stmt:
                then_body.append(stmt)
            self.skip_newlines()

        else_body: list[ASTNode] = []
        if self.peek_type() == "ELSE":
            self.consume("ELSE")
            self.skip_newlines()
            while self._not_block_end("END"):
                stmt = self.parse_statement()
                if stmt:
                    else_body.append(stmt)
                self.skip_newlines()

        self.consume("END")
        return A.ComptimeIf(cond, then_body, else_body)

    # Check for comptime block
    if self.peek_type() == "THEN":
        self.consume("THEN")
        self.skip_newlines()

        body: list[ASTNode] = []
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                body.append(stmt)
            self.skip_newlines()

        self.consume("END")
        return A.ComptimeBlock(body)

    # Simple comptime expression
    expr = self.parse_expression()
    return A.ComptimeExpr(expr)


def _parse_static_assert(self) -> ASTNode:
    """Parse static assertion: static_assert cond [, "message"]

    Fails compilation if condition is false at compile time.
    """
    from parser import ast as A

    self.consume("STATIC_ASSERT")
    condition = self.parse_expression()

    message = None
    if self.peek_type() == "COMMA":
        self.consume("COMMA")
        msg_token = self.consume("STRLIT")
        message = msg_token.strip('"')

    return A.StaticAssert(condition, message)


def _is_statement_start(self) -> bool:
    """Check if current token starts a new statement.

    Used to detect statement boundaries in expression parsing.
    """
    if not self.peek():
        return False
    token_type = self.peek_type()
    # These tokens can only start statements, not continue expressions
    statement_starters = {
        "IF",
        "UNLESS",
        "WHILE",
        "UNTIL",
        "FOR",
        "FOREACH",
        "LOOP",
        "RETURN",
        "BREAK",
        "CONTINUE",
        "PRINT",
        "PUTS",
        "PUTC",
        "MATCH",
        "DEF",
        "END",
        "RECORD",
        "ENUM",
        "CLASS",
        "PUBLIC",
        "PRIVATE",
        "TRY",
        "REPEAT",
        "IMPORT",
        "FROM",
        "USE",
    }
    if token_type in statement_starters:
        return True
    # IDENT followed by ASSIGN is a statement (assignment)
    if token_type == "IDENT":
        next_pos = self.pos + 1
        if (
            next_pos < len(self.tokens)
            and token_type_at(self.tokens, next_pos) == "ASSIGN"
        ):
            return True
        # IDENT followed by COMMA then IDENT is tuple unpacking
        if (
            next_pos < len(self.tokens)
            and token_type_at(self.tokens, next_pos) == "COMMA"
        ):
            return True
    return False


def _parse_print_stmt(self, token_type: str) -> ASTNode:
    """Parse print/puts statement"""
    fn_name = self.consume(token_type)
    args: list[ASTNode] = []
    if self.peek_type() == "LPAREN":
        self.consume("LPAREN")
        if self.peek_type() != "RPAREN":
            args.append(self.parse_expression())
            while self.peek_type() == "COMMA":
                self.consume("COMMA")
                args.append(self.parse_expression())
        self.consume("RPAREN")
    else:
        # Parse first argument
        args.append(self.parse_expression())
        # Continue only if COMMA and not starting a new statement
        while self.peek_type() == "COMMA" and not self._is_statement_start():
            self.consume("COMMA")
            if self._is_statement_start():
                break
            args.append(self.parse_expression())
    stmt: ASTNode = Call(fn_name.lower(), args)
    return self._wrap_postfix_conditional(stmt)


def _parse_typed_var_decl(self, token_type: str) -> VarDecl:
    """Parse variable declaration with type annotation"""
    is_public = False
    is_const = False

    if token_type == "PUBLIC":
        is_public = True
        self.consume("PUBLIC")
        token_type = self.peek_type() or ""
    elif token_type == "PRIVATE":
        self.consume("PRIVATE")
        token_type = self.peek_type() or ""

    if token_type == "CONST":
        is_const = True
        self.consume("CONST")

    type_name = self.parse_type()
    var_name = self.consume("IDENT")
    self.consume("ASSIGN")
    init_value = self.parse_expression()
    return VarDecl(type_name, var_name, init_value, is_const, is_public)


def _lookahead_for_assign(self) -> bool:
    """Check if the next token after current is ASSIGN"""
    next_pos = self.pos + 1
    return (
        next_pos < len(self.tokens) and token_type_at(self.tokens, next_pos) == "ASSIGN"
    )


def _lookahead_is_custom_type_decl(self) -> bool:
    """Return true when a PascalCase identifier is used as a variable type.

    `_is_type_ident()` intentionally treats PascalCase names as possible record
    and class types, but statement parsing must not steal PascalCase function
    calls such as Win32's `DestroyWindow(hwnd)`. Treat it as a declaration only
    when the type name is followed by a variable identifier, optionally after
    generic brackets: `Rect r = ...`, `Box[int] b = ...`.
    """
    pos = self.pos + 1
    if pos < len(self.tokens) and token_type_at(self.tokens, pos) == "LBRACKET":
        depth = 1
        pos += 1
        while pos < len(self.tokens) and depth > 0:
            tok = token_type_at(self.tokens, pos)
            if tok == "LBRACKET":
                depth += 1
            elif tok == "RBRACKET":
                depth -= 1
            pos += 1
        if depth != 0:
            return False
    return pos < len(self.tokens) and token_type_at(self.tokens, pos) == "IDENT"


def _parse_tuple_assign(self) -> TupleAssign:
    """Parse tuple unpacking: a, b = b, a or a, b, c = get_values()"""
    var_names: list[str] = []

    # Collect all target variable names
    var_names.append(self.consume("IDENT"))
    while self.peek_type() == "COMMA":
        self.consume("COMMA")
        if self.peek_type() == "ASSIGN":
            break  # Trailing comma before =
        var_names.append(self.consume("IDENT"))

    self.consume("ASSIGN")

    # Parse right-hand side expressions
    values: list[ASTNode] = []
    values.append(self.parse_expression())
    while self.peek_type() == "COMMA":
        self.consume("COMMA")
        values.append(self.parse_expression())

    return TupleAssign(var_names, values)


def _parse_dot_access_stmt(self) -> ASTNode:
    """Parse dot access statement (field assign, method call, block call)"""
    var_name = self.consume("IDENT")
    self.consume("DOT")
    # Accept TIMES keyword or IDENT as method/field name
    if self.peek_type() == "TIMES":
        field_name = self.consume("TIMES")
    else:
        field_name = self._consume_field_name()

    # Check for chained subscript: obj.field[index] = value
    if self.peek() and self.peek_type() == "LBRACKET":
        self.consume("LBRACKET")
        index_expr = self.parse_expression()
        is_unsafe = False
        if self.peek_type() == "COMMA":
            self.consume("COMMA")
            if self.peek_type() == "UNSAFE":
                self.consume("UNSAFE")
                is_unsafe = True
        self.consume("RBRACKET")

        if self.peek() and self.peek_type() == "ASSIGN":
            self.consume("ASSIGN")
            value = self.parse_expression()
            # Create DictAssign with ArrayAccess(FieldAccess(...), index) as target
            field_access = FieldAccess(Variable(var_name), field_name)
            return DictAssign(field_access, index_expr, value)

        # Not an assignment, return ArrayAccess of FieldAccess
        field_access = FieldAccess(Variable(var_name), field_name)
        return ArrayAccess(field_access, index_expr, unsafe=is_unsafe)

    if self.peek() and self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        value = self.parse_expression()
        return FieldAssign(Variable(var_name), field_name, value)

    # Method call with optional block
    if self.peek() and self.peek_type() == "LPAREN":
        self.consume("LPAREN")
        method_args: list[ASTNode] = []
        if self.peek_type() != "RPAREN":
            method_args.append(self.parse_expression())
            while self.peek_type() == "COMMA":
                self.consume("COMMA")
                method_args.append(self.parse_expression())
        self.consume("RPAREN")

        # Check for block: method() |params| then ... end
        if self.peek_type() == "PIPE":
            block = self._parse_block()
            return BlockCall(Variable(var_name), field_name, method_args, block)

        return MethodCall(Variable(var_name), field_name, method_args)

    # Block call without parens: items.each |x| then ... end
    if self.peek_type() == "PIPE":
        block = self._parse_block()
        return BlockCall(Variable(var_name), field_name, [], block)

    return FieldAccess(Variable(var_name), field_name)


def _parse_block(self) -> Block:
    """Parse Ruby-style block: |params| then ... end"""
    self.consume("PIPE")

    # Parse block parameters
    params: list[str] = []
    if self.peek_type() != "PIPE":
        params.append(self.consume("IDENT"))
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            params.append(self.consume("IDENT"))

    self.consume("PIPE")
    self.consume("THEN")
    self.skip_newlines()

    # Parse block body
    body: list[ASTNode] = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return Block(params, body)


def _parse_subscript_stmt(self) -> ASTNode:
    """Parse subscript statement (array/dict assignment or access)

    Supports: arr[idx] = val, arr[idx, unsafe] = val
    """
    var_name = self.consume("IDENT")
    self.consume("LBRACKET")
    key_expr = self.parse_expression()

    # Check for unsafe keyword: arr[idx, unsafe]
    is_unsafe = False
    if self.peek_type() == "COMMA":
        self.consume("COMMA")
        if self.peek_type() == "UNSAFE":
            self.consume("UNSAFE")
            is_unsafe = True

    self.consume("RBRACKET")

    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        value = self.parse_expression()
        return DictAssign(Variable(var_name), key_expr, value, unsafe=is_unsafe)

    return ArrayAccess(Variable(var_name), key_expr, unsafe=is_unsafe)


# Type tokens that indicate a variable declaration
_TYPE_TOKENS = frozenset(
    {
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
        "ARRAY",
        "PTR",
        "CONST",
        "PUBLIC",
        "PRIVATE",
    }
)

# Tokens that can start a function/builtin call in parse_primary
_CALLABLE_TOKENS = frozenset(
    {
        "IDENT",
        "TEST",  # Allow 'test' as function name
        "PRINT",
        "PUTS",
        "PUTC",
        "LEN",
        "CHAR_AT",
        "ORD",
        "CHR",
        "SUBSTR",
        "STRLEN",
        "INPUT",
        "READ_FILE",
        "WRITE_FILE",
        "SQL_OPEN",
        "SQL_EXEC",
        "SQL_QUERY",
        "SQL_CLOSE",
        "SPAWN",  # Threading: spawn func()
        "JOIN",  # Threading: join(handle)
        "ATOMIC",  # Atomic operations
        "CHANNEL",  # Channels: channel(type, capacity)
        "CHAN_SEND",  # Channels: chan_send(ch, value)
        "CHAN_RECV",  # Channels: chan_recv(ch)
        "CHAN_TRY_SEND",  # Channels: chan_try_send(ch, value)
        "CHAN_TRY_RECV",  # Channels: chan_try_recv(ch)
        "CHAN_CLOSE",  # Channels: chan_close(ch)
        "ALLOC",  # Memory: alloc(bytes) -> ptr
        "DEALLOC",  # Memory: dealloc(ptr)
        "PEEK64",  # Memory: peek64(ptr, offset) -> i64
        "POKE64",  # Memory: poke64(ptr, offset, value)
        "PEEK32",  # Memory: peek32(ptr, offset) -> u32
        "POKE32",  # Memory: poke32(ptr, offset, value)
        "PEEK8",  # Memory: peek8(ptr, offset) -> u8
        "POKE8",  # Memory: poke8(ptr, offset, value)
    }
)


def parse_statement(
    self,
) -> Optional[ASTNode]:
    """Parse a statement using dispatch to specialized methods"""
    # Check depth limit to prevent stack overflow from deeply nested statements
    self._parse_depth += 1
    # Capture the line BEFORE dispatch so the resulting AST node carries
    # the source line of its first token. Used by codegen.di_location_for_line
    # to emit per-statement !DILocation metadata.
    stmt_line = self.peek_line()
    try:
        self._check_depth()
        node = self._parse_statement_impl()
        if node is not None and getattr(node, "line", 0) == 0 and stmt_line > 0:
            node.set_pos(stmt_line)
        return node
    finally:
        self._parse_depth -= 1


def _parse_statement_impl(self) -> Optional[ASTNode]:
    """Internal implementation of parse_statement."""
    token_type = self.peek_type()
    if not token_type:
        return None

    # Recovery: if we're positioned on an ASSIGN, step back to the ident
    if (
        token_type == "ASSIGN"
        and self.pos > 0
        and token_type_at(self.tokens, self.pos - 1) == "IDENT"
    ):
        self.pos -= 1
        token_type = self.peek_type()
    if token_type is None:
        self.error("Unexpected end of input in statement")

    # Handle @bounded(N) decorator for loops
    if token_type == "BOUNDED":
        return self._parse_bounded_loop()

    # Handle @bound decorator (auto-infer from condition)
    if token_type == "BOUND":
        return self._parse_auto_bound_loop()

    # Simple keyword statements - dispatch table
    simple_dispatch = {
        "RETURN": self._parse_return_stmt,
        "BREAK": self._parse_break_stmt,
        "CONTINUE": self._parse_continue_stmt,
        "ASSERT": self._parse_assert_stmt,  # Assert statement
        "IF": self.parse_if,
        "UNLESS": self.parse_unless,  # Ruby-style negative if
        "WHILE": self.parse_while,
        "DO": self.parse_do_while,  # do-while loop (LLVM-optimal structure)
        "UNTIL": self.parse_until,  # Ruby-style negative while
        "FOR": self.parse_for,
        "LOOP": self.parse_loop,
        "FOREACH": self.parse_foreach,
        "REPEAT": self.parse_repeat,
        "MATCH": self.parse_match,
        "TRY": self.parse_try,
        "THROW": self.parse_throw,
        "ASM": self.parse_asm,
        "SPAWN": self.parse_spawn,  # Threading: spawn function_call
        "COMPTIME": self._parse_comptime,  # Compile-time evaluation
        "STATIC_ASSERT": self._parse_static_assert,  # Compile-time assertion
    }

    if token_type in simple_dispatch:
        return simple_dispatch[token_type]()

    if token_type in ("PLUSPLUS", "MINUSMINUS"):
        return self._parse_prefix_increment_stmt()

    # Print/puts/putc statements
    if token_type in ("PRINT", "PUTS", "PUTC"):
        return self._parse_print_stmt(token_type)

    # Type-annotated variable declarations
    is_type_ident = token_type == "IDENT" and self._is_type_ident(self.peek_text())
    is_slice_like_type = (
        token_type == "IDENT"
        and self.peek_text() in ("slice", "view")
        and self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "LBRACKET"
    )
    is_custom_type_decl = is_type_ident and self._lookahead_is_custom_type_decl()
    if token_type in self._TYPE_TOKENS or is_custom_type_decl or is_slice_like_type:
        return self._parse_typed_var_decl(token_type)

    # Identifier-based statements (assignments, method calls, etc.)
    if token_type == "IDENT":
        return self._parse_ident_stmt()

    # TEST keyword can be used as variable name in assignments
    if token_type == "TEST" and self._lookahead_for_assign():
        return self._parse_keyword_as_ident_stmt()

    # This-based statements
    if token_type == "THIS":
        return self._parse_this_stmt()

    # Default: parse as expression
    expr = self.parse_expression()
    return self._wrap_postfix_conditional(expr)


def _wrap_postfix_conditional(self, stmt: ASTNode) -> ASTNode:
    """Check for postfix conditional: stmt if/unless/while cond

    Examples:
        return 0 if done
        break if count > max
        x = x + 1 while x < 10

    Note: Does NOT consume if the 'if' is followed by 'then' later,
    which indicates a block if statement, not a postfix conditional.
    """
    if not self.peek():
        return stmt

    token_type = self.peek_type()

    # Check if this looks like a postfix conditional
    # Postfix conditionals don't use 'then', block statements do
    if token_type in ("IF", "UNLESS", "WHILE", "UNTIL"):
        # Save position to potentially backtrack
        save_pos = self.pos
        self.consume()  # Consume IF/UNLESS/WHILE/UNTIL

        # Try to parse the condition expression
        # Note: We only backtrack on EOF/empty, not on genuine syntax errors
        cond = self.parse_expression()
        if cond is None:
            # No expression found, restore and return
            self.pos = save_pos
            return stmt

        # If followed by 'then', this is a block statement, not postfix
        if self.peek_type() == "THEN":
            # Restore position - let the main parser handle this
            self.pos = save_pos
            return stmt

        # It's a postfix conditional
        if token_type == "IF":
            return If(cond, [stmt], [])
        if token_type == "UNLESS":
            negated = UnaryOp("not", cond)
            return If(negated, [stmt], [])
        if token_type == "WHILE":
            return While(cond, [stmt])
        if token_type == "UNTIL":
            negated = UnaryOp("not", cond)
            return While(negated, [stmt])

    return stmt


# ========================================================================
# Control flow parsing
# ========================================================================

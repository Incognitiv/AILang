"""Parser helper implementations extracted from parser.py."""

from __future__ import annotations

from lexer.scan import char_literal_to_int

from .ast import (
    ArrayAccess,
    ArrayLit,
    ASTNode,
    Await,
    BinaryOp,
    BlockCall,
    Bool,
    Cast,
    DictLit,
    FieldAccess,
    InterpolatedString,
    ListComprehension,
    MethodCall,
    Null,
    Number,
    Range,
    SafeFieldAccess,
    StringLit,
    StringSlice,
    TernaryOp,
    ThisExpr,
    UnaryOp,
)


def parse_expression(self) -> ASTNode:
    """Parse expression with operator precedence"""
    self._parse_depth += 1
    self._check_depth()
    try:
        return self.parse_ternary()
    finally:
        self._parse_depth -= 1


def parse_ternary(self) -> ASTNode:
    """Parse ternary operator: cond ? true_expr : false_expr"""
    expr = self.parse_logical_or()

    if self.peek() and self.peek_type() == "QUESTION":
        self.consume("QUESTION")
        true_expr = self.parse_expression()
        self.consume("COLON")
        false_expr = self.parse_expression()
        return TernaryOp(expr, true_expr, false_expr)

    return expr


def parse_logical_or(self) -> ASTNode:
    """Parse logical OR"""
    left = self.parse_logical_and()

    while self.peek() and self.peek_type() == "OR":
        self.consume("OR")
        right = self.parse_logical_and()
        left = BinaryOp("or", left, right)

    return left


def parse_logical_and(self) -> ASTNode:
    """Parse logical AND"""
    left = self.parse_bitwise_or()

    while self.peek() and self.peek_type() == "AND":
        self.consume("AND")
        right = self.parse_bitwise_or()
        left = BinaryOp("and", left, right)

    return left


def parse_bitwise_or(self) -> ASTNode:
    """Parse bitwise OR: |, OR, NOR"""
    left = self.parse_bitwise_xor()

    while self.peek() and self.peek_type() in ("PIPE", "GATE_OR", "GATE_NOR"):
        op_type = self.peek_type()
        self.consume()
        right = self.parse_bitwise_xor()
        if op_type == "GATE_NOR":
            left = BinaryOp("NOR", left, right)
        else:
            left = BinaryOp("OR", left, right)

    return left


def parse_bitwise_xor(self) -> ASTNode:
    """Parse bitwise XOR: XOR, XNOR, ^"""
    left = self.parse_bitwise_and()

    while self.peek() and self.peek_type() in ("GATE_XOR", "GATE_XNOR"):
        op_type = self.peek_type()
        self.consume()
        right = self.parse_bitwise_and()
        if op_type == "GATE_XNOR":
            left = BinaryOp("XNOR", left, right)
        else:
            left = BinaryOp("XOR", left, right)

    return left


def parse_bitwise_and(self) -> ASTNode:
    """Parse bitwise AND: &, AND, NAND"""
    left = self.parse_equality()

    while self.peek() and self.peek_type() in (
        "AMPERSAND",
        "GATE_AND",
        "GATE_NAND",
    ):
        op_type = self.peek_type()
        self.consume()
        right = self.parse_equality()
        if op_type == "GATE_NAND":
            left = BinaryOp("NAND", left, right)
        else:
            left = BinaryOp("AND", left, right)

    return left


def parse_equality(self) -> ASTNode:
    """Parse equality operators: ==, !=, is, is not"""
    left = self.parse_comparison()

    while self.peek() and self.peek_type() in ("EQ", "NEQ", "IS", "IS_NOT"):
        op_type = self.peek_type()
        op = self.consume()  # Get the actual value
        right = self.parse_comparison()
        # Map IS/IS_NOT to == and !=
        if op_type == "IS":
            left = BinaryOp("==", left, right)
        elif op_type == "IS_NOT":
            left = BinaryOp("!=", left, right)
        else:
            left = BinaryOp(op, left, right)  # Use op value, not op_type

    return left


def parse_comparison(self) -> ASTNode:
    """Parse comparison operators: <, <=, >, >="""
    left = self.parse_shift()

    while self.peek() and self.peek_type() in ("LT", "LTEQ", "GT", "GTEQ"):
        op = self.consume()
        right = self.parse_shift()
        left = BinaryOp(op, left, right)

    return left


def parse_shift(self) -> ASTNode:
    """Parse shift operators: <<, >>, shl, shr, ushr"""
    left = self.parse_range()

    while self.peek() and self.peek_type() in (
        "LSHIFT",
        "RSHIFT",
        "SHL",
        "SHR",
        "USHR",
    ):
        op_type = self.peek_type()
        self.consume()
        right = self.parse_term()
        if op_type in ("LSHIFT", "SHL"):
            left = BinaryOp("shl", left, right)
        elif op_type == "USHR":
            left = BinaryOp("ushr", left, right)
        else:
            left = BinaryOp("shr", left, right)

    return left


def parse_range(self) -> ASTNode:
    """Parse range expressions: 1..10 (inclusive) or 1...10 (exclusive)

    Ruby-style ranges for iteration:
        for i in 1..10 then    // 1,2,3,4,5,6,7,8,9,10
        for i in 1...10 then   // 1,2,3,4,5,6,7,8,9
    """
    left = self.parse_term()

    if self.peek() and self.peek_type() in ("RANGE", "RANGE_EXCL"):
        inclusive = self.peek_type() == "RANGE"
        self.consume()
        right = self.parse_term()
        return Range(left, right, inclusive)

    return left


def parse_term(self) -> ASTNode:
    """Parse +/-"""
    left = self.parse_factor()

    while self.peek() and self.peek_type() in ("PLUS", "MINUS"):
        op = self.consume()
        right = self.parse_factor()
        left = BinaryOp(op, left, right)

    return left


def parse_factor(self) -> ASTNode:
    """Parse *, /, %"""
    left = self.parse_power()

    while self.peek() and self.peek_type() in ("STAR", "SLASH", "MOD"):
        op = self.consume()
        right = self.parse_power()
        left = BinaryOp(op, left, right)

    return left


def parse_power(self) -> ASTNode:
    """Parse ** or ^ (power operator, right-associative)"""
    left = self.parse_unary()

    # Support both ** (Python-style) and ^ (traditional) for power
    if self.peek() and self.peek_type() in ("POWER", "CARET"):
        self.consume()
        right = self.parse_power()  # Right-associative
        return BinaryOp("**", left, right)

    return left


def parse_unary(self) -> ASTNode:
    """Parse unary operators: +, -, not, NOT (bitwise), await"""
    if self.peek() and self.peek_type() in (
        "PLUS",
        "MINUS",
        "NOT",
        "GATE_NOT",
        "TILDE",
    ):
        op_type = self.peek_type()
        op = self.consume()
        operand = self.parse_unary()
        # Map GATE_NOT to "NOT" for codegen
        if op_type == "GATE_NOT":
            return UnaryOp("NOT", operand)
        # Unary + is a no-op, but we still create the node for consistency
        return UnaryOp(op, operand)

    # Handle await expression
    if self.peek() and self.peek_type() == "AWAIT":
        self.consume("AWAIT")
        expr = self.parse_unary()
        return Await(expr)

    return self.parse_primary()


def parse_primary(
    self,
) -> ASTNode:
    """Parse primary expressions"""
    token_type = self.peek_type()
    if not token_type:
        raise SyntaxError("Unexpected end of input")

    # Dispatch to specialized handlers
    if token_type == "COMPTIME":
        # Comptime expression in primary context
        from parser import ast as A

        self.consume("COMPTIME")
        expr = self.parse_expression()
        return A.ComptimeExpr(expr)
    if token_type == "NEW":
        return self.parse_new()
    if token_type in ("REINTERPRET", "BITCAST"):
        return self._parse_reinterpret_cast()
    if token_type == "THIS":
        return self._parse_this_primary()
    if token_type == "LPAREN":
        return self._parse_paren_or_cast()
    if token_type in ("NUMBER", "FLOAT"):
        num = self._parse_number_literal(token_type)
        # Check for method call on number: 5.times |x| ...
        if self.peek_type() == "DOT":
            self.consume("DOT")
            # Accept TIMES keyword or IDENT as method name
            if self.peek_type() == "TIMES":
                method_name = self.consume("TIMES")
            else:
                method_name = self.consume("IDENT")
            # Check for block
            if self.peek_type() == "PIPE":
                block = self._parse_block()
                return BlockCall(num, method_name, [], block)
            # Regular method call on number (rare but possible)
            if self.peek_type() == "LPAREN":
                args, _ = self._parse_arg_list()
                return MethodCall(num, method_name, args)
            return FieldAccess(num, method_name)
        return num
    if token_type in ("TRUE", "FALSE"):
        return self._parse_bool_literal(token_type)
    if token_type in ("NULL", "NULLPTR"):
        self.consume(token_type)
        return Null()
    if token_type == "CHARLIT":
        return self._parse_char_literal()
    if token_type == "STRLIT":
        return StringLit(self.consume("STRLIT"))
    if token_type == "HEREDOC":
        return self._parse_heredoc()
    if token_type == "INTERP_STRLIT":
        return self._parse_interpolated_string()
    if token_type == "LBRACKET":
        return self._parse_array_literal()
    if token_type == "LBRACE":
        return self._parse_dict_literal()
    if token_type in self._CALLABLE_TOKENS:
        return self._parse_callable_primary()

    self.error(f"Unexpected token type: {token_type}")
    raise AssertionError("unreachable")


def _parse_this_primary(self) -> ASTNode:
    """Parse THIS keyword in primary expression context"""
    self.consume("THIS")
    this_expr = ThisExpr()

    if not (self.peek() and self.peek_type() == "DOT"):
        return this_expr

    self.consume("DOT")
    field_name = self._consume_field_name()

    if self.peek() and self.peek_type() == "LPAREN":
        args, _ = self._parse_arg_list()
        return MethodCall(this_expr, field_name, args)

    return FieldAccess(this_expr, field_name)


def _parse_paren_or_cast(self) -> ASTNode:
    """Parse parenthesized expression or type cast"""
    # Check if it's a cast: (type)expr
    next_token = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
    cast_types = (
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
        "FLOAT_T",
        "DOUBLE",
        "QUAD",
        "BOOL",
    )

    if next_token and next_token[0] in cast_types:
        self.consume("LPAREN")
        target_type = self.consume()
        self.consume("RPAREN")
        return Cast(target_type, self.parse_unary())

    # Regular parenthesized expression or tuple literal
    self.consume("LPAREN")
    expr = self.parse_expression()

    # Check if this is a tuple: (a, b, c)
    if self.peek_type() == "COMMA":
        from parser import ast as A

        elements = [expr]
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            if self.peek_type() == "RPAREN":
                break  # Trailing comma allowed: (a, b,)
            elements.append(self.parse_expression())
        self.consume("RPAREN")
        return A.TupleLit(elements)

    self.consume("RPAREN")
    return expr


def _parse_number_literal(self, token_type: str) -> Number:
    """Parse NUMBER or FLOAT literal"""
    value = self.consume(token_type)
    if token_type == "FLOAT":
        return Number(value, is_long=False, is_float=True)
    is_long = value.endswith("L") or value.endswith("l")
    return Number(value, is_long=is_long, is_float=False)


def _parse_bool_literal(self, token_type: str) -> Bool:
    """Parse TRUE or FALSE literal"""
    self.consume(token_type)
    return Bool(token_type == "TRUE")


def _parse_char_literal(self) -> Number:
    """Parse character literal: 'a' -> Number(97)

    Character literals are converted to their ASCII/Unicode integer values.
    Supports escape sequences: '\\n' -> 10, '\\t' -> 9, '\\x41' -> 65
    """
    raw = self.consume("CHARLIT")
    value = char_literal_to_int(raw)
    # Number expects a string value, convert int to string
    return Number(str(value), is_long=False, is_float=False)


def _parse_heredoc(self) -> StringLit:
    """Parse multi-line string (heredoc): \"\"\"...\"\"\"

    Strips the triple quotes and preserves internal newlines.
    """
    raw = self.consume("HEREDOC")
    # Remove surrounding triple quotes
    content = raw[3:-3]
    # Create a StringLit but bypass the normal quote-stripping
    node = StringLit.__new__(StringLit)
    node.value = content
    return node


def _parse_interpolated_string(self) -> InterpolatedString:
    """Parse interpolated string: "Hello #{name}, count: #{count}"

    Splits the string into parts:
      - literal text portions (str)
      - expressions inside #{...} (parsed as AST nodes)
    """
    raw = self.consume("INTERP_STRLIT")
    # Remove surrounding quotes
    content = raw[1:-1]

    parts: list[str | ASTNode] = []
    pos = 0

    while pos < len(content):
        # Find next #{
        interp_start = content.find("#{", pos)
        if interp_start == -1:
            # No more interpolations, add remaining text
            remaining = content[pos:]
            if remaining:
                # Handle escape sequences
                remaining = remaining.replace("\\n", "\n").replace("\\t", "\t")
                parts.append(remaining)
            break

        # Add text before #{
        if interp_start > pos:
            text = content[pos:interp_start]
            text = text.replace("\\n", "\n").replace("\\t", "\t")
            parts.append(text)

        # Find matching }
        brace_start = interp_start + 2
        brace_end = content.find("}", brace_start)
        if brace_end == -1:
            self.error("Unclosed interpolation in string")

        # Extract and parse the expression
        expr_str = content[brace_start:brace_end]
        from lexer.scan import tokenize

        from .parser import Parser as _InlineParser

        expr_tokens = tokenize(expr_str)
        expr_parser = _InlineParser(expr_tokens)
        expr = expr_parser.parse_expression()
        parts.append(expr)

        pos = brace_end + 1

    return InterpolatedString(parts)


def _parse_array_literal(self) -> ArrayLit | ListComprehension:
    """Parse array literal: [1, 2, 3] or list comprehension: [x*x for x in 1..10]"""
    self.consume("LBRACKET")

    if self.peek_type() == "RBRACKET":
        self.consume("RBRACKET")
        return ArrayLit([])

    # Parse first expression
    first_expr = self.parse_expression()

    # Check if this is a list comprehension: [expr for var in iterable]
    if self.peek_type() == "FOR":
        self.consume("FOR")
        var_name = self.consume("IDENT")
        self.consume("IN")
        iterable = self.parse_expression()

        # Optional condition: [expr for var in iterable if cond]
        condition = None
        if self.peek_type() == "IF":
            self.consume("IF")
            condition = self.parse_expression()

        self.consume("RBRACKET")
        return ListComprehension(first_expr, var_name, iterable, condition)

    # Regular array literal
    elements: list[ASTNode] = [first_expr]
    while self.peek_type() == "COMMA":
        self.consume("COMMA")
        elements.append(self.parse_expression())
    self.consume("RBRACKET")
    return ArrayLit(elements)


def _parse_dict_literal(self) -> DictLit:
    """Parse dict literal: {key1: val1, key2: val2}"""
    self.consume("LBRACE")
    pairs: list[tuple[ASTNode, ASTNode]] = []
    if self.peek_type() != "RBRACE":
        pairs.append(self._parse_dict_entry())
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            if self.peek_type() == "RBRACE":
                break  # Allow trailing comma
            pairs.append(self._parse_dict_entry())
    self.consume("RBRACE")
    return DictLit(pairs)


def _parse_dict_entry(self) -> tuple[ASTNode, ASTNode]:
    """Parse single dict entry: key: value"""
    key = self.parse_expression()
    self.consume("COLON")
    val = self.parse_expression()
    return (key, val)


def _parse_arg_list(self) -> tuple[list[ASTNode], bool]:
    """Parse function argument list: (arg1, arg2, ..., unsafe?)

    Returns tuple of (args, is_unsafe) where is_unsafe is True if
    the 'unsafe' keyword appears as the last argument.
    """
    self.consume("LPAREN")
    args: list[ASTNode] = []
    is_unsafe = False
    if self.peek_type() != "RPAREN":
        # Check if first token is 'unsafe' alone
        if self.peek_type() == "UNSAFE":
            self.consume("UNSAFE")
            is_unsafe = True
        else:
            args.append(self.parse_expression())
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            # Check if 'unsafe' keyword (not an expression)
            if self.peek_type() == "UNSAFE":
                self.consume("UNSAFE")
                is_unsafe = True
                break  # unsafe must be last
            args.append(self.parse_expression())
    self.consume("RPAREN")
    return args, is_unsafe


def _parse_arg_list_simple(self) -> list[ASTNode]:
    """Parse function argument list without unsafe support (for internal use)."""
    args, _ = self._parse_arg_list()
    return args


def _parse_postfix_ops(self, expr: ASTNode) -> ASTNode:
    """Parse chained postfix operations: .field, [index], (args).

    Handles patterns like: obj.field[index], arr[i].field, func().field[0]
    """
    result: ASTNode = expr
    while self.peek():
        token_type = self.peek_type()

        # Array subscript: expr[index]
        if token_type == "LBRACKET":
            self.consume("LBRACKET")
            index_expr = self.parse_expression()
            is_unsafe = False
            if self.peek_type() == "COMMA":
                self.consume("COMMA")
                if self.peek_type() == "UNSAFE":
                    self.consume("UNSAFE")
                    is_unsafe = True
            # Check for slice: expr[start:end]
            if self.peek_type() == "COLON":
                self.consume("COLON")
                if self.peek_type() == "RBRACKET":
                    self.consume("RBRACKET")
                    result = StringSlice(result, index_expr, None)
                    continue
                end_expr = self.parse_expression()
                self.consume("RBRACKET")
                result = StringSlice(result, index_expr, end_expr)
                continue
            self.consume("RBRACKET")
            result = ArrayAccess(result, index_expr, unsafe=is_unsafe)
            continue

        # Field access: expr.field
        if token_type == "DOT":
            self.consume("DOT")
            # Tuple index: expr.0
            if self.peek_type() == "NUMBER":
                from parser import ast as A

                index_str = self.consume("NUMBER")
                index = int(index_str)
                result = A.TupleAccess(result, index)
                continue
            field_name = self._consume_field_name()
            # Method call: expr.method(args)
            if self.peek() and self.peek_type() == "LPAREN":
                args, _ = self._parse_arg_list()
                result = MethodCall(result, field_name, args)
                continue
            result = FieldAccess(result, field_name)
            continue

        # Safe field access: expr?.field
        if token_type == "SAFE_DOT":
            self.consume("SAFE_DOT")
            field_name = self._consume_field_name()
            result = SafeFieldAccess(result, field_name)
            continue

        # No more postfix operations
        break

    return result

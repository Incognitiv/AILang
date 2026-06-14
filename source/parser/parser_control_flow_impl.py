"""Parser helper implementations extracted from parser.py."""

from __future__ import annotations

from typing import Optional

from token_access import token_line_at, token_type_at

from .ast import (
    ASTNode,
    BinaryOp,
    DoWhile,
    For,
    Foreach,
    If,
    Loop,
    Repeat,
    Spawn,
    UnaryOp,
    While,
)


def _is_elsif_token(self) -> bool:
    """Check if current token is elsif or an alias (otherwise, else if)"""
    tok_type = self.peek_type()
    if tok_type in ("ELSIF", "OTHERWISE"):
        return True
    # Check for 'else if' (two tokens ON THE SAME LINE)
    # Only treat as elsif if both tokens are on the same source line;
    # otherwise the user wrote a standalone 'else' block containing 'if'.
    if tok_type == "ELSE" and self._peek_next_type() == "IF":
        else_line = (
            token_line_at(self.tokens, self.pos) if self.pos < len(self.tokens) else -1
        )
        if_line = (
            token_line_at(self.tokens, self.pos + 1)
            if self.pos + 1 < len(self.tokens)
            else -2
        )
        return else_line == if_line
    return False


def _consume_elsif(self) -> None:
    """Consume elsif or its alias (otherwise, else if)"""
    tok_type = self.peek_type()
    if tok_type == "ELSIF":
        self.consume("ELSIF")
    elif tok_type == "OTHERWISE":
        self.consume("OTHERWISE")
    elif tok_type == "ELSE":
        # else if - consume both tokens
        self.consume("ELSE")
        self.consume("IF")
    else:
        self.error(f"Expected elsif, otherwise, or else if, got {tok_type}")


def _peek_next_type(self) -> str | None:
    """Peek at the token after the current one"""
    if self.pos + 1 < len(self.tokens):
        return token_type_at(self.tokens, self.pos + 1)
    return None


def _parse_statements_until(self, *terminators: str) -> list[ASTNode]:
    """Parse statements until one of the given block terminators."""
    body: list[ASTNode] = []
    while self._not_block_end(*terminators):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()
    return body


def _parse_foreach_header(self) -> tuple[str, ASTNode]:
    self.consume("FOREACH")
    var_name = self.consume("IDENT")
    self.consume("IN")
    iterable = self.parse_expression()
    self.consume("THEN")
    self.skip_newlines()
    return var_name, iterable


def parse_if(self) -> If:
    """Parse: if cond then ... elsif cond then ... else ... end

    Also supports: otherwise (British alias), else if (two words)
    """
    self.consume("IF")
    cond = self.parse_expression()
    self.consume("THEN")
    self.skip_newlines()

    then_body = []
    while not self._is_elsif_token() and self.peek_type() not in ("ELSE", "END"):
        stmt = self.parse_statement()
        if stmt:
            then_body.append(stmt)
        self.skip_newlines()

    # Build else_body which may contain nested if statements for elsif
    else_body = []

    # Keep processing elsif clauses (including 'otherwise' and 'else if')
    if self._is_elsif_token():
        # Recursively parse elsif as a nested if statement
        nested_if = self.parse_if_continuation()
        else_body = [nested_if]
    elif self.peek_type() == "ELSE":
        self.consume("ELSE")
        self.skip_newlines()
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                else_body.append(stmt)
            self.skip_newlines()

    self.consume("END")
    return If(cond, then_body, else_body)


def parse_if_continuation(self) -> ASTNode:
    """Parse elsif/else continuation - returns an If node

    Handles: elsif, otherwise, else if
    """
    self._consume_elsif()
    cond = self.parse_expression()
    self.consume("THEN")
    self.skip_newlines()

    then_body = []
    while not self._is_elsif_token() and self.peek_type() not in ("ELSE", "END"):
        stmt = self.parse_statement()
        if stmt:
            then_body.append(stmt)
        self.skip_newlines()

    # Recursively handle more elsifs or else
    else_body = []
    if self._is_elsif_token():
        nested_if = self.parse_if_continuation()
        else_body = [nested_if]
    elif self.peek_type() == "ELSE":
        self.consume("ELSE")
        self.skip_newlines()
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                else_body.append(stmt)
            self.skip_newlines()

    return If(cond, then_body, else_body)


# ========================================================================
# Bounded Loop Support
# ========================================================================


def _parse_bounded_loop(self) -> ASTNode:
    """Parse @bounded(N) decorator followed by a loop.

    Syntax:
        @bounded(100)
        while x < 10 then ... end

        @bounded(1000)
        for (i = 0; i < n; i = i + 1) then ... end
    """
    self.consume("BOUNDED")
    self.consume("LPAREN")
    max_iterations = self.parse_expression()
    self.consume("RPAREN")
    self.skip_newlines()

    # Now parse the loop
    loop_type = self.peek_type()
    if loop_type == "WHILE":
        return self._parse_while_with_bound(max_iterations)
    if loop_type == "UNTIL":
        return self._parse_until_with_bound(max_iterations)
    if loop_type == "FOR":
        return self._parse_for_with_bound(max_iterations)
    if loop_type == "LOOP":
        return self._parse_loop_with_bound(max_iterations)
    if loop_type == "FOREACH":
        return self._parse_foreach_with_bound(max_iterations)
    self.error(f"@bounded must be followed by a loop, got {loop_type}")
    raise AssertionError("unreachable")


def _parse_auto_bound_loop(self) -> ASTNode:
    """Parse @bound decorator that infers bound from condition.

    Syntax:
        @bound
        while x < 10 then ... end  // infers max_iterations = 10
    """
    self.consume("BOUND")
    self.skip_newlines()

    loop_type = self.peek_type()
    if loop_type not in ("WHILE", "UNTIL", "FOR"):
        self.error(
            f"@bound can only be used with while/until/for loops, got {loop_type}"
        )

    # Parse the loop and try to extract bound from condition
    if loop_type == "WHILE":
        self.consume("WHILE")
        cond = self.parse_expression()
        bound = self._extract_bound_from_condition(cond)
        return self._finish_while_parse(cond, bound)
    if loop_type == "UNTIL":
        self.consume("UNTIL")
        cond = self.parse_expression()
        bound = self._extract_bound_from_condition(cond)
        negated_cond = UnaryOp("not", cond)
        return self._finish_while_parse(negated_cond, bound)
    if loop_type == "FOR":
        return self._parse_for_with_auto_bound()

    self.error("Unexpected token after @bound")
    raise AssertionError("unreachable")


def _extract_bound_from_condition(self, cond: ASTNode) -> Optional[ASTNode]:
    """Try to extract a numeric bound from a comparison condition.

    Examples:
        x < 10   -> 10
        i <= 100 -> 100
        n > 0    -> None (can't infer upper bound)
    """
    if isinstance(cond, BinaryOp):
        # For x < N or x <= N, the bound is N
        if cond.op in ("<", "<=", "LT", "LTEQ"):
            return cond.right
        # For N > x or N >= x, the bound is N
        if cond.op in (">", ">=", "GT", "GTEQ"):
            return cond.left
    return None


def _is_max_keyword(self) -> bool:
    """Check if current token is 'max' used as keyword (not identifier)."""
    return self.peek_type() == "IDENT" and self.peek_text() == "max"


def _parse_inline_max(self) -> Optional[ASTNode]:
    """Parse optional inline max: N or max: infinity clause.

    Used after condition in loops: while x < 10 max: 100 then
    Returns None if no max clause found.
    """
    if not self._is_max_keyword():
        return None

    self.consume("IDENT")  # consume 'max'
    self.consume("COLON")
    if self.peek_type() == "INFINITY":
        self.consume("INFINITY")
        return None  # infinity means unbounded
    return self.parse_expression()


def _finish_while_parse(
    self, cond: ASTNode, max_iterations: Optional[ASTNode]
) -> While:
    """Finish parsing a while loop after condition is parsed."""
    self.consume("THEN")
    # Check for inline max: N
    inline_max = self._parse_inline_max()
    if inline_max is not None:
        max_iterations = inline_max
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return While(cond, body, max_iterations)


def _parse_while_with_bound(self, max_iterations: ASTNode) -> While:
    """Parse while loop with explicit bound from @bounded(N)."""
    self.consume("WHILE")
    cond = self.parse_expression()
    return self._finish_while_parse(cond, max_iterations)


def _parse_until_with_bound(self, max_iterations: ASTNode) -> While:
    """Parse until loop with explicit bound from @bounded(N)."""
    self.consume("UNTIL")
    cond = self.parse_expression()
    negated_cond = UnaryOp("not", cond)
    return self._finish_while_parse(negated_cond, max_iterations)


def _parse_for_with_bound(self, max_iterations: ASTNode) -> For | Foreach:
    """Parse for loop with explicit bound from @bounded(N)."""
    return self._parse_for_internal(max_iterations)


def _parse_for_with_auto_bound(self) -> For | Foreach:
    """Parse for loop with @bound, inferring from condition."""
    return self._parse_for_internal(None, auto_bound=True)


def _parse_for_internal(
    self, max_iterations: Optional[ASTNode] = None, auto_bound: bool = False
) -> For | Foreach:
    """Internal helper to parse for loop with optional bound.

    Args:
        max_iterations: Explicit bound from @bounded(N)
        auto_bound: If True, try to infer bound from condition
    """
    self.consume("FOR")

    # Check if this is "for i in ..." (range-style) or "for (...)" (C-style)
    if self.peek_type() == "IDENT":
        pos = self.pos
        var_name = self.consume()
        if self.peek_type() == "IN":
            # Range-style: for i in expr then
            self.consume("IN")
            iterable = self.parse_expression()
            self.consume("THEN")
            self.skip_newlines()

            body: list[ASTNode] = []
            while self._not_block_end("END"):
                stmt = self.parse_statement()
                if stmt:
                    body.append(stmt)
                self.skip_newlines()

            self.consume("END")
            return Foreach(var_name, iterable, body, max_iterations)
        # Backtrack
        self.pos = pos

    # C-style: for (init; cond; step) then
    self.consume("LPAREN")
    init = self.parse_statement()
    self.consume("SEMICOLON")
    cond = self.parse_expression()
    self.consume("SEMICOLON")
    step = self.parse_statement()
    self.consume("RPAREN")

    # Auto-infer bound from condition if @bound was used
    if auto_bound and max_iterations is None:
        max_iterations = self._extract_bound_from_condition(cond)

    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return For(init, cond, step, body, max_iterations)


def _parse_loop_with_bound(self, max_iterations: ASTNode) -> Loop:
    """Parse loop with explicit bound from @bounded(N)."""
    self.consume("LOOP")
    # Check for inline max: N after LOOP keyword
    if self._is_max_keyword():
        inline_max = self._parse_inline_max()
        if inline_max is not None:
            max_iterations = inline_max
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return Loop(body, max_iterations)


def _parse_foreach_with_bound(self, max_iterations: ASTNode) -> Foreach:
    """Parse foreach with explicit bound from @bounded(N)."""
    var_name, iterable = self._parse_foreach_header()
    body = self._parse_statements_until("END")
    self.consume("END")
    return Foreach(var_name, iterable, body, max_iterations)


def parse_while(self) -> While:
    """Parse: while cond [max: N] then ... end

    Optional bound can be specified inline:
        while x < 10 max: 100 then ... end
    """
    self.consume("WHILE")
    cond = self.parse_expression()
    # Check for inline max: N before THEN
    max_iterations = None
    if self._is_max_keyword():
        max_iterations = self._parse_inline_max()
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return While(cond, body, max_iterations)


def parse_do_while(self) -> DoWhile:
    """Parse: do then ... end while condition

    This generates LLVM-optimal loop structure (rotated form).
    The body executes at least once, then condition is checked.

    Example:
        do then
            x = x + 1
        end while x < 10
    """
    self.consume("DO")
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    self.consume("WHILE")
    cond = self.parse_expression()
    return DoWhile(body, cond)


def parse_unless(self) -> If:
    """Parse: unless cond then ... else ... end (Ruby-style negative if)

    Transforms: unless cond then A else B end
    Into:       if not cond then A else B end
    """
    self.consume("UNLESS")
    cond = self.parse_expression()
    self.consume("THEN")
    self.skip_newlines()

    then_body = []
    while self._not_block_end("ELSE", "END"):
        stmt = self.parse_statement()
        if stmt:
            then_body.append(stmt)
        self.skip_newlines()

    else_body = []
    if self.peek_type() == "ELSE":
        self.consume("ELSE")
        self.skip_newlines()
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                else_body.append(stmt)
            self.skip_newlines()

    self.consume("END")
    # Negate the condition: unless X -> if not X
    negated_cond = UnaryOp("not", cond)
    return If(negated_cond, then_body, else_body)


def parse_until(self) -> While:
    """Parse: until cond [max: N] then ... end (Ruby-style negative while)

    Transforms: until cond then ... end
    Into:       while not cond then ... end
    """
    self.consume("UNTIL")
    cond = self.parse_expression()
    # Check for inline max: N before THEN
    max_iterations = None
    if self._is_max_keyword():
        max_iterations = self._parse_inline_max()
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    # Negate the condition: until X -> while not X
    negated_cond = UnaryOp("not", cond)
    return While(negated_cond, body, max_iterations)


def parse_for(self) -> For | Foreach:
    """Parse for loops in two forms:

    C-style:    for (init; cond; step) then ... end
    Range-style: for i in 1..10 then ... end
    """
    self.consume("FOR")

    # Check if this is "for i in ..." (range-style) or "for (...)" (C-style)
    if self.peek_type() == "IDENT":
        # Lookahead: if next is IN, it's range-style
        pos = self.pos
        var_name = self.consume()  # consume IDENT
        if self.peek_type() == "IN":
            # Range-style: for i in expr then
            self.consume("IN")
            iterable = self.parse_expression()
            self.consume("THEN")
            self.skip_newlines()

            body = []
            while self._not_block_end("END"):
                stmt = self.parse_statement()
                if stmt:
                    body.append(stmt)
                self.skip_newlines()

            self.consume("END")
            return Foreach(var_name, iterable, body)
        # Not "for i in", backtrack and parse C-style
        self.pos = pos

    # C-style: for (init; cond; step) then
    self.consume("LPAREN")
    init = self.parse_statement()  # init statement
    self.consume("SEMICOLON")
    cond = self.parse_expression()  # condition
    self.consume("SEMICOLON")
    step = self.parse_statement()  # step statement
    self.consume("RPAREN")
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return For(init, cond, step, body)


def parse_loop(self) -> Loop:
    """Parse: loop [max: N] then ... end

    Optional bound can be specified inline:
        loop max: 1000 then ... end
        loop max: infinity then ... end  (same as unbounded)
    """
    self.consume("LOOP")
    # Check for inline max: N before THEN
    max_iterations = None
    if self._is_max_keyword():
        max_iterations = self._parse_inline_max()
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return Loop(body, max_iterations)


def parse_foreach(self) -> Foreach:
    """Parse: foreach x in xs then ... end"""
    var_name, iterable = self._parse_foreach_header()
    body = self._parse_statements_until("END")
    self.consume("END")
    return Foreach(var_name, iterable, body)


def parse_repeat(self) -> Repeat:
    """Parse: repeat N times then ... end"""
    self.consume("REPEAT")
    count = self.parse_expression()
    self.consume("TIMES")
    self.consume("THEN")
    self.skip_newlines()

    body = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()

    self.consume("END")
    return Repeat(count, body)


def parse_spawn(self) -> Spawn:
    """Parse: spawn function_call

    Spawns a new thread to execute the function call.
    Returns a thread handle that can be joined later.

    Example:
        handle = spawn compute_heavy(1000)
        result = join(handle)
    """
    self.consume("SPAWN")
    # Parse the function call expression
    func_call = self.parse_expression()
    return Spawn(func_call)

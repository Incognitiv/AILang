"""Range statement parsing helpers."""

from __future__ import annotations

from token_access import token_type_at

from .ast import ASTNode, BinaryOp, RangeType, RangeVarDecl, UnaryOp


def _lookahead_is_range_decl(self, start_pos: int) -> bool:
    """Check if tokens after := look like a range declaration."""
    pos = start_pos
    if pos < len(self.tokens) and token_type_at(self.tokens, pos) == "MINUS":
        pos += 1
    if pos >= len(self.tokens):
        return False
    if token_type_at(self.tokens, pos) not in ("NUMBER", "IDENT"):
        return False
    pos += 1
    if pos >= len(self.tokens):
        return False
    return token_type_at(self.tokens, pos) in ("RANGE", "RANGE_EXCL")


def _parse_range_var_decl(self) -> RangeVarDecl:
    """Parse Ada-style range variable: x := 0..100 or x := 0..100 = 50."""
    var_name = self.consume("IDENT")
    self.consume("COLON_ASSIGN")

    low = self._parse_range_bound()
    exclusive = False
    if self.peek_type() == "RANGE_EXCL":
        self.consume("RANGE_EXCL")
        exclusive = True
    elif self.peek_type() == "RANGE":
        self.consume("RANGE")
    else:
        self.error(f"Expected '..' or '...' in range declaration for {var_name}")
    high = self._parse_range_bound()

    init_value = None
    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        init_value = self.parse_expression()

    return RangeVarDecl(var_name, RangeType(low, high, exclusive), init_value)


def _parse_range_bound(self) -> ASTNode:
    """Parse a range bound without consuming range operators."""
    if self.peek_type() == "MINUS":
        self.consume("MINUS")
        return UnaryOp("-", self._parse_range_bound())

    left = self.parse_primary()
    op_map = {"PLUS": "+", "MINUS": "-", "STAR": "*", "SLASH": "/", "MOD": "%"}
    while True:
        op_type = self.peek_type()
        if op_type not in op_map:
            break
        self.consume(op_type)
        left = BinaryOp(op_map[op_type], left, self.parse_primary())

    return left

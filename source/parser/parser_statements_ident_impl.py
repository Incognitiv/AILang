"""Identifier-led statement parsing extracted from parser_statements_impl."""

from __future__ import annotations

from token_access import token_type_at

from .ast import Assign, ASTNode, BinaryOp, Number, Variable


def _parse_keyword_as_ident_stmt(self) -> ASTNode:
    """Parse a keyword token (like TEST) used as a variable name in assignment."""
    var_name = self.consume()  # consume() returns the text directly
    self.consume("ASSIGN")
    value = self.parse_expression()
    return Assign(var_name, value)


def _parse_ident_stmt(self) -> ASTNode:
    """Parse identifier-based statement (assignment, field, method, expr)."""
    next_pos = self.pos + 1

    if next_pos < len(self.tokens) and token_type_at(self.tokens, next_pos) == "COMMA":
        return self._parse_tuple_assign()

    if next_pos < len(self.tokens) and token_type_at(self.tokens, next_pos) == "DOT":
        return self._parse_dot_access_stmt()

    if (
        next_pos < len(self.tokens)
        and token_type_at(self.tokens, next_pos) == "LBRACKET"
    ):
        return self._parse_subscript_stmt()

    if (
        next_pos < len(self.tokens)
        and token_type_at(self.tokens, next_pos) == "COLON_ASSIGN"
    ):
        if self._lookahead_is_range_decl(next_pos + 1):
            return self._parse_range_var_decl()
        var_name = self.consume("IDENT")
        self.consume("COLON_ASSIGN")
        value = self.parse_expression()
        return Assign(var_name, value)

    if next_pos < len(self.tokens) and token_type_at(self.tokens, next_pos) == "ASSIGN":
        var_name = self.consume("IDENT")
        self.consume("ASSIGN")
        value = self.parse_expression()
        return Assign(var_name, value)

    if next_pos < len(self.tokens):
        tok2 = token_type_at(self.tokens, next_pos)
        compound_op_for_token = {
            "PLUSEQ": "+",
            "MINUSEQ": "-",
            "STAREQ": "*",
            "SLASHEQ": "/",
            "MODEQ": "%",
        }
        if tok2 == "PLUSPLUS":
            var_name = self.consume("IDENT")
            self.consume("PLUSPLUS")
            one = Number("1", is_long=False, is_float=False)
            return Assign(var_name, BinaryOp("+", Variable(var_name), one))
        if tok2 == "MINUSMINUS":
            var_name = self.consume("IDENT")
            self.consume("MINUSMINUS")
            one = Number("1", is_long=False, is_float=False)
            return Assign(var_name, BinaryOp("-", Variable(var_name), one))
        if tok2 in compound_op_for_token:
            var_name = self.consume("IDENT")
            self.consume(tok2)
            rhs = self.parse_expression()
            op = compound_op_for_token[tok2]
            return Assign(var_name, BinaryOp(op, Variable(var_name), rhs))

    return self.parse_expression()

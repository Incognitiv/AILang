"""Object/instance statement helpers extracted from parser_statements_impl."""

from __future__ import annotations

from .ast import ASTNode, FieldAccess, FieldAssign, MethodCall, NewExpr, ThisExpr


def parse_new(self) -> ASTNode:
    """Parse: new TypeName(arg1, arg2, ...)."""
    self.consume("NEW")
    type_name = self.consume("IDENT")
    self.consume("LPAREN")

    args = []
    if self.peek() and self.peek_type() != "RPAREN":
        args.append(self.parse_expression())
        while self.peek() and self.peek_type() == "COMMA":
            self.consume("COMMA")
            args.append(self.parse_expression())

    self.consume("RPAREN")
    return NewExpr(type_name, args)


def _parse_this_stmt(self) -> ASTNode:
    """Parse this-based statement."""
    self.consume("THIS")
    if self.peek_type() != "DOT":
        return ThisExpr()

    self.consume("DOT")
    field_name = self._consume_field_name()

    if self.peek_type() == "ASSIGN":
        self.consume("ASSIGN")
        value = self.parse_expression()
        return FieldAssign(ThisExpr(), field_name, value)

    if self.peek_type() == "LPAREN":
        self.consume("LPAREN")
        this_args: list[ASTNode] = []
        if self.peek_type() != "RPAREN":
            this_args.append(self.parse_expression())
            while self.peek_type() == "COMMA":
                self.consume("COMMA")
                this_args.append(self.parse_expression())
        self.consume("RPAREN")
        return MethodCall(ThisExpr(), field_name, this_args)

    return FieldAccess(ThisExpr(), field_name)

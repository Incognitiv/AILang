"""Callable/identifier expression parsing extracted from parser_expression_impl."""

from __future__ import annotations

from .ast import (
    ArrayAccess,
    ASTNode,
    AtomicOp,
    Call,
    ChannelClose,
    ChannelCreate,
    ChannelRecv,
    ChannelSend,
    ChannelTryRecv,
    ChannelTrySend,
    FieldAccess,
    Join,
    MethodCall,
    SafeFieldAccess,
    Spawn,
    StringSlice,
    Variable,
)


def _parse_callable_primary(self) -> ASTNode:
    """Parse identifier that could be variable, call, array access, or field access."""
    token_type = self.peek_type()

    if token_type == "SPAWN":
        self.consume("SPAWN")
        func_call = self.parse_expression()
        return Spawn(func_call)

    if token_type == "JOIN":
        self.consume("JOIN")
        self.consume("LPAREN")
        handle = self.parse_expression()
        self.consume("RPAREN")
        return Join(handle)

    if token_type == "ATOMIC":
        self.consume("ATOMIC")
        if self.peek_type() == "UNDERSCORE":
            self.consume("UNDERSCORE")
        if self.peek_type() == "IDENT":
            op_name = self.consume("IDENT")
            self.consume("LPAREN")
            ptr = self.parse_expression()
            value = None
            expected = None
            if self.peek_type() == "COMMA":
                self.consume("COMMA")
                value = self.parse_expression()
            if self.peek_type() == "COMMA":
                self.consume("COMMA")
                expected = value
                value = self.parse_expression()
            self.consume("RPAREN")
            return AtomicOp(op_name, ptr, value, expected)

    if token_type == "CHANNEL":
        self.consume("CHANNEL")
        self.consume("LPAREN")
        type_token = self.peek_type()
        if type_token in ("INT", "LONG", "FLOAT_T", "DOUBLE", "BOOL", "IDENT"):
            elem_type = self.consume()
        else:
            elem_type = self.consume("IDENT")
        self.consume("COMMA")
        capacity = self.parse_expression()
        self.consume("RPAREN")
        return ChannelCreate(elem_type, capacity)

    if token_type == "CHAN_SEND":
        self.consume("CHAN_SEND")
        self.consume("LPAREN")
        ch = self.parse_expression()
        self.consume("COMMA")
        value = self.parse_expression()
        self.consume("RPAREN")
        return ChannelSend(ch, value)

    if token_type == "CHAN_RECV":
        self.consume("CHAN_RECV")
        self.consume("LPAREN")
        ch = self.parse_expression()
        self.consume("RPAREN")
        return ChannelRecv(ch)

    if token_type == "CHAN_TRY_SEND":
        self.consume("CHAN_TRY_SEND")
        self.consume("LPAREN")
        ch = self.parse_expression()
        self.consume("COMMA")
        value = self.parse_expression()
        self.consume("RPAREN")
        return ChannelTrySend(ch, value)

    if token_type == "CHAN_TRY_RECV":
        self.consume("CHAN_TRY_RECV")
        self.consume("LPAREN")
        ch = self.parse_expression()
        self.consume("RPAREN")
        return ChannelTryRecv(ch)

    if token_type == "CHAN_CLOSE":
        self.consume("CHAN_CLOSE")
        self.consume("LPAREN")
        ch = self.parse_expression()
        self.consume("RPAREN")
        return ChannelClose(ch)

    name = self.consume()

    if self.peek() and self.peek_type() == "LPAREN":
        args, is_unsafe = self._parse_arg_list()
        call_result: ASTNode = Call(name, args, unsafe=is_unsafe)
        return self._parse_postfix_ops(call_result)

    if self.peek() and self.peek_type() == "LBRACKET":
        if self._is_generic_call():
            return self._parse_generic_call(name)
        self.consume("LBRACKET")
        start_expr = self.parse_expression()
        is_unsafe = False
        if self.peek_type() == "COMMA":
            self.consume("COMMA")
            if self.peek_type() == "UNSAFE":
                self.consume("UNSAFE")
                is_unsafe = True
        if self.peek_type() == "COLON":
            self.consume("COLON")
            if self.peek_type() == "RBRACKET":
                self.consume("RBRACKET")
                slice_result: ASTNode = StringSlice(Variable(name), start_expr, None)
                return self._parse_postfix_ops(slice_result)
            end_expr = self.parse_expression()
            self.consume("RBRACKET")
            slice_result2: ASTNode = StringSlice(Variable(name), start_expr, end_expr)
            return self._parse_postfix_ops(slice_result2)
        self.consume("RBRACKET")
        arr_result: ASTNode = ArrayAccess(Variable(name), start_expr, unsafe=is_unsafe)
        return self._parse_postfix_ops(arr_result)

    if self.peek() and self.peek_type() == "SAFE_DOT":
        self.consume("SAFE_DOT")
        field_name = self._consume_field_name()
        safe_result: ASTNode = SafeFieldAccess(Variable(name), field_name)
        return self._parse_postfix_ops(safe_result)

    if self.peek() and self.peek_type() == "DOT":
        self.consume("DOT")
        if self.peek_type() == "NUMBER":
            from parser import ast as A

            index_str = self.consume("NUMBER")
            index = int(index_str)
            tuple_result: ASTNode = A.TupleAccess(Variable(name), index)
            return self._parse_postfix_ops(tuple_result)
        field_name = self._consume_field_name()
        if self.peek() and self.peek_type() == "LPAREN":
            args, _ = self._parse_arg_list()
            method_result: ASTNode = MethodCall(Variable(name), field_name, args)
            return self._parse_postfix_ops(method_result)
        field_result: ASTNode = FieldAccess(Variable(name), field_name)
        return self._parse_postfix_ops(field_result)

    return Variable(name)

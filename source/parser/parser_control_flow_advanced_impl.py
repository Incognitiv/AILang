"""Advanced control-flow parser helpers extracted from parser_control_flow_impl."""

from __future__ import annotations

from typing import Optional

from token_access import token_type_at

from .ast import ASTNode, InlineAsm, Match, MatchPattern, Throw, TryExcept


def _parse_match_pattern(self) -> ASTNode:
    """Parse a match pattern."""
    if self.peek_type() == "UNDERSCORE":
        self.consume("UNDERSCORE")
        return MatchPattern("wildcard", "_", [])

    return self.parse_expression()


def _is_match_default(self) -> bool:
    """Return true for match fallback syntax.

    `default:` is contextual here so `default` remains usable as a normal
    identifier outside match blocks. `else:` remains accepted for compatibility.
    """
    return self.peek_type() == "ELSE" or (
        self.peek_type() == "IDENT" and self.peek_text() == "default"
    )


def _consume_match_default(self) -> None:
    if self.peek_type() == "ELSE":
        self.consume("ELSE")
        return
    if self.peek_type() == "IDENT" and self.peek_text() == "default":
        self.consume("IDENT")
        return
    self.error(f"Expected 'default' or 'else', got {self.peek_type()}")


def parse_match(self) -> Match:
    """Parse: match expr then case pattern: ... [default: ...] end."""
    self.consume("MATCH")
    expr = self.parse_expression()
    self.consume("THEN")
    self.skip_newlines()

    cases = []
    default_case: list[ASTNode] = []

    while self.peek() and not self._is_match_default() and self.peek_type() != "END":
        if self.peek_type() == "CASE":
            self.consume("CASE")
            pattern = self._parse_match_pattern()
            self.consume("COLON")
            self.skip_newlines()

            case_body = []
            while (
                self.peek()
                and self.peek_type() not in ("CASE", "END")
                and not self._is_match_default()
            ):
                stmt = self.parse_statement()
                if stmt:
                    case_body.append(stmt)
                self.skip_newlines()

            cases.append((pattern, case_body))
        else:
            self.error(f"Expected 'case', got {self.peek_type()}")
            raise AssertionError("unreachable")

    if self._is_match_default():
        self._consume_match_default()
        self.consume("COLON")
        self.skip_newlines()

        default_case = []
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                default_case.append(stmt)
            self.skip_newlines()

    self.consume("END")
    return Match(expr, cases, default_case)


def parse_try(self) -> ASTNode:
    """Parse try/catch/except/finally block."""
    self.consume("TRY")

    try_expr = None
    if self.peek_type() != "THEN":
        try_expr = self.parse_expression()
    self.consume("THEN")
    self.skip_newlines()

    try_body = []
    while self.peek_type() not in ("CATCH", "EXCEPT", "FINALLY", "END"):
        stmt = self.parse_statement()
        if stmt:
            try_body.append(stmt)
        self.skip_newlines()

    catch_blocks: list[tuple[str, Optional[str], list[ASTNode]]] = []
    while self.peek_type() == "CATCH":
        self.consume("CATCH")
        error_type = self.consume("IDENT")
        self.consume("THEN")
        self.skip_newlines()

        catch_body: list[ASTNode] = []
        while self.peek_type() not in ("CATCH", "EXCEPT", "FINALLY", "END"):
            stmt = self.parse_statement()
            if stmt:
                catch_body.append(stmt)
            self.skip_newlines()

        catch_blocks.append((error_type, None, catch_body))

    except_block = None
    if self.peek_type() == "EXCEPT":
        self.consume("EXCEPT")
        error_var = self.consume("IDENT")
        self.consume("THEN")
        self.skip_newlines()

        except_body = []
        while self.peek_type() not in ("FINALLY", "END"):
            stmt = self.parse_statement()
            if stmt:
                except_body.append(stmt)
            self.skip_newlines()

        except_block = (error_var, except_body)

    finally_block = None
    if self.peek_type() == "FINALLY":
        self.consume("FINALLY")
        self.consume("THEN")
        self.skip_newlines()

        finally_body = []
        while self._not_block_end("END"):
            stmt = self.parse_statement()
            if stmt:
                finally_body.append(stmt)
            self.skip_newlines()

        finally_block = finally_body

    self.consume("END")
    return TryExcept(try_expr, try_body, catch_blocks, except_block, finally_block)


def parse_throw(self) -> Throw:
    """Parse: throw \"message\" or throw ErrorType(\"message\")."""
    self.consume("THROW")

    error_type = None
    if (
        self.peek_type() == "IDENT"
        and self.pos + 1 < len(self.tokens)
        and token_type_at(self.tokens, self.pos + 1) == "LPAREN"
    ):
        error_type = self.consume("IDENT")
        self.consume("LPAREN")
        message = self.parse_expression()
        self.consume("RPAREN")
        return Throw(error_type, message)

    message = self.parse_expression()
    return Throw(None, message)


def parse_asm(self) -> InlineAsm:
    """Parse inline assembly: asm(\"instruction\")."""
    self.consume("ASM")
    self.consume("LPAREN")
    asm_code = self.consume("STRLIT")
    asm_code = asm_code[1:-1]
    self.consume("RPAREN")
    return InlineAsm(asm_code)

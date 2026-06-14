from __future__ import annotations

from parser.ast import EnumDef, EnumVariant, parsed_type_to_str


def parse_enum(self) -> EnumDef:
    """Parse enum definition with optional data-carrying variants.

    Simple enum (C-style):
        enum Status then Pending, Active = 5, Done end

    Data-carrying enum (Rust-style):
        enum AST then
            Number(int value)
            BinOp(AST left, string op, AST right)
            Variable(string name)
        end
    """
    self.consume("ENUM")
    name = self.consume("IDENT")
    self.consume("THEN")
    self.skip_newlines()

    variants: list[EnumVariant] = []
    next_value = 0  # Auto-increment for simple variants

    while self.peek() and self.peek_type() != "END":
        # Parse variant: NAME or NAME = value or NAME(type field, ...)
        variant_name = self.consume("IDENT")

        if self.peek() and self.peek_type() == "LPAREN":
            # Data-carrying variant: Name(type field, type field, ...)
            self.consume("LPAREN")
            fields: list[tuple[str, str]] = []

            while self.peek_type() != "RPAREN":
                # Parse: type field_name
                field_type = self.parse_type()
                # Convert ParsedType to string representation
                type_str = parsed_type_to_str(field_type)
                field_name = self.consume("IDENT")
                fields.append((field_name, type_str))

                if self.peek_type() == "COMMA":
                    self.consume("COMMA")
                elif self.peek_type() != "RPAREN":
                    break

            self.consume("RPAREN")
            variants.append(EnumVariant(variant_name, fields, next_value))
            next_value += 1

        elif self.peek() and self.peek_type() == "ASSIGN":
            # Explicit integer value: Name = 5
            self.consume("ASSIGN")
            value_token = self.consume("NUMBER")
            value_int = int(value_token)
            variants.append(EnumVariant(variant_name, None, value_int))
            next_value = value_int + 1
        else:
            # Simple variant with auto-increment
            variants.append(EnumVariant(variant_name, None, next_value))
            next_value += 1

        # Optional comma or semicolon
        if self.peek() and self.peek_type() == "COMMA":
            self.consume("COMMA")
        if self.peek() and self.peek_type() == "SEMICOLON":
            self.consume("SEMICOLON")

        self.skip_newlines()

    self.consume("END")
    return EnumDef(name, variants)

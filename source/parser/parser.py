"""
AILang Parser - Converts tokens to AST
Implements recursive descent parser with operator precedence
"""

import re
from typing import NoReturn, Optional

from .parser_control_flow_advanced_impl import (
    _consume_match_default as _m_consume_match_default_impl,
)
from .parser_control_flow_advanced_impl import (
    _is_match_default as _m_is_match_default_impl,
)
from .parser_control_flow_advanced_impl import (
    _parse_match_pattern as _m_parse_match_pattern_impl,
)
from .parser_control_flow_advanced_impl import parse_asm as _parse_asm_impl
from .parser_control_flow_advanced_impl import parse_match as _parse_match_impl
from .parser_control_flow_advanced_impl import parse_throw as _parse_throw_impl
from .parser_control_flow_advanced_impl import parse_try as _parse_try_impl
from .parser_control_flow_impl import _consume_elsif as _m_consume_elsif_impl
from .parser_control_flow_impl import (
    _extract_bound_from_condition as _m_extract_bound_from_condition_impl,
)
from .parser_control_flow_impl import _finish_while_parse as _m_finish_while_parse_impl
from .parser_control_flow_impl import _is_elsif_token as _m_is_elsif_token_impl
from .parser_control_flow_impl import _is_max_keyword as _m_is_max_keyword_impl
from .parser_control_flow_impl import (
    _parse_auto_bound_loop as _m_parse_auto_bound_loop_impl,
)
from .parser_control_flow_impl import _parse_bounded_loop as _m_parse_bounded_loop_impl
from .parser_control_flow_impl import _parse_for_internal as _m_parse_for_internal_impl
from .parser_control_flow_impl import (
    _parse_for_with_auto_bound as _m_parse_for_with_auto_bound_impl,
)
from .parser_control_flow_impl import (
    _parse_for_with_bound as _m_parse_for_with_bound_impl,
)
from .parser_control_flow_impl import (
    _parse_foreach_header as _m_parse_foreach_header_impl,
)
from .parser_control_flow_impl import (
    _parse_foreach_with_bound as _m_parse_foreach_with_bound_impl,
)
from .parser_control_flow_impl import _parse_inline_max as _m_parse_inline_max_impl
from .parser_control_flow_impl import (
    _parse_loop_with_bound as _m_parse_loop_with_bound_impl,
)
from .parser_control_flow_impl import (
    _parse_statements_until as _m_parse_statements_until_impl,
)
from .parser_control_flow_impl import (
    _parse_until_with_bound as _m_parse_until_with_bound_impl,
)
from .parser_control_flow_impl import (
    _parse_while_with_bound as _m_parse_while_with_bound_impl,
)
from .parser_control_flow_impl import _peek_next_type as _m_peek_next_type_impl
from .parser_control_flow_impl import parse_do_while as _parse_do_while_impl
from .parser_control_flow_impl import parse_for as _parse_for_impl
from .parser_control_flow_impl import parse_foreach as _parse_foreach_impl
from .parser_control_flow_impl import parse_if as _parse_if_impl
from .parser_control_flow_impl import (
    parse_if_continuation as _parse_if_continuation_impl,
)
from .parser_control_flow_impl import parse_loop as _parse_loop_impl
from .parser_control_flow_impl import parse_repeat as _parse_repeat_impl
from .parser_control_flow_impl import parse_spawn as _parse_spawn_impl
from .parser_control_flow_impl import parse_unless as _parse_unless_impl
from .parser_control_flow_impl import parse_until as _parse_until_impl
from .parser_control_flow_impl import parse_while as _parse_while_impl
from .parser_declarations_class_impl import _is_init_method as _m_is_init_method_impl
from .parser_declarations_class_impl import (
    _parse_class_destructor as _m_parse_class_destructor_impl,
)
from .parser_declarations_class_impl import (
    _parse_class_field as _m_parse_class_field_impl,
)
from .parser_declarations_class_impl import (
    _parse_class_init as _m_parse_class_init_impl,
)
from .parser_declarations_class_impl import (
    _parse_init_params as _m_parse_init_params_impl,
)
from .parser_declarations_class_impl import _parse_method as _m_parse_method_impl
from .parser_declarations_class_impl import (
    _parse_visibility as _m_parse_visibility_impl,
)
from .parser_declarations_class_impl import parse_class as _parse_class_impl
from .parser_declarations_impl import _infer_string_types as _m_infer_string_types_impl
from .parser_declarations_impl import _is_generic_call as _m_is_generic_call_impl
from .parser_declarations_impl import _parse_decorators as _m_parse_decorators_impl
from .parser_declarations_impl import _parse_generic_args as _m_parse_generic_args_impl
from .parser_declarations_impl import _parse_generic_call as _m_parse_generic_call_impl
from .parser_declarations_impl import (
    _parse_generic_params as _m_parse_generic_params_impl,
)
from .parser_declarations_impl import (
    _parse_where_constraints as _m_parse_where_constraints_impl,
)
from .parser_declarations_impl import parse_function as _parse_function_impl
from .parser_declarations_impl import parse_opaque_record as _parse_opaque_record_impl
from .parser_declarations_impl import parse_record as _parse_record_impl
from .parser_declarations_impl import parse_template_block as _parse_template_block_impl
from .parser_declarations_impl import parse_union as _parse_union_impl
from .parser_enum_impl import parse_enum as _parse_enum_impl
from .parser_expression_callable_impl import (
    _parse_callable_primary as _m_parse_callable_primary_impl,
)
from .parser_expression_impl import _parse_arg_list as _m_parse_arg_list_impl
from .parser_expression_impl import (
    _parse_arg_list_simple as _m_parse_arg_list_simple_impl,
)
from .parser_expression_impl import _parse_array_literal as _m_parse_array_literal_impl
from .parser_expression_impl import _parse_bool_literal as _m_parse_bool_literal_impl
from .parser_expression_impl import _parse_char_literal as _m_parse_char_literal_impl
from .parser_expression_impl import _parse_dict_entry as _m_parse_dict_entry_impl
from .parser_expression_impl import _parse_dict_literal as _m_parse_dict_literal_impl
from .parser_expression_impl import _parse_heredoc as _m_parse_heredoc_impl
from .parser_expression_impl import (
    _parse_interpolated_string as _m_parse_interpolated_string_impl,
)
from .parser_expression_impl import (
    _parse_number_literal as _m_parse_number_literal_impl,
)
from .parser_expression_impl import _parse_paren_or_cast as _m_parse_paren_or_cast_impl
from .parser_expression_impl import _parse_postfix_ops as _m_parse_postfix_ops_impl
from .parser_expression_impl import _parse_this_primary as _m_parse_this_primary_impl
from .parser_expression_impl import parse_bitwise_and as _parse_bitwise_and_impl
from .parser_expression_impl import parse_bitwise_or as _parse_bitwise_or_impl
from .parser_expression_impl import parse_bitwise_xor as _parse_bitwise_xor_impl
from .parser_expression_impl import parse_comparison as _parse_comparison_impl
from .parser_expression_impl import parse_equality as _parse_equality_impl
from .parser_expression_impl import parse_expression as _parse_expression_impl
from .parser_expression_impl import parse_factor as _parse_factor_impl
from .parser_expression_impl import parse_logical_and as _parse_logical_and_impl
from .parser_expression_impl import parse_logical_or as _parse_logical_or_impl
from .parser_expression_impl import parse_power as _parse_power_impl
from .parser_expression_impl import parse_primary as _parse_primary_impl
from .parser_expression_impl import parse_range as _parse_range_impl
from .parser_expression_impl import parse_shift as _parse_shift_impl
from .parser_expression_impl import parse_term as _parse_term_impl
from .parser_expression_impl import parse_ternary as _parse_ternary_impl
from .parser_expression_impl import parse_unary as _parse_unary_impl
from .parser_module_decls import (
    _consume_extern_symbol_name as _consume_extern_symbol_name_impl,
)
from .parser_module_decls import (
    _consume_module_name_part as _consume_module_name_part_impl,
)
from .parser_module_decls import _parse_extern_decl as _parse_extern_decl_impl
from .parser_module_decls import _parse_import_block as _parse_import_block_impl
from .parser_module_decls import (
    _parse_optional_import_target as _parse_optional_import_target_impl,
)
from .parser_module_decls import _parse_single_import as _parse_single_import_impl
from .parser_module_decls import _parse_single_use as _parse_single_use_impl
from .parser_module_decls import _parse_use_block as _parse_use_block_impl
from .parser_module_decls import parse_cabi_header as _parse_cabi_header_impl
from .parser_module_decls import parse_cimport as _parse_cimport_impl
from .parser_module_decls import parse_cinclude as _parse_cinclude_impl
from .parser_module_decls import parse_extern_fn as _parse_extern_fn_impl
from .parser_module_decls import parse_extern_record as _parse_extern_record_impl
from .parser_module_decls import parse_extern_var as _parse_extern_var_impl
from .parser_module_decls import parse_from_import as _parse_from_import_impl
from .parser_module_decls import parse_import as _parse_import_impl
from .parser_module_decls import parse_library_decl as _parse_library_decl_impl
from .parser_module_decls import parse_link_directive as _parse_link_directive_impl
from .parser_module_decls import parse_use as _parse_use_impl
from .parser_program_impl import _is_ui_block as _m_is_ui_block_impl
from .parser_program_impl import (
    _parse_bare_global_const as _m_parse_bare_global_const_impl,
)
from .parser_program_impl import _parse_definition as _m_parse_definition_impl
from .parser_program_impl import _parse_global_var as _m_parse_global_var_impl
from .parser_program_impl import (
    _parse_import_statement as _m_parse_import_statement_impl,
)
from .parser_program_impl import _parse_program_impl as _m_parse_program_impl_impl
from .parser_program_impl import _parse_type_alias as _m_parse_type_alias_impl
from .parser_program_impl import (
    _parse_type_alias_target as _m_parse_type_alias_target_impl,
)
from .parser_program_impl import _skip_ui_block as _m_skip_ui_block_impl
from .parser_program_impl import parse_program as _parse_program_impl
from .parser_statements_ident_impl import _parse_ident_stmt as _m_parse_ident_stmt_impl
from .parser_statements_ident_impl import (
    _parse_keyword_as_ident_stmt as _m_parse_keyword_as_ident_stmt_impl,
)
from .parser_statements_impl import _is_statement_start as _m_is_statement_start_impl
from .parser_statements_impl import (
    _lookahead_for_assign as _m_lookahead_for_assign_impl,
)
from .parser_statements_impl import (
    _lookahead_is_custom_type_decl as _m_lookahead_is_custom_type_decl_impl,
)
from .parser_statements_impl import _parse_assert_stmt as _m_parse_assert_stmt_impl
from .parser_statements_impl import _parse_block as _m_parse_block_impl
from .parser_statements_impl import _parse_break_stmt as _m_parse_break_stmt_impl
from .parser_statements_impl import _parse_comptime as _m_parse_comptime_impl
from .parser_statements_impl import _parse_continue_stmt as _m_parse_continue_stmt_impl
from .parser_statements_impl import (
    _parse_dot_access_stmt as _m_parse_dot_access_stmt_impl,
)
from .parser_statements_impl import (
    _parse_prefix_increment_stmt as _m_parse_prefix_increment_stmt_impl,
)
from .parser_statements_impl import _parse_print_stmt as _m_parse_print_stmt_impl
from .parser_statements_impl import (
    _parse_reinterpret_cast as _m_parse_reinterpret_cast_impl,
)
from .parser_statements_impl import _parse_return_stmt as _m_parse_return_stmt_impl
from .parser_statements_impl import (
    _parse_statement_impl as _m_parse_statement_impl_impl,
)
from .parser_statements_impl import _parse_static_assert as _m_parse_static_assert_impl
from .parser_statements_impl import (
    _parse_subscript_stmt as _m_parse_subscript_stmt_impl,
)
from .parser_statements_impl import _parse_tuple_assign as _m_parse_tuple_assign_impl
from .parser_statements_impl import (
    _parse_typed_var_decl as _m_parse_typed_var_decl_impl,
)
from .parser_statements_impl import (
    _wrap_postfix_conditional as _m_wrap_postfix_conditional_impl,
)
from .parser_statements_impl import parse_statement as _parse_statement_impl
from .parser_statements_object_impl import _parse_this_stmt as _m_parse_this_stmt_impl
from .parser_statements_object_impl import parse_new as _parse_new_impl
from .parser_statements_range_impl import (
    _lookahead_is_range_decl as _m_lookahead_is_range_decl_impl,
)
from .parser_statements_range_impl import (
    _parse_range_bound as _m_parse_range_bound_impl,
)
from .parser_statements_range_impl import (
    _parse_range_var_decl as _m_parse_range_var_decl_impl,
)
from .parser_type_parsing import _is_type_token as _is_type_token_impl
from .parser_type_parsing import _parse_single_param as _parse_single_param_impl
from .parser_type_parsing import _parse_type_name as _parse_type_name_impl
from .parser_type_parsing import parse_type as _parse_type_impl


class Parser:
    """
    Recursive descent parser for AILang
    Converts token stream into Abstract Syntax Tree
    """

    tokens: list[tuple[str, str, int, int]]
    pos: int
    # Parser depth limit to prevent stack overflow from deeply nested expressions
    MAX_PARSE_DEPTH = 500
    _parse_depth: int

    def __init__(self, tokens: list[tuple[str, str, int, int]]) -> None:
        self.tokens = tokens
        self.pos = 0
        self._parse_depth = 0

    def _check_depth(self) -> None:
        """Check if parsing depth exceeds limit to prevent stack overflow."""
        if self._parse_depth > self.MAX_PARSE_DEPTH:
            line = self.peek_line() if self.peek() else 0
            col = self.peek_col() if self.peek() else 0
            raise SyntaxError(
                f"Line {line}, Col {col}: Expression too deeply nested "
                f"(depth {self._parse_depth} > {self.MAX_PARSE_DEPTH}). "
                "This may indicate malicious input or malformed code."
            )

    def _not_block_end(self, *terminators: str) -> bool:
        """Check if the current token is NOT a block terminator nor EOF.

        Raises SyntaxError on unexpected end of input so body-parsing
        loops never spin forever on a missing ``end``.
        """
        tt = self.peek_type()
        if tt is None:
            raise SyntaxError("Unexpected end of input \u2014 missing 'end' keyword?")
        return tt not in terminators

    def peek(self) -> Optional[tuple[str, str, int, int]]:
        """Look at current token without consuming"""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def peek_type(self) -> Optional[str]:
        """Get type of current token"""
        token = self.peek()
        if not token:
            return None
        token_type, _, _, _ = token
        return token_type

    def peek_text(self) -> str:
        """Get text of current token"""
        token = self.peek()
        if not token:
            raise SyntaxError("Unexpected end of input")
        _, text, _, _ = token
        return text

    def peek_line(self) -> int:
        """Get line number of current token"""
        token = self.peek()
        if not token:
            raise SyntaxError("Unexpected end of input")
        _, _, line, _ = token
        return line

    def peek_col(self) -> int:
        """Get column number of current token"""
        token = self.peek()
        if not token:
            return 0
        _, _, _, col = token
        return col

    def _get_token_line(self, token: Optional[tuple[str, str, int, int]]) -> int:
        """Safely get line number from a token tuple, returns -1 if invalid."""
        if token is None or len(token) < 3:
            return -1
        _ttype, _text, line, *_rest = token
        return line

    def _get_token_col(self, token: Optional[tuple[str, str, int, int]]) -> int:
        """Safely get column number from a token tuple, returns 0 if invalid."""
        if token is None or len(token) < 4:
            return 0
        _ttype, _text, _line, col = token
        return col

    def consume(self, expected_type: Optional[str] = None) -> str:
        """Consume current token and advance"""
        if self.pos >= len(self.tokens):
            raise SyntaxError("Unexpected end of input")
        token_type, text, line, col = self.tokens[self.pos]
        if expected_type and token_type != expected_type:
            raise SyntaxError(
                f"Line {line}, Col {col}: Expected {expected_type}, got {token_type}"
            )
        self.pos += 1
        return text

    # Keywords that are valid as field/method names after a dot
    _FIELD_NAME_KEYWORDS = frozenset(
        {
            "TYPE",
            "MATCH",
            "RANGE",
            "IMPORT",
            "FROM",
            "USE",
            "RETURN",
            "BREAK",
            "CONTINUE",
            "THROW",
            "ASSERT",
            "PUBLIC",
            "PRIVATE",
            "MUTABLE",
            "CONST",
        }
    )

    # Type tokens that indicate a variable declaration.
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

    # Tokens that can start a function/builtin call in parse_primary.
    _CALLABLE_TOKENS = frozenset(
        {
            "IDENT",
            "TEST",
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
            "SPAWN",
            "JOIN",
            "ATOMIC",
            "CHANNEL",
            "CHAN_SEND",
            "CHAN_RECV",
            "CHAN_TRY_SEND",
            "CHAN_TRY_RECV",
            "CHAN_CLOSE",
            "ALLOC",
            "DEALLOC",
            "PEEK64",
            "POKE64",
            "PEEK32",
            "POKE32",
            "PEEK8",
            "POKE8",
        }
    )

    def _consume_field_name(self) -> str:
        """Consume a field name: IDENT or keyword usable as field name."""
        if self.pos >= len(self.tokens):
            raise SyntaxError("Unexpected end of input, expected field name")
        token_type, text, line, col = self.tokens[self.pos]
        if token_type == "IDENT" or token_type in self._FIELD_NAME_KEYWORDS:
            self.pos += 1
            return text
        raise SyntaxError(f"Line {line}, Col {col}: Expected IDENT, got {token_type}")

    def expect(self, expected_type: str) -> str:
        """Alias for consume with required type"""
        return self.consume(expected_type)

    def error(self, message: str) -> NoReturn:
        """Raise a syntax error with current line and column number"""
        line = self.peek_line() if self.peek() else 0
        col = self.peek_col() if self.peek() else 0
        if col > 0:
            raise SyntaxError(f"Line {line}, Col {col}: {message}")
        raise SyntaxError(f"Line {line}: {message}")

    def skip_newlines(self) -> None:
        """Skip all newline tokens"""
        while self.peek_type() == "NEWLINE":
            self.pos += 1

    # --------------------------------------------------------------------
    def _is_type_ident(self, name: str) -> bool:
        """Check if an identifier represents a type name.

        Recognizes:
        - Built-in cute type names (tiny, byte, int, uint, long, etc.)
        - Numeric type patterns (i32, u64, f32, etc.)
        - PascalCase identifiers (assumed to be class/record types)
        """
        cute_names = {
            "tiny",
            "byte",
            "small",
            "usmall",
            "short",
            "ushort",
            "int",
            "uint",
            "long",
            "ulong",
            "wide",
            "uwide",
            "vast",
            "uvast",
            "grand",
            "ugrand",
            "giant",
            "ugiant",
            "titan",
            "utitan",
            "colos",
            "ucolos",
            "pointer",
            "ptrptr",
            "str_array",
        }
        if name in cute_names:
            return True
        if re.match(r"^[iu]\d+$", name):
            return True
        if re.match(r"^f\d+$", name):
            return True
        # PascalCase identifiers are assumed to be class/record types
        # e.g., Unbounded, BigInt, MyClass
        # Also accept single uppercase letters (T, U, K, V) as generic type params
        return bool(
            name and name[0].isupper() and (not name.isupper() or len(name) == 1)
        )

    # ========================================================================
    # Top-level parsing
    # ========================================================================

    # Delegated implementations extracted to parser helper modules.
    parse_use = _parse_use_impl
    _consume_module_name_part = _consume_module_name_part_impl
    _parse_single_use = _parse_single_use_impl
    _parse_use_block = _parse_use_block_impl
    parse_import = _parse_import_impl
    _parse_single_import = _parse_single_import_impl
    _parse_optional_import_target = _parse_optional_import_target_impl
    _parse_import_block = _parse_import_block_impl
    parse_from_import = _parse_from_import_impl
    parse_library_decl = _parse_library_decl_impl
    parse_cinclude = _parse_cinclude_impl
    parse_cabi_header = _parse_cabi_header_impl
    parse_link_directive = _parse_link_directive_impl
    parse_cimport = _parse_cimport_impl
    _parse_extern_decl = _parse_extern_decl_impl
    parse_extern_var = _parse_extern_var_impl
    parse_extern_record = _parse_extern_record_impl
    parse_extern_fn = _parse_extern_fn_impl
    _consume_extern_symbol_name = _consume_extern_symbol_name_impl
    _parse_single_param = _parse_single_param_impl
    _parse_type_name = _parse_type_name_impl
    _is_type_token = _is_type_token_impl
    parse_type = _parse_type_impl

    _parse_import_statement = _m_parse_import_statement_impl
    _is_ui_block = _m_is_ui_block_impl
    _skip_ui_block = _m_skip_ui_block_impl
    _parse_definition = _m_parse_definition_impl
    parse_program = _parse_program_impl
    _parse_program_impl = _m_parse_program_impl_impl
    _parse_global_var = _m_parse_global_var_impl
    _parse_bare_global_const = _m_parse_bare_global_const_impl
    _parse_type_alias_target = _m_parse_type_alias_target_impl
    _parse_type_alias = _m_parse_type_alias_impl
    _parse_decorators = _m_parse_decorators_impl
    _parse_generic_params = _m_parse_generic_params_impl
    _parse_where_constraints = _m_parse_where_constraints_impl
    _parse_generic_args = _m_parse_generic_args_impl
    _is_generic_call = _m_is_generic_call_impl
    _parse_generic_call = _m_parse_generic_call_impl
    parse_function = _parse_function_impl
    _infer_string_types = _m_infer_string_types_impl
    parse_opaque_record = _parse_opaque_record_impl
    parse_record = _parse_record_impl
    parse_union = _parse_union_impl
    parse_template_block = _parse_template_block_impl
    parse_enum = _parse_enum_impl
    _parse_class_destructor = _m_parse_class_destructor_impl
    _parse_init_params = _m_parse_init_params_impl
    _parse_class_init = _m_parse_class_init_impl
    _parse_class_field = _m_parse_class_field_impl
    parse_class = _parse_class_impl
    _parse_visibility = _m_parse_visibility_impl
    _is_init_method = _m_is_init_method_impl
    _parse_method = _m_parse_method_impl
    parse_new = _parse_new_impl
    _parse_reinterpret_cast = _m_parse_reinterpret_cast_impl
    _parse_return_stmt = _m_parse_return_stmt_impl
    _parse_break_stmt = _m_parse_break_stmt_impl
    _parse_continue_stmt = _m_parse_continue_stmt_impl
    _parse_assert_stmt = _m_parse_assert_stmt_impl
    _parse_comptime = _m_parse_comptime_impl
    _parse_static_assert = _m_parse_static_assert_impl
    _is_statement_start = _m_is_statement_start_impl
    _parse_print_stmt = _m_parse_print_stmt_impl
    _parse_typed_var_decl = _m_parse_typed_var_decl_impl
    _lookahead_for_assign = _m_lookahead_for_assign_impl
    _parse_keyword_as_ident_stmt = _m_parse_keyword_as_ident_stmt_impl
    _parse_prefix_increment_stmt = _m_parse_prefix_increment_stmt_impl
    _parse_ident_stmt = _m_parse_ident_stmt_impl
    _lookahead_is_custom_type_decl = _m_lookahead_is_custom_type_decl_impl
    _lookahead_is_range_decl = _m_lookahead_is_range_decl_impl
    _parse_range_var_decl = _m_parse_range_var_decl_impl
    _parse_range_bound = _m_parse_range_bound_impl
    _parse_tuple_assign = _m_parse_tuple_assign_impl
    _parse_dot_access_stmt = _m_parse_dot_access_stmt_impl
    _parse_block = _m_parse_block_impl
    _parse_subscript_stmt = _m_parse_subscript_stmt_impl
    _parse_this_stmt = _m_parse_this_stmt_impl
    parse_statement = _parse_statement_impl
    _parse_statement_impl = _m_parse_statement_impl_impl
    _wrap_postfix_conditional = _m_wrap_postfix_conditional_impl
    _is_elsif_token = _m_is_elsif_token_impl
    _consume_elsif = _m_consume_elsif_impl
    _peek_next_type = _m_peek_next_type_impl
    parse_if = _parse_if_impl
    parse_if_continuation = _parse_if_continuation_impl
    _parse_bounded_loop = _m_parse_bounded_loop_impl
    _parse_auto_bound_loop = _m_parse_auto_bound_loop_impl
    _extract_bound_from_condition = _m_extract_bound_from_condition_impl
    _is_max_keyword = _m_is_max_keyword_impl
    _parse_inline_max = _m_parse_inline_max_impl
    _parse_statements_until = _m_parse_statements_until_impl
    _finish_while_parse = _m_finish_while_parse_impl
    _parse_while_with_bound = _m_parse_while_with_bound_impl
    _parse_until_with_bound = _m_parse_until_with_bound_impl
    _parse_for_with_bound = _m_parse_for_with_bound_impl
    _parse_for_with_auto_bound = _m_parse_for_with_auto_bound_impl
    _parse_for_internal = _m_parse_for_internal_impl
    _parse_loop_with_bound = _m_parse_loop_with_bound_impl
    _parse_foreach_header = _m_parse_foreach_header_impl
    _parse_foreach_with_bound = _m_parse_foreach_with_bound_impl
    parse_while = _parse_while_impl
    parse_do_while = _parse_do_while_impl
    parse_unless = _parse_unless_impl
    parse_until = _parse_until_impl
    parse_for = _parse_for_impl
    parse_loop = _parse_loop_impl
    parse_foreach = _parse_foreach_impl
    parse_repeat = _parse_repeat_impl
    parse_spawn = _parse_spawn_impl
    _parse_match_pattern = _m_parse_match_pattern_impl
    _is_match_default = _m_is_match_default_impl
    _consume_match_default = _m_consume_match_default_impl
    parse_match = _parse_match_impl
    parse_try = _parse_try_impl
    parse_throw = _parse_throw_impl
    parse_asm = _parse_asm_impl
    parse_expression = _parse_expression_impl
    parse_ternary = _parse_ternary_impl
    parse_logical_or = _parse_logical_or_impl
    parse_logical_and = _parse_logical_and_impl
    parse_bitwise_or = _parse_bitwise_or_impl
    parse_bitwise_xor = _parse_bitwise_xor_impl
    parse_bitwise_and = _parse_bitwise_and_impl
    parse_equality = _parse_equality_impl
    parse_comparison = _parse_comparison_impl
    parse_shift = _parse_shift_impl
    parse_range = _parse_range_impl
    parse_term = _parse_term_impl
    parse_factor = _parse_factor_impl
    parse_power = _parse_power_impl
    parse_unary = _parse_unary_impl
    parse_primary = _parse_primary_impl
    _parse_this_primary = _m_parse_this_primary_impl
    _parse_paren_or_cast = _m_parse_paren_or_cast_impl
    _parse_number_literal = _m_parse_number_literal_impl
    _parse_bool_literal = _m_parse_bool_literal_impl
    _parse_char_literal = _m_parse_char_literal_impl
    _parse_heredoc = _m_parse_heredoc_impl
    _parse_interpolated_string = _m_parse_interpolated_string_impl
    _parse_array_literal = _m_parse_array_literal_impl
    _parse_dict_literal = _m_parse_dict_literal_impl
    _parse_dict_entry = _m_parse_dict_entry_impl
    _parse_arg_list = _m_parse_arg_list_impl
    _parse_arg_list_simple = _m_parse_arg_list_simple_impl
    _parse_callable_primary = _m_parse_callable_primary_impl
    _parse_postfix_ops = _m_parse_postfix_ops_impl

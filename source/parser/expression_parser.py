"""Compatibility wrapper for expression parser helpers.

The active parser binds the functions from ``parser_expression_impl`` directly.
This class remains only for older imports that expected an ``ExpressionParser``
mixin; keeping implementation logic here would duplicate parser behavior.
"""

from __future__ import annotations

from typing import Any

from .parser_expression_impl import (
    _parse_arg_list,
    _parse_arg_list_simple,
    _parse_array_literal,
    _parse_bool_literal,
    _parse_char_literal,
    _parse_dict_entry,
    _parse_dict_literal,
    _parse_heredoc,
    _parse_interpolated_string,
    _parse_number_literal,
    _parse_paren_or_cast,
    _parse_postfix_ops,
    _parse_this_primary,
    parse_bitwise_and,
    parse_bitwise_or,
    parse_bitwise_xor,
    parse_comparison,
    parse_equality,
    parse_expression,
    parse_factor,
    parse_logical_and,
    parse_logical_or,
    parse_power,
    parse_primary,
    parse_range,
    parse_shift,
    parse_term,
    parse_ternary,
    parse_unary,
)


class ExpressionParser:
    """Legacy mixin API backed by the single parser implementation."""

    def __init__(self: Any) -> None:
        self._parse_depth = 0

    parse_expression = parse_expression
    parse_ternary = parse_ternary
    parse_logical_or = parse_logical_or
    parse_logical_and = parse_logical_and
    parse_bitwise_or = parse_bitwise_or
    parse_bitwise_xor = parse_bitwise_xor
    parse_bitwise_and = parse_bitwise_and
    parse_equality = parse_equality
    parse_comparison = parse_comparison
    parse_shift = parse_shift
    parse_range = parse_range
    parse_term = parse_term
    parse_factor = parse_factor
    parse_power = parse_power
    parse_unary = parse_unary
    parse_primary = parse_primary
    _parse_this_primary = _parse_this_primary
    _parse_paren_or_cast = _parse_paren_or_cast
    _parse_number_literal = _parse_number_literal
    _parse_bool_literal = _parse_bool_literal
    _parse_char_literal = _parse_char_literal
    _parse_heredoc = _parse_heredoc
    _parse_interpolated_string = _parse_interpolated_string
    _parse_array_literal = _parse_array_literal
    _parse_dict_literal = _parse_dict_literal
    _parse_dict_entry = _parse_dict_entry
    _parse_arg_list = _parse_arg_list
    _parse_arg_list_simple = _parse_arg_list_simple
    _parse_postfix_ops = _parse_postfix_ops

"""CTranspiler type-state aliases and per-function type tracking."""

from __future__ import annotations

from parser import ast as A
from typing import Any

ClassField = tuple[str, str, str]
RecordField = tuple[str, str]


class _CTranspilerTypeStateMixin:
    @property
    def classes(self: Any) -> dict[str, tuple[list[tuple[str, str, str]], list[Any]]]:
        return self.type_info.classes

    @classes.setter
    def classes(
        self: Any, value: dict[str, tuple[list[tuple[str, str, str]], list[Any]]]
    ) -> None:
        self.type_info.classes = value

    @property
    def functions(self: Any) -> dict[str, tuple[list[str], str]]:
        return self.type_info.functions

    @functions.setter
    def functions(self: Any, value: dict[str, tuple[list[str], str]]) -> None:
        self.type_info.functions = value

    @property
    def data_enums(self: Any) -> dict[str, dict[str, list[tuple[str, str]]]]:
        return self.type_info.data_enums

    @data_enums.setter
    def data_enums(
        self: Any, value: dict[str, dict[str, list[tuple[str, str]]]]
    ) -> None:
        self.type_info.data_enums = value

    @property
    def _type_decorators(self: Any) -> dict[str, list[Any]]:
        return self.type_info.type_decorators

    @_type_decorators.setter
    def _type_decorators(self: Any, value: dict[str, list[Any]]) -> None:
        self.type_info.type_decorators = value

    @property
    def _func_defaults(self: Any) -> dict[str, list[tuple[int, A.ASTNode]]]:
        return self.type_info.func_defaults

    @_func_defaults.setter
    def _func_defaults(
        self: Any, value: dict[str, list[tuple[int, A.ASTNode]]]
    ) -> None:
        self.type_info.func_defaults = value

    @property
    def _string_vars(self: Any) -> dict[str | None, set[str]]:
        return self.type_info.string_vars

    @_string_vars.setter
    def _string_vars(self: Any, value: dict[str | None, set[str]]) -> None:
        self.type_info.string_vars = value

    @property
    def _vec256_vars(self: Any) -> dict[str | None, set[str]]:
        return self.type_info.vec256_vars

    @_vec256_vars.setter
    def _vec256_vars(self: Any, value: dict[str | None, set[str]]) -> None:
        self.type_info.vec256_vars = value

    @property
    def _vec512_vars(self: Any) -> dict[str | None, set[str]]:
        return self.type_info.vec512_vars

    @_vec512_vars.setter
    def _vec512_vars(self: Any, value: dict[str | None, set[str]]) -> None:
        self.type_info.vec512_vars = value

    @property
    def _array_vars(self: Any) -> set[str]:
        return self.type_info.array_vars

    @_array_vars.setter
    def _array_vars(self: Any, value: set[str]) -> None:
        self.type_info.array_vars = value

    @property
    def _dict_vars(self: Any) -> set[str]:
        return self.type_info.dict_vars

    @_dict_vars.setter
    def _dict_vars(self: Any, value: set[str]) -> None:
        self.type_info.dict_vars = value

    @property
    def _dyn_array_vars(self: Any) -> set[str]:
        return self.type_info.dyn_array_vars

    @_dyn_array_vars.setter
    def _dyn_array_vars(self: Any, value: set[str]) -> None:
        self.type_info.dyn_array_vars = value

    @property
    def _enum_vars(self: Any) -> set[str]:
        return self.type_info.enum_vars

    @_enum_vars.setter
    def _enum_vars(self: Any, value: set[str]) -> None:
        self.type_info.enum_vars = value

    @property
    def _var_types(self: Any) -> dict[str, str]:
        return self.type_info.var_types

    @_var_types.setter
    def _var_types(self: Any, value: dict[str, str]) -> None:
        self.type_info.var_types = value

    @property
    def _single_use_owned_strings(self: Any) -> set[str]:
        return self.type_info.single_use_owned_strings

    @_single_use_owned_strings.setter
    def _single_use_owned_strings(self: Any, value: set[str]) -> None:
        self.type_info.single_use_owned_strings = value

    @property
    def _recursive_funcs(self: Any) -> set[str]:
        return self.type_info.recursive_funcs

    @_recursive_funcs.setter
    def _recursive_funcs(self: Any, value: set[str]) -> None:
        self.type_info.recursive_funcs = value

"""Parser helpers for contextual internal C ABI boundary functions."""

from __future__ import annotations

from typing import Optional

from .ast import ASTNode, Function, ParsedType, parsed_type_to_str

_INTERNAL_C_ABI_TYPES: dict[str, tuple[str, ParsedType]] = {
    "void": ("void", "void"),
    "size_t": ("size_t", "i64"),
    "ssize_t": ("ssize_t", "i64"),
    "int": ("int", "i64"),
    "uint": ("unsigned int", "i64"),
    "long": ("long", "i64"),
    "ulong": ("unsigned long", "i64"),
    "longlong": ("long long", "i64"),
    "int32_t": ("int32_t", "i64"),
    "uint32_t": ("uint32_t", "i64"),
    "int64_t": ("int64_t", "i64"),
    "uint64_t": ("uint64_t", "i64"),
    "pid_t": ("pid_t", "i64"),
    "uid_t": ("uid_t", "i64"),
    "gid_t": ("gid_t", "i64"),
    "mode_t": ("mode_t", "i64"),
    "pointer": ("void *", "ptr"),
    "ptr": ("void *", "ptr"),
    "const_pointer": ("const void *", "ptr"),
    "cptr": ("const void *", "ptr"),
    "cstring": ("const char *", "ptr"),
    "charptr": ("char *", "ptr"),
    "char_pointer": ("char *", "ptr"),
    "charpp": ("char **", "ptr"),
    "cstringp": ("const char **", "ptr"),
    "fileptr": ("FILE *", "ptr"),
    "size_tp": ("size_t *", "ptr"),
    "uintp": ("unsigned int *", "ptr"),
    "intp": ("int *", "ptr"),
    "longp": ("long *", "ptr"),
}


def _is_internal_keyword(self) -> bool:
    return self.peek_type() == "IDENT" and self.peek_text() == "internal"


def _parse_internal_abi_type_after_keyword(self) -> tuple[str, ParsedType]:
    token_type = self.peek_type()
    if token_type in {
        "VOID",
        "INT",
        "UINT",
        "LONG",
        "ULONG",
        "PTR",
    }:
        type_name = self.consume().lower()
    elif token_type == "IDENT":
        type_name = self.consume("IDENT")
    else:
        self.error(f"Expected internal ABI type, got {self.peek_text()!r}")
        type_name = "int"  # Unreachable, keeps type checkers happy.

    normalized = type_name.lower()
    if normalized not in _INTERNAL_C_ABI_TYPES:
        self.error(
            "Unknown internal ABI type "
            f"{type_name!r}; expected one of: "
            + ", ".join(sorted(_INTERNAL_C_ABI_TYPES))
        )
    return _INTERNAL_C_ABI_TYPES[normalized]


def _parse_internal_abi_type(self) -> tuple[str, ParsedType]:
    if not _is_internal_keyword(self):
        self.error("Expected 'internal' before ABI type")
    self.consume("IDENT")
    return _parse_internal_abi_type_after_keyword(self)


def _c_abi_for_ailang_type(ptype: ParsedType) -> str:
    type_name = parsed_type_to_str(ptype).lower()
    if type_name in {"ptr", "pointer"}:
        return "void *"
    if type_name == "string":
        return "const char *"
    if type_name == "void":
        return "void"
    if type_name == "bool":
        return "bool"
    if type_name == "float":
        return "float"
    if type_name == "double":
        return "double"
    if type_name in {"tiny", "byte"}:
        return "int8_t"
    if type_name in {"small", "short"}:
        return "int16_t"
    if type_name in {"int", "long", "wide", "vast", "i64"}:
        return "int64_t"
    if type_name in {"uint", "ulong", "uwide", "uvast", "u64"}:
        return "uint64_t"
    return "int64_t"


def _parse_internal_param(
    self,
) -> tuple[tuple[str, ParsedType, Optional[ASTNode]], str]:
    param_name = self.consume("IDENT")
    param_type: ParsedType = "i64"
    abi_type = "int64_t"

    if self.peek_type() == "COLON":
        self.consume("COLON")
        if _is_internal_keyword(self):
            abi_type, param_type = _parse_internal_abi_type(self)
        else:
            param_type = self.parse_type()
            abi_type = _c_abi_for_ailang_type(param_type)

    if self.peek_type() == "ASSIGN":
        self.error("internal ABI function parameters cannot have defaults")

    return (param_name, param_type, None), f"{abi_type} {param_name}"


def _parse_internal_function(
    self,
    decorators: list[str],
    start_line: int,
    is_public: bool,
    is_async: bool,
) -> Function:
    self.consume("IDENT")  # contextual 'internal'
    abi_return_type, return_type = _parse_internal_abi_type_after_keyword(self)
    name = self.consume("IDENT")

    self.consume("LPAREN")
    self.skip_newlines()
    params: list[tuple[str, ParsedType, Optional[ASTNode]]] = []
    c_params: list[str] = []
    if self.peek_type() != "RPAREN":
        param, c_param = _parse_internal_param(self)
        params.append(param)
        c_params.append(c_param)
        self.skip_newlines()
        while self.peek_type() == "COMMA":
            self.consume("COMMA")
            self.skip_newlines()
            param, c_param = _parse_internal_param(self)
            params.append(param)
            c_params.append(c_param)
            self.skip_newlines()
    self.consume("RPAREN")
    self.consume("COLON")
    self.skip_newlines()

    body: list[ASTNode] = []
    while self._not_block_end("END"):
        stmt = self.parse_statement()
        if stmt:
            body.append(stmt)
        self.skip_newlines()
    self.consume("END")

    if not c_params:
        c_params = ["void"]
    abi_decorator = f"abi({abi_return_type},{','.join(c_params)})"
    export_decorator = f"export({name})"
    merged_decorators = list(decorators)
    if not any(
        str(item).lstrip("@").lower().startswith("export") for item in merged_decorators
    ):
        merged_decorators.append(export_decorator)
    if not any(
        str(item).lstrip("@").lower().startswith(("abi(", "cabi(", "c_abi("))
        for item in merged_decorators
    ):
        merged_decorators.append(abi_decorator)

    fn = Function(
        name,
        params,
        return_type,
        body,
        is_public=is_public,
        decorators=merged_decorators,
        is_async=is_async,
        is_test=False,
    )
    fn.set_pos(start_line)
    return fn

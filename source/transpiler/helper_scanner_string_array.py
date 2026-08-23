"""String and array proof helpers for HelperScanner."""

from __future__ import annotations

from parser import ast as A
from typing import Optional

from ast_access import arg_at
from transpiler.arithmetic_literal_proofs import int_literal_in_range, int_literal_value
from transpiler.class_field_ownership import is_string_type


def _array_access_literal_proven(self, node: A.ArrayAccess) -> bool:
    array_len = self._known_array_len_hint(node.array)
    return array_len is not None and int_literal_in_range(node.index, 0, array_len)


def _cached_strlen_field_arg(self, node: A.Call) -> bool:
    if node.name not in ("strlen", "len") or len(node.args) != 1:
        return False
    arg = arg_at(node, 0)
    if not isinstance(arg, A.FieldAccess):
        return False
    owner_class = None
    if isinstance(arg.object_expr, A.ThisExpr):
        owner_class = self._current_class
    if owner_class is None:
        return False
    return self._class_field_is_string(owner_class, arg.field_name)


def _class_field_is_string(self, class_name: str, field_name: str) -> bool:
    class_info = self.classes.get(class_name)
    fields = class_info[0] if class_info else []
    for field in fields:
        if not isinstance(field, tuple) or len(field) < 3:
            continue
        _visibility, name, field_type = field[:3]
        if str(name) == field_name:
            return is_string_type(field_type)
    return False


def _field_assign_targets_string(self, node: A.FieldAssign) -> bool:
    owner_class = None
    if isinstance(node.object_expr, A.ThisExpr):
        owner_class = self._current_class
    if owner_class is None:
        return False
    return self._class_field_is_string(owner_class, node.field_name)


def _is_integer_type_name(self, type_name: str) -> bool:
    lowered = str(type_name).strip().lower()
    if lowered in {
        "int",
        "integer",
        "long",
        "short",
        "byte",
        "i8",
        "i16",
        "i32",
        "i64",
        "isize",
        "uint",
        "ulong",
        "ushort",
        "ubyte",
        "u8",
        "u16",
        "u32",
        "u64",
        "usize",
    }:
        return True
    if lowered.startswith("int") and lowered[3:].isdigit():
        return True
    return lowered.startswith("uint") and lowered[4:].isdigit()


def _known_array_len_hint(self, array_expr: A.ASTNode) -> Optional[int]:
    if not isinstance(array_expr, A.Variable):
        return None
    scoped_key = (self._func_scope, array_expr.name)
    if scoped_key in self._array_len_hints:
        return int(self._array_len_hints[scoped_key])
    global_key = (None, array_expr.name)
    if global_key in self._array_len_hints:
        return int(self._array_len_hints[global_key])
    type_text = self._local_types.get(array_expr.name, "")
    if not (
        isinstance(type_text, str) and type_text.startswith("[") and ";" in type_text
    ):
        return None
    try:
        size_text = type_text.rsplit(";", 1)[1].rstrip("] ")
        return int(size_text.strip())
    except (TypeError, ValueError):
        return None


def _literal_char_at_length_proven(self, node: A.Call) -> bool:
    if node.name != "char_at" or len(node.args) < 3:
        return False
    index_value = int_literal_value(arg_at(node, 1))
    length_value = int_literal_value(arg_at(node, 2))
    return (
        index_value is not None
        and length_value is not None
        and 0 <= index_value < length_value
    )


def _scan_methodcall_hidden_string_lengths(self, node: A.MethodCall) -> None:
    cls = None
    if isinstance(node.object_expr, A.Variable):
        owners = [
            class_name
            for class_name, (_fields, methods) in self.classes.items()
            if any(method.name == node.method_name for method in methods)
        ]
        cls = owners[0] if len(owners) == 1 else None
    if cls is None:
        return
    method = next(
        (
            candidate
            for candidate in self.classes.get(cls, ([], []))[1]
            if candidate.name == node.method_name
        ),
        None,
    )
    if method is None:
        return
    for index, arg in enumerate(node.args):
        if index >= len(method.params or []):
            continue
        param = method.params[index]
        if (
            isinstance(param, tuple)
            and len(param) >= 2
            and is_string_type(param[1])
            and self._virtual_concat_numeric_arg(arg) is not None
        ):
            self._needs.helpers.add("i64_decimal_len")


def _scan_newexpr_hidden_string_lengths(self, node: A.NewExpr) -> None:
    class_info = self.classes.get(node.type_name)
    fields, methods = class_info if class_info else ([], [])
    init_method = next((m for m in methods if m.name == "init"), None)
    sources = init_method.params if init_method is not None else fields
    for index, arg in enumerate(node.args):
        if index >= len(sources):
            continue
        source = sources[index]
        if init_method is not None:
            is_string_source = (
                isinstance(source, tuple)
                and len(source) >= 2
                and is_string_type(source[1])
            )
        else:
            is_string_source = (
                isinstance(source, tuple)
                and len(source) >= 3
                and is_string_type(source[2])
            )
        if is_string_source and self._virtual_concat_numeric_arg(arg) is not None:
            self._needs.helpers.add("i64_decimal_len")


def _scan_streq_slice_fastpath(self, node: A.Call) -> bool:
    """Register helpers for streq(substr(...), literal) without substr."""
    if node.name != "streq" or len(node.args) != 2:
        return False
    left, right = node.args
    slice_arg: A.Call | None = None
    if (
        isinstance(left, A.Call)
        and left.name == "substr"
        and isinstance(right, A.StringLit)
    ):
        slice_arg = left
    elif (
        isinstance(right, A.Call)
        and right.name == "substr"
        and isinstance(left, A.StringLit)
    ):
        slice_arg = right
    if slice_arg is None or len(slice_arg.args) < 3:
        return False
    self._needs.helpers.add("streq_lit")
    for arg in slice_arg.args[:3]:
        self._scan_node(arg)
    return True


def _str_arg_is_known_integer(self, node: A.ASTNode) -> bool:
    if isinstance(node, A.Number):
        return not node.is_float
    if isinstance(node, A.Variable):
        var_type = self._local_types.get(node.name)
        return var_type is not None and self._is_integer_type_name(var_type)
    if isinstance(node, A.UnaryOp):
        return node.op in ("+", "plus", "-", "minus") and (
            self._str_arg_is_known_integer(node.operand)
        )
    if isinstance(node, A.BinaryOp):
        if node.op not in {
            "+",
            "plus",
            "-",
            "minus",
            "*",
            "%",
            "/",
            "//",
            "mod",
        }:
            return False
        return self._str_arg_is_known_integer(
            node.left
        ) and self._str_arg_is_known_integer(node.right)
    return False


def _virtual_concat_numeric_arg(self, arg: A.ASTNode) -> Optional[A.ASTNode]:
    if not isinstance(arg, A.BinaryOp) or arg.op not in ("+", "plus"):
        return None
    if not isinstance(arg.left, A.StringLit):
        return None
    if not (
        isinstance(arg.right, A.Call)
        and arg.right.name == "str"
        and len(arg.right.args) == 1
    ):
        return None
    value_arg = arg_at(arg.right, 0)
    return value_arg


def _virtual_strlen_numeric_arg(self, node: A.Call) -> Optional[A.ASTNode]:
    if node.name not in ("strlen", "len") or len(node.args) != 1:
        return None
    return self._virtual_concat_numeric_arg(arg_at(node, 0))

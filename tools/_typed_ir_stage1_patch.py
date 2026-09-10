#!/usr/bin/env python3
"""Temporary staging patcher for the first typed-IR conversion slice."""

from pathlib import Path

AST_PATH = Path("source/parser/ast_expr_nodes.py")
ast_text = AST_PATH.read_text(encoding="utf-8")
old_number = '''class Number(ASTNode):
    value: Any  # Can be int or float
    is_float: bool
    is_long: bool
    precision: str

    def __init__(
        self, value: str, is_long: bool = False, is_float: bool = False
    ) -> None:
        if is_float:
            self.value = float(value.rstrip("fFdDqQ"))
            self.is_float = True
            # Determine precision from suffix
            suffix = value[-1].lower() if value and value[-1].isalpha() else "d"
            self.precision = suffix  # 'f', 'd', or 'q'
            self.is_long = False
        else:
            val_str = value.rstrip("lL")
            self.value = int(val_str, 0)  # auto-detect base (0x,0b,0o)
            self.is_long = is_long
            self.is_float = False
            self.precision = ""
'''
new_number = '''class Number(ASTNode):
    value: Any  # Can be int or float
    is_float: bool
    is_long: bool
    precision: str
    precision_explicit: bool

    def __init__(
        self, value: str, is_long: bool = False, is_float: bool = False
    ) -> None:
        if is_float:
            self.value = float(value.rstrip("fFdDqQ"))
            self.is_float = True
            # Keep whether the source chose a precision explicitly. Unsuffixed
            # floating literals are contextual even though their standalone
            # fallback representation remains f64.
            self.precision_explicit = bool(value and value[-1].isalpha())
            suffix = value[-1].lower() if self.precision_explicit else "d"
            self.precision = suffix  # 'f', 'd', or 'q'
            self.is_long = False
        else:
            val_str = value.rstrip("lL")
            self.value = int(val_str, 0)  # auto-detect base (0x,0b,0o)
            self.is_long = is_long
            self.is_float = False
            self.precision = ""
            self.precision_explicit = False
'''
if old_number not in ast_text:
    raise SystemExit("Number AST anchor not found")
ast_text = ast_text.replace(old_number, new_number, 1)
AST_PATH.write_text(ast_text, encoding="utf-8")

PATH = Path("source/parser/return_type_inference.py")
text = PATH.read_text(encoding="utf-8")

old_import = "from typing import Any\n\nfrom . import ast as A\n"
new_import = (
    "from typing import Any\n\n"
    "from type_semantics import (\n"
    "    ConversionKind,\n"
    "    canonical_type_name,\n"
    "    classify_conversion,\n"
    "    is_implicit_conversion,\n"
    ")\n\n"
    "from . import ast as A\n"
)
if old_import not in text:
    raise SystemExit("return inference import anchor not found")
text = text.replace(old_import, new_import, 1)

old_canon = '''def _canon(t: Any) -> str:
    if t is None:
        return ""
    s = parsed_type_to_str(t) if not isinstance(t, str) else t
    aliases = {
        "int": "i64",
        "uint": "u64",
        "float": "f32",
        "double": "f64",
        "quad": "f128",
        "pointer": "ptr",
    }
    return aliases.get(s.lower(), s)
'''
new_canon = '''def _canon(t: Any) -> str:
    if t is None:
        return ""
    s = parsed_type_to_str(t) if not isinstance(t, str) else t
    return canonical_type_name(s)
'''
if old_canon not in text:
    raise SystemExit("_canon anchor not found")
text = text.replace(old_canon, new_canon, 1)

old_binary = '''    if isinstance(expr, A.BinaryOp):
        if expr.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or", "&&", "||"):
            return "bool"
        lt = _expr_type(
            expr.left, env, fn_returns, class_fields, class_methods, current_class
        )
        rt = _expr_type(
            expr.right, env, fn_returns, class_fields, class_methods, current_class
        )
        if expr.op == "+" and (lt == "string" or rt == "string"):
            return "string"
        return _join(lt or "", rt or "")
'''
new_binary = '''    if isinstance(expr, A.BinaryOp):
        if expr.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or", "&&", "||"):
            return "bool"
        lt = _expr_type(
            expr.left, env, fn_returns, class_fields, class_methods, current_class
        )
        rt = _expr_type(
            expr.right, env, fn_returns, class_fields, class_methods, current_class
        )
        # An unsuffixed floating literal is contextual. `float x; x / 2.0`
        # stays f32, while `x / 2.0d` deliberately introduces f64. Preserve
        # the distinction in the AST so typed IR can make it explicit later.
        floats = {"f32", "f64", "f128"}
        if (
            isinstance(expr.left, A.Number)
            and expr.left.is_float
            and not getattr(expr.left, "precision_explicit", True)
            and rt in floats
        ):
            lt = rt
        if (
            isinstance(expr.right, A.Number)
            and expr.right.is_float
            and not getattr(expr.right, "precision_explicit", True)
            and lt in floats
        ):
            rt = lt
        if expr.op == "+" and (lt == "string" or rt == "string"):
            return "string"
        return _join(lt or "", rt or "")
'''
if old_binary not in text:
    raise SystemExit("binary expression type anchor not found")
text = text.replace(old_binary, new_binary, 1)

marker = "def validate_return_contracts(program: list[A.ASTNode]) -> None:\n"
if marker not in text:
    raise SystemExit("validate_return_contracts anchor not found")
prefix = text.split(marker, 1)[0]
replacement = '''def validate_return_contracts(program: list[A.ASTNode]) -> None:
    """Enforce control-flow and type contracts at every return boundary.

    The parser owns the language-level decision about whether a returned value
    may flow into the declared function result. Backends therefore receive a
    resolved contract instead of silently inventing narrowing conversions.
    """
    functions: list[tuple[A.Function, str | None]] = []
    class_fields: dict[str, dict[str, str]] = {}
    for node in program:
        if isinstance(node, A.Function):
            functions.append((node, None))
        elif isinstance(node, A.ClassDef):
            class_fields[node.name] = {f[1]: _canon(f[2]) for f in node.fields}
            functions.extend((method, node.name) for method in node.methods)
        elif isinstance(node, A.RecordDef):
            class_fields[node.name] = {name: _canon(t) for name, t in node.fields}

    fn_returns: dict[str, str] = {}
    class_methods: dict[tuple[str, str], str] = {}
    for fn, cls in functions:
        resolved = _canon(fn.return_type)
        if cls:
            class_methods[(cls, fn.name)] = resolved
        else:
            fn_returns[fn.name] = resolved

    defer_unresolved = _has_unresolved_language_imports(program)
    for fn, cls in functions:
        if (
            defer_unresolved
            and not getattr(fn, "return_type_explicit", True)
            and not getattr(fn, "return_type_inferred", False)
        ):
            continue
        ret_type = _canon(fn.return_type)
        rs = _returns(fn)
        valued = [r for r in rs if r.value is not None]
        bare = [r for r in rs if r.value is None]
        display = f"{cls}.{fn.name}" if cls else fn.name

        if ret_type == "void":
            if valued:
                raise SyntaxError(
                    f"Void function '{display}' cannot return a value; "
                    "use bare return only for an early exit"
                )
            continue

        if bare:
            raise SyntaxError(
                f"Non-void function '{display}' cannot use bare return; "
                f"it must return a value compatible with {ret_type}"
            )
        if not _body_terminates_with_value_or_throw(fn.body):
            raise SyntaxError(
                f"Non-void function '{display}' does not return a value or throw "
                "on every control-flow path"
            )

        env = _collect_env(fn, cls, fn_returns, class_fields, class_methods)
        for ret in valued:
            if ret.value is None:
                continue
            source_type = _expr_type(
                ret.value, env, fn_returns, class_fields, class_methods, cls
            )
            if not source_type:
                # Some expression families are still resolved after module or
                # backend-specific analysis. Stage 1 rejects only conversions
                # whose source type the frontend can establish here.
                continue
            conversion = classify_conversion(source_type, ret_type)
            if is_implicit_conversion(conversion):
                continue
            if conversion is ConversionKind.EXPLICIT_LOSSY:
                raise SyntaxError(
                    f"Return in function '{display}' requires explicit lossy "
                    f"conversion from {source_type} to {ret_type}"
                )
            raise SyntaxError(
                f"Return in function '{display}' cannot convert "
                f"{source_type} to {ret_type}"
            )
'''
PATH.write_text(prefix + replacement, encoding="utf-8")

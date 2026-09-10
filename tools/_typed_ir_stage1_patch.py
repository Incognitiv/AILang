#!/usr/bin/env python3
"""Temporary staging patcher for the first typed-IR conversion slice."""

from pathlib import Path

AST_PATH = Path("source/parser/ast_expr_nodes.py")
ast_text = AST_PATH.read_text(encoding="utf-8")
old_number = '''class Number(ASTNode):\n    value: Any  # Can be int or float\n    is_float: bool\n    is_long: bool\n    precision: str\n\n    def __init__(\n        self, value: str, is_long: bool = False, is_float: bool = False\n    ) -> None:\n        if is_float:\n            self.value = float(value.rstrip("fFdDqQ"))\n            self.is_float = True\n            # Determine precision from suffix\n            suffix = value[-1].lower() if value and value[-1].isalpha() else "d"\n            self.precision = suffix  # 'f', 'd', or 'q'\n            self.is_long = False\n        else:\n            val_str = value.rstrip("lL")\n            self.value = int(val_str, 0)  # auto-detect base (0x,0b,0o)\n            self.is_long = is_long\n            self.is_float = False\n            self.precision = ""\n'''
new_number = '''class Number(ASTNode):\n    value: Any  # Can be int or float\n    is_float: bool\n    is_long: bool\n    precision: str\n    precision_explicit: bool\n\n    def __init__(\n        self, value: str, is_long: bool = False, is_float: bool = False\n    ) -> None:\n        if is_float:\n            self.value = float(value.rstrip("fFdDqQ"))\n            self.is_float = True\n            # Keep whether the source chose a precision explicitly. Unsuffixed\n            # floating literals are contextual even though their standalone\n            # fallback representation remains f64.\n            self.precision_explicit = bool(value and value[-1].isalpha())\n            suffix = value[-1].lower() if self.precision_explicit else "d"\n            self.precision = suffix  # 'f', 'd', or 'q'\n            self.is_long = False\n        else:\n            val_str = value.rstrip("lL")\n            self.value = int(val_str, 0)  # auto-detect base (0x,0b,0o)\n            self.is_long = is_long\n            self.is_float = False\n            self.precision = ""\n            self.precision_explicit = False\n'''
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

old_canon = '''def _canon(t: Any) -> str:\n    if t is None:\n        return ""\n    s = parsed_type_to_str(t) if not isinstance(t, str) else t\n    aliases = {\n        "int": "i64",\n        "uint": "u64",\n        "float": "f32",\n        "double": "f64",\n        "quad": "f128",\n        "pointer": "ptr",\n    }\n    return aliases.get(s.lower(), s)\n'''
new_canon = '''def _canon(t: Any) -> str:\n    if t is None:\n        return ""\n    s = parsed_type_to_str(t) if not isinstance(t, str) else t\n    return canonical_type_name(s)\n'''
if old_canon not in text:
    raise SystemExit("_canon anchor not found")
text = text.replace(old_canon, new_canon, 1)

old_binary = '''    if isinstance(expr, A.BinaryOp):\n        if expr.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or", "&&", "||"):\n            return "bool"\n        lt = _expr_type(\n            expr.left, env, fn_returns, class_fields, class_methods, current_class\n        )\n        rt = _expr_type(\n            expr.right, env, fn_returns, class_fields, class_methods, current_class\n        )\n        if expr.op == "+" and (lt == "string" or rt == "string"):\n            return "string"\n        return _join(lt or "", rt or "")\n'''
new_binary = '''    if isinstance(expr, A.BinaryOp):\n        if expr.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or", "&&", "||"):\n            return "bool"\n        lt = _expr_type(\n            expr.left, env, fn_returns, class_fields, class_methods, current_class\n        )\n        rt = _expr_type(\n            expr.right, env, fn_returns, class_fields, class_methods, current_class\n        )\n        # An unsuffixed floating literal is contextual. `float x; x / 2.0`\n        # stays f32, while `x / 2.0d` deliberately introduces f64. Preserve\n        # the distinction in the AST so typed IR can make it explicit later.\n        floats = set(_FLOAT_PRECISION)\n        if (\n            isinstance(expr.left, A.Number)\n            and expr.left.is_float\n            and not getattr(expr.left, "precision_explicit", True)\n            and rt in floats\n        ):\n            lt = rt\n        if (\n            isinstance(expr.right, A.Number)\n            and expr.right.is_float\n            and not getattr(expr.right, "precision_explicit", True)\n            and lt in floats\n        ):\n            rt = lt\n        if expr.op == "+" and (lt == "string" or rt == "string"):\n            return "string"\n        return _join(lt or "", rt or "")\n'''
if old_binary not in text:
    raise SystemExit("binary expression type anchor not found")
text = text.replace(old_binary, new_binary, 1)

marker = "def validate_return_contracts(program: list[A.ASTNode]) -> None:\n"
if marker not in text:
    raise SystemExit("validate_return_contracts anchor not found")
prefix = text.split(marker, 1)[0]
replacement = '''def validate_return_contracts(program: list[A.ASTNode]) -> None:\n    """Enforce control-flow and type contracts at every return boundary.\n\n    The parser owns the language-level decision about whether a returned value\n    may flow into the declared function result. Backends therefore receive a\n    resolved contract instead of silently inventing narrowing conversions.\n    """\n    functions: list[tuple[A.Function, str | None]] = []\n    class_fields: dict[str, dict[str, str]] = {}\n    for node in program:\n        if isinstance(node, A.Function):\n            functions.append((node, None))\n        elif isinstance(node, A.ClassDef):\n            class_fields[node.name] = {f[1]: _canon(f[2]) for f in node.fields}\n            functions.extend((method, node.name) for method in node.methods)\n        elif isinstance(node, A.RecordDef):\n            class_fields[node.name] = {name: _canon(t) for name, t in node.fields}\n\n    fn_returns: dict[str, str] = {}\n    class_methods: dict[tuple[str, str], str] = {}\n    for fn, cls in functions:\n        resolved = _canon(fn.return_type)\n        if cls:\n            class_methods[(cls, fn.name)] = resolved\n        else:\n            fn_returns[fn.name] = resolved\n\n    defer_unresolved = _has_unresolved_language_imports(program)\n    for fn, cls in functions:\n        if (\n            defer_unresolved\n            and not getattr(fn, "return_type_explicit", True)\n            and not getattr(fn, "return_type_inferred", False)\n        ):\n            continue\n        ret_type = _canon(fn.return_type)\n        rs = _returns(fn)\n        valued = [r for r in rs if r.value is not None]\n        bare = [r for r in rs if r.value is None]\n        display = f"{cls}.{fn.name}" if cls else fn.name\n\n        if ret_type == "void":\n            if valued:\n                raise SyntaxError(\n                    f"Void function '{display}' cannot return a value; "\n                    "use bare return only for an early exit"\n                )\n            continue\n\n        if bare:\n            raise SyntaxError(\n                f"Non-void function '{display}' cannot use bare return; "\n                f"it must return a value compatible with {ret_type}"\n            )\n        if not _body_terminates_with_value_or_throw(fn.body):\n            raise SyntaxError(\n                f"Non-void function '{display}' does not return a value or throw "\n                "on every control-flow path"\n            )\n\n        env = _collect_env(fn, cls, fn_returns, class_fields, class_methods)\n        for ret in valued:\n            if ret.value is None:\n                continue\n            source_type = _expr_type(\n                ret.value, env, fn_returns, class_fields, class_methods, cls\n            )\n            if not source_type:\n                # Some expression families are still resolved after module or\n                # backend-specific analysis. Stage 1 rejects only conversions\n                # whose source type the frontend can establish here.\n                continue\n            conversion = classify_conversion(source_type, ret_type)\n            if is_implicit_conversion(conversion):\n                continue\n            if conversion is ConversionKind.EXPLICIT_LOSSY:\n                raise SyntaxError(\n                    f"Return in function '{display}' requires explicit lossy "\n                    f"conversion from {source_type} to {ret_type}"\n                )\n            raise SyntaxError(\n                f"Return in function '{display}' cannot convert "\n                f"{source_type} to {ret_type}"\n            )\n'''
PATH.write_text(prefix + replacement, encoding="utf-8")

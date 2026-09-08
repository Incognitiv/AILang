"""Whole-program return-type inference for unannotated ``def`` functions.

The rule is intentionally conservative: inference must preserve values, never
pick a type merely because a backend can cast to it.  Explicit type-prefix or
postfix annotations remain contracts and are not changed here.
"""

from __future__ import annotations

import re
from typing import Any

from . import ast as A
from .ast import parsed_type_to_str

_INT_RE = re.compile(r"^([iu])(8|16|32|64|128|256|512|1024|2048|4096|8192)$")
_INT_WIDTHS = (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)

_BUILTIN_RETURNS = {
    "strlen": "i64",
    "len": "i64",
    "ord": "i64",
    "char_at": "i64",
    "unsafe_char_at": "i64",
    "argc": "i64",
    "time_ns": "i64",
    "clock_ns": "i64",
    "int": "i64",
    "abs": "i64",
    "min": "i64",
    "max": "i64",
    "popcount": "i64",
    "startswith": "bool",
    "endswith": "bool",
    "file_exists": "i64",
    "str": "string",
    "hex": "string",
    "bin": "string",
    "oct": "string",
    "chr": "string",
    "substr": "string",
    "concat": "string",
    "read_file": "string",
    "read_stdin": "string",
    "input": "string",
    "argv": "string",
    "getenv": "string",
    "float": "f64",
    "sqrt": "f64",
    "pow": "f64",
    "ptr_add": "ptr",
    "ptr_sub": "ptr",
}


def _canon(t: Any) -> str:
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


def _join_ints(a: str, b: str) -> str | None:
    ma, mb = _INT_RE.match(a), _INT_RE.match(b)
    if not ma or not mb:
        return None
    sa, wa = ma.group(1), int(ma.group(2))
    sb, wb = mb.group(1), int(mb.group(2))
    if sa == sb:
        return f"{sa}{max(wa, wb)}"
    signed_w = wa if sa == "i" else wb
    unsigned_w = wa if sa == "u" else wb
    needed = max(signed_w, unsigned_w + 1)
    width = next((w for w in _INT_WIDTHS if w >= needed), None)
    return f"i{width}" if width is not None else None


def _join(a: str, b: str) -> str | None:
    a, b = _canon(a), _canon(b)
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    ints = _join_ints(a, b)
    if ints:
        return ints
    floats = {"f32": 24, "f64": 53, "f128": 113}
    if a in floats and b in floats:
        return max((a, b), key=lambda t: floats[t])
    # Mixed integer/float inference is allowed only when every value in the
    # integer type is exactly representable by that float type.
    if a in floats and _INT_RE.match(b):
        m = _INT_RE.match(b)
        assert m
        bits = int(m.group(2)) - (1 if m.group(1) == "i" else 0)
        return a if bits <= floats[a] else None
    if b in floats and _INT_RE.match(a):
        return _join(b, a)
    return None


def _walk_nodes(value: Any):
    if isinstance(value, A.ASTNode):
        yield value
        for child in vars(value).values():
            yield from _walk_nodes(child)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_nodes(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_nodes(item)


def _collect_env(
    fn: A.Function,
    class_name: str | None,
    fn_returns: dict[str, str],
    class_fields: dict[str, dict[str, str]],
    class_methods: dict[tuple[str, str], str],
) -> dict[str, str]:
    env = {p[0]: _canon(p[1]) for p in fn.params}
    if class_name:
        env["this"] = class_name
    nodes = list(_walk_nodes(fn.body))
    for node in nodes:
        if isinstance(node, A.VarDecl):
            env[node.var_name] = _canon(node.type_name)
        elif isinstance(node, A.RangeVarDecl):
            # Range-constrained scalar declarations currently use the integer
            # domain; range facts refine values but not the storage family.
            env[node.var_name] = "i64"
    # Untyped assignment syntax is common in AILang.  Seed obvious local types
    # from their first assignment; later assignments do not silently widen a
    # variable's established language type.
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if not isinstance(node, A.Assign) or node.var_name in env:
                continue
            value = node.value
            inferred = None
            if isinstance(value, A.Number):
                inferred = (
                    "i64"
                    if not value.is_float
                    else {"f": "f32", "q": "f128"}.get(value.precision, "f64")
                )
            elif isinstance(value, A.Bool):
                inferred = "bool"
            elif isinstance(value, (A.StringLit, A.InterpolatedString, A.StringSlice)):
                inferred = "string"
            elif isinstance(value, A.Variable):
                inferred = env.get(value.name)
            elif isinstance(value, (A.Cast, A.ReinterpretCast)):
                inferred = _canon(value.target_type)
            elif isinstance(value, A.NewExpr):
                inferred = value.type_name
            else:
                inferred = _expr_type(
                    value,
                    env,
                    fn_returns,
                    class_fields,
                    class_methods,
                    class_name,
                )
            if inferred:
                env[node.var_name] = inferred
                changed = True
    return env


def _expr_type(
    expr: A.ASTNode,
    env: dict[str, str],
    fn_returns: dict[str, str],
    class_fields: dict[str, dict[str, str]],
    class_methods: dict[tuple[str, str], str],
    current_class: str | None,
) -> str | None:
    if isinstance(expr, A.Number):
        if not expr.is_float:
            return "i64"
        return {"f": "f32", "q": "f128"}.get(expr.precision, "f64")
    if isinstance(expr, A.Bool):
        return "bool"
    if isinstance(expr, (A.StringLit, A.InterpolatedString, A.StringSlice)):
        return "string"
    if isinstance(expr, A.Null):
        return "ptr"
    if isinstance(expr, A.Variable):
        return env.get(expr.name)
    if isinstance(expr, A.ThisExpr):
        return current_class
    if isinstance(expr, (A.Cast, A.ReinterpretCast)):
        return _canon(expr.target_type)
    if isinstance(expr, A.NewExpr):
        return expr.type_name
    if isinstance(expr, A.UnaryOp):
        if expr.op in ("not", "!"):
            return "bool"
        return _expr_type(
            expr.operand, env, fn_returns, class_fields, class_methods, current_class
        )
    if isinstance(expr, A.BinaryOp):
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
    if isinstance(expr, A.TernaryOp):
        return _join(
            _expr_type(
                expr.true_expr,
                env,
                fn_returns,
                class_fields,
                class_methods,
                current_class,
            )
            or "",
            _expr_type(
                expr.false_expr,
                env,
                fn_returns,
                class_fields,
                class_methods,
                current_class,
            )
            or "",
        )
    if isinstance(expr, A.Call):
        return fn_returns.get(expr.name) or _BUILTIN_RETURNS.get(expr.name)
    if isinstance(expr, A.FieldAccess):
        ot = _expr_type(
            expr.object_expr,
            env,
            fn_returns,
            class_fields,
            class_methods,
            current_class,
        )
        if ot in class_fields:
            return class_fields[ot].get(expr.field_name)
    if isinstance(expr, A.MethodCall):
        ot = _expr_type(
            expr.object_expr,
            env,
            fn_returns,
            class_fields,
            class_methods,
            current_class,
        )
        if ot:
            return class_methods.get((ot, expr.method_name))
    if isinstance(expr, A.ArrayAccess):
        at = _expr_type(
            expr.array, env, fn_returns, class_fields, class_methods, current_class
        )
        # textual fallback for parser-level aliases is intentionally conservative.
        m = re.match(r"^(?:slice|view)\[(.+)\]$", at or "")
        if m:
            return _canon(m.group(1))
    return None


def _returns(fn: A.Function) -> list[A.Return]:
    return [n for n in _walk_nodes(fn.body) if isinstance(n, A.Return)]


def _stmt_terminates_with_value_or_throw(stmt: A.ASTNode) -> bool:
    if isinstance(stmt, A.Return):
        return stmt.value is not None
    if isinstance(stmt, A.Throw):
        return True
    if isinstance(stmt, (A.If, A.ComptimeIf)):
        return (
            bool(stmt.else_body)
            and _body_terminates_with_value_or_throw(stmt.then_body)
            and _body_terminates_with_value_or_throw(stmt.else_body)
        )
    if isinstance(stmt, A.Match):
        return (
            bool(stmt.default_case)
            and all(
                _body_terminates_with_value_or_throw(body) for _, body in stmt.cases
            )
            and _body_terminates_with_value_or_throw(stmt.default_case or [])
        )
    if isinstance(stmt, A.TryExcept):
        # A returning/throwing finally dominates every other path. Otherwise
        # require the try body and every present handler to terminate.
        if stmt.finally_block and _body_terminates_with_value_or_throw(
            stmt.finally_block
        ):
            return True
        handlers = [body for _, _, body in stmt.catch_blocks]
        if stmt.except_block is not None:
            handlers.append(stmt.except_block[1])
        return (
            bool(handlers)
            and _body_terminates_with_value_or_throw(stmt.try_body)
            and all(_body_terminates_with_value_or_throw(body) for body in handlers)
        )
    return False


def _body_terminates_with_value_or_throw(body: list[A.ASTNode]) -> bool:
    for stmt in body:
        if _stmt_terminates_with_value_or_throw(stmt):
            return True
    return False


def body_terminates_with_value_or_throw(body: list[A.ASTNode]) -> bool:
    """Return whether every path through *body* terminates with a value or throw.

    This is shared with code generation so parser validation and backend
    fallthrough handling use the same control-flow contract.
    """
    return _body_terminates_with_value_or_throw(body)


def infer_unannotated_return_types(program: list[A.ASTNode]) -> None:
    """Resolve unannotated ``def`` return types in place; raise on ambiguity."""
    functions: list[tuple[A.Function, str | None]] = []
    class_fields: dict[str, dict[str, str]] = {}
    for node in program:
        if isinstance(node, A.Function):
            functions.append((node, None))
        elif isinstance(node, A.ClassDef):
            class_fields[node.name] = {f[1]: _canon(f[2]) for f in node.fields}
            functions.extend((m, node.name) for m in node.methods)
        elif isinstance(node, A.RecordDef):
            class_fields[node.name] = {name: _canon(t) for name, t in node.fields}

    fn_returns: dict[str, str] = {}
    class_methods: dict[tuple[str, str], str] = {}
    for fn, cls in functions:
        if getattr(fn, "return_type_explicit", True):
            if cls:
                class_methods[(cls, fn.name)] = _canon(fn.return_type)
            else:
                fn_returns[fn.name] = _canon(fn.return_type)

    pending = [
        (fn, cls)
        for fn, cls in functions
        if not getattr(fn, "return_type_explicit", True)
    ]
    for _ in range(max(1, len(pending) + 1)):
        changed = False
        for fn, cls in list(pending):
            rs = _returns(fn)
            valued = [r for r in rs if r.value is not None]
            bare = [r for r in rs if r.value is None]
            if not valued:
                # No value ever leaves the function: this is void.  A bare
                # return, when present, is only an early control-flow exit.
                inferred = "void"
            elif bare:
                raise SyntaxError(
                    f"Cannot infer return type for function '{fn.name}': it mixes value returns with bare return"
                )
            else:
                env = _collect_env(fn, cls, fn_returns, class_fields, class_methods)
                inferred = ""
                unresolved = False
                for ret in valued:
                    assert ret.value is not None
                    rt = _expr_type(
                        ret.value, env, fn_returns, class_fields, class_methods, cls
                    )
                    if not rt:
                        unresolved = True
                        continue
                    joined = _join(inferred, rt)
                    if joined is None:
                        raise SyntaxError(
                            f"Cannot infer one lossless return type for function '{fn.name}' from '{inferred}' and '{rt}'; add an explicit type prefix"
                        )
                    inferred = joined
                if unresolved:
                    # Publish a lossless provisional type when at least one
                    # return arm is known. This lets recursive and mutually
                    # recursive calls participate in the next fixpoint round.
                    if inferred:
                        if cls:
                            key = (cls, fn.name)
                            if class_methods.get(key) != inferred:
                                class_methods[key] = inferred
                                changed = True
                        elif fn_returns.get(fn.name) != inferred:
                            fn_returns[fn.name] = inferred
                            changed = True
                    continue
            if inferred != "void" and not _body_terminates_with_value_or_throw(fn.body):
                raise SyntaxError(
                    f"Cannot infer non-void return type for function '{fn.name}': not every control-flow path returns a value or throws"
                )
            fn.return_type = inferred
            fn.return_type_inferred = True
            if cls:
                class_methods[(cls, fn.name)] = inferred
            else:
                fn_returns[fn.name] = inferred
            pending.remove((fn, cls))
            changed = True
        if not pending or not changed:
            break
    if pending:
        names = ", ".join(f"{cls + '.' if cls else ''}{fn.name}" for fn, cls in pending)
        raise SyntaxError(
            f"Cannot infer return type for: {names}; add an explicit type prefix"
        )


def validate_return_contracts(program: list[A.ASTNode]) -> None:
    """Enforce value-vs-void return semantics for every concrete function.

    A bare ``return`` is only an early-exit from a void procedure.  It never
    supplies a value.  Conversely, a non-void function must return a value (or
    throw) on every terminating path.
    """
    functions: list[tuple[A.Function, str | None]] = []
    for node in program:
        if isinstance(node, A.Function):
            functions.append((node, None))
        elif isinstance(node, A.ClassDef):
            functions.extend((method, node.name) for method in node.methods)

    for fn, cls in functions:
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

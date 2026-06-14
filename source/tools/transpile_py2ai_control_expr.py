"""Python-to-AILang visitors for control flow and expression nodes."""

from __future__ import annotations

import ast

from ast_access import arg_at, value_at


def _visit_If(self, node: ast.If) -> str:
    lines = [f"{self._indent()}if {self._visit(node.test)} then"]
    self.indent += 1

    for stmt in node.body:
        result = self._visit(stmt)
        if result:
            lines.append(result)

    self.indent -= 1

    # Handle elif chain
    orelse = node.orelse
    while orelse:
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            elif_node = orelse[0]
            lines.append(f"{self._indent()}elsif {self._visit(elif_node.test)} then")
            self.indent += 1
            for stmt in elif_node.body:
                result = self._visit(stmt)
                if result:
                    lines.append(result)
            self.indent -= 1
            orelse = elif_node.orelse
        else:
            lines.append(f"{self._indent()}else")
            self.indent += 1
            for stmt in orelse:
                result = self._visit(stmt)
                if result:
                    lines.append(result)
            self.indent -= 1
            break

    lines.append(f"{self._indent()}end")
    return "\n".join(lines)


def _visit_While(self, node: ast.While) -> str:
    lines = [f"{self._indent()}while {self._visit(node.test)} then"]
    self.indent += 1
    for stmt in node.body:
        result = self._visit(stmt)
        if result:
            lines.append(result)
    self.indent -= 1
    lines.append(f"{self._indent()}end")
    return "\n".join(lines)


def _visit_For(self, node: ast.For) -> str:
    target = self._visit(node.target)
    iter_expr = self._visit(node.iter)

    lines = [f"{self._indent()}foreach {target} in {iter_expr} then"]
    self.indent += 1
    for stmt in node.body:
        result = self._visit(stmt)
        if result:
            lines.append(result)
    self.indent -= 1
    lines.append(f"{self._indent()}end")
    return "\n".join(lines)


def _visit_Expr(self, node: ast.Expr) -> str:
    return f"{self._indent()}{self._visit(node.value)}"


def _visit_Pass(self, node: ast.Pass) -> str:
    return ""  # AILang doesn't need pass


def _visit_Break(self, node: ast.Break) -> str:
    return f"{self._indent()}break"


def _visit_Continue(self, node: ast.Continue) -> str:
    return f"{self._indent()}continue"


def _visit_Raise(self, node: ast.Raise) -> str:
    """Convert raise to panic (AILang style)"""
    if node.exc:
        # raise SyntaxError("msg") -> panic("msg")
        if isinstance(node.exc, ast.Call):
            args = ", ".join(self._visit(a) for a in node.exc.args)
            return f"{self._indent()}panic({args})"
        return f"{self._indent()}panic({self._visit(node.exc)})"
    return f"{self._indent()}panic()"


def _visit_Try(self, node: ast.Try) -> str:
    """Convert try/except/finally to AILang try/catch/finally"""
    lines = [f"{self._indent()}try then"]
    self.indent += 1

    # Try body
    for stmt in node.body:
        result = self._visit(stmt)
        if result:
            result = result.replace("self.", "this.")
            lines.append(result)

    self.indent -= 1

    # Exception handlers (except/catch)
    for handler in node.handlers:
        if handler.type:
            exc_type = self._visit(handler.type)
            if handler.name:
                lines.append(f"{self._indent()}catch {exc_type} as {handler.name} then")
            else:
                lines.append(f"{self._indent()}catch {exc_type} then")
        else:
            lines.append(f"{self._indent()}catch then")

        self.indent += 1
        for stmt in handler.body:
            result = self._visit(stmt)
            if result:
                result = result.replace("self.", "this.")
                lines.append(result)
        self.indent -= 1

    # Else clause (runs if no exception)
    if node.orelse:
        lines.append(f"{self._indent()}else")
        self.indent += 1
        for stmt in node.orelse:
            result = self._visit(stmt)
            if result:
                result = result.replace("self.", "this.")
                lines.append(result)
        self.indent -= 1

    # Finally clause
    if node.finalbody:
        lines.append(f"{self._indent()}finally then")
        self.indent += 1
        for stmt in node.finalbody:
            result = self._visit(stmt)
            if result:
                result = result.replace("self.", "this.")
                lines.append(result)
        self.indent -= 1

    lines.append(f"{self._indent()}end")
    return "\n".join(lines)


def _visit_With(self, node: ast.With) -> str:
    """Convert with statement - AILang doesn't have with, expand it"""
    lines = []
    # For each context manager
    for item in node.items:
        ctx = self._visit(item.context_expr)
        if item.optional_vars:
            var = self._visit(item.optional_vars)
            lines.append(f"{self._indent()}{var} = {ctx}")
        else:
            lines.append(f"{self._indent()}{ctx}")

    # Body
    for stmt in node.body:
        result = self._visit(stmt)
        if result:
            result = result.replace("self.", "this.")
            lines.append(result)

    return "\n".join(lines)


def _visit_Assert(self, node: ast.Assert) -> str:
    """Convert assert to if/panic"""
    test = self._visit(node.test)
    if node.msg:
        msg = self._visit(node.msg)
        return f"{self._indent()}if not ({test}) then\n{self._indent()}    panic({msg})\n{self._indent()}end"
    return f'{self._indent()}if not ({test}) then\n{self._indent()}    panic("Assertion failed")\n{self._indent()}end'


def _visit_Global(self, node: ast.Global) -> str:
    """Global statement - comment it out, AILang handles scope differently"""
    return f"{self._indent()}// global {', '.join(node.names)}"


def _visit_Nonlocal(self, node: ast.Nonlocal) -> str:
    """Nonlocal statement - comment it out"""
    return f"{self._indent()}// nonlocal {', '.join(node.names)}"


def _visit_Lambda(self, node: ast.Lambda) -> str:
    """Convert lambda to inline function syntax"""
    args = ", ".join(arg.arg for arg in node.args.args)
    body = self._visit(node.body)
    # AILang could support: |args| -> expr
    return f"|{args}| -> {body}"


def _visit_Name(self, node: ast.Name) -> str:
    # Map Python constants
    if node.id == "True":
        return "true"
    elif node.id == "False":
        return "false"
    elif node.id == "None":
        return "0"  # null
    return self._safe_name(node.id)


def _visit_Constant(self, node: ast.Constant) -> str:
    if isinstance(node.value, str):
        escaped = (
            node.value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        )
        return f'"{escaped}"'
    elif isinstance(node.value, bool):
        return "true" if node.value else "false"
    elif node.value is None:
        return "nil"
    return str(node.value)


def _visit_JoinedStr(self, node: ast.JoinedStr) -> str:
    """Convert f-string to string concatenation"""
    parts = []
    for val in node.values:
        if isinstance(val, ast.Constant):
            # Escape special characters
            escaped = (
                str(val.value)
                .replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\t", "\\t")
                .replace("\r", "\\r")
            )
            parts.append(f'"{escaped}"')
        elif isinstance(val, ast.FormattedValue):
            # Handle format specs like {x:.2f}
            value = self._visit(val.value)
            if val.conversion == ord("s"):
                value = f"str({value})"
            elif val.conversion == ord("r"):
                value = f"repr({value})"
            parts.append(value)
    return " + ".join(parts) if parts else '""'


def _visit_Attribute(self, node: ast.Attribute) -> str:
    safe_attr = self._safe_name(node.attr)
    return f"{self._visit(node.value)}.{safe_attr}"


def _visit_Subscript(self, node: ast.Subscript) -> str:
    return f"{self._visit(node.value)}[{self._visit(node.slice)}]"


def _visit_Slice(self, node: ast.Slice) -> str:
    """Handle slice expressions like [1:5] or [::2]"""
    lower = self._visit(node.lower) if node.lower else ""
    upper = self._visit(node.upper) if node.upper else ""
    step = self._visit(node.step) if node.step else ""
    if step:
        return f"{lower}:{upper}:{step}"
    return f"{lower}:{upper}"


def _visit_IfExp(self, node: ast.IfExp) -> str:
    """Handle ternary: x if condition else y

    AILang supports both Python-style and C-style:
      - Python: value if condition else other
      - C-style: condition ? value : other

    We use C-style since it's more compact.
    """
    body = self._visit(node.body)
    test = self._visit(node.test)
    orelse = self._visit(node.orelse)
    return f"{test} ? {body} : {orelse}"


def _visit_Set(self, node: ast.Set) -> str:
    """Handle set literals {1, 2, 3}"""
    elements = ", ".join(self._visit(e) for e in node.elts)
    return f"{{{elements}}}"


def _visit_NamedExpr(self, node: ast.NamedExpr) -> str:
    """Handle walrus operator: (x := value)"""
    target = self._visit(node.target)
    value = self._visit(node.value)
    # AILang could support assignment expressions
    return f"({target} = {value})"


def _visit_Starred(self, node: ast.Starred) -> str:
    """Handle starred expressions: *args"""
    return f"*{self._visit(node.value)}"


def _visit_Call(self, node: ast.Call) -> str:
    # Check for Python string method calls: obj.method(args)
    if isinstance(node.func, ast.Attribute):
        method_name = node.func.attr
        if method_name in self.STRING_METHODS:
            ailang_func, needs_obj = self.STRING_METHODS[method_name]
            obj = self._visit(node.func.value)
            args = [self._visit(a) for a in node.args]
            all_args = [obj, *args] if needs_obj else args
            # Mark as needing AILang string stdlib
            return f"{ailang_func}({', '.join(all_args)})"

    func = self._visit(node.func)

    # Convert Python type casts to AILang casts
    # float(x) -> (float)x
    if func in self.TYPE_CASTS and len(node.args) == 1:
        arg = self._visit(arg_at(node, 0))
        return f"({func}){arg}"

    # Handle positional args
    args = [self._visit(a) for a in node.args]
    # Handle keyword args
    for kw in node.keywords:
        if kw.arg:
            args.append(f"{kw.arg}={self._visit(kw.value)}")
        else:
            # **kwargs
            args.append(f"**{self._visit(kw.value)}")
    return f"{func}({', '.join(args)})"


def _visit_ListComp(self, node: ast.ListComp) -> str:
    """Handle list comprehensions: [x for x in items]"""
    # Convert to explicit loop notation or keep as comprehension
    elt = self._visit(node.elt)
    generators = []
    for gen in node.generators:
        target = self._visit(gen.target)
        iter_expr = self._visit(gen.iter)
        gen_str = f"for {target} in {iter_expr}"
        if gen.ifs:
            conditions = " and ".join(self._visit(if_clause) for if_clause in gen.ifs)
            gen_str += f" if {conditions}"
        generators.append(gen_str)
    return f"[{elt} {' '.join(generators)}]"


def _visit_DictComp(self, node: ast.DictComp) -> str:
    """Handle dict comprehensions: {k: v for k, v in items}"""
    key = self._visit(node.key)
    value = self._visit(node.value)
    generators = []
    for gen in node.generators:
        target = self._visit(gen.target)
        iter_expr = self._visit(gen.iter)
        gen_str = f"for {target} in {iter_expr}"
        if gen.ifs:
            conditions = " and ".join(self._visit(if_clause) for if_clause in gen.ifs)
            gen_str += f" if {conditions}"
        generators.append(gen_str)
    return f"{{{key}: {value} {' '.join(generators)}}}"


def _visit_GeneratorExp(self, node: ast.GeneratorExp) -> str:
    """Handle generator expressions: (x for x in items)"""
    elt = self._visit(node.elt)
    generators = []
    for gen in node.generators:
        target = self._visit(gen.target)
        iter_expr = self._visit(gen.iter)
        gen_str = f"for {target} in {iter_expr}"
        if gen.ifs:
            conditions = " and ".join(self._visit(if_clause) for if_clause in gen.ifs)
            gen_str += f" if {conditions}"
        generators.append(gen_str)
    return f"({elt} {' '.join(generators)})"


def _visit_BinOp(self, node: ast.BinOp) -> str:
    left = self._visit(node.left)
    right = self._visit(node.right)
    op = self._binop_symbol(node.op)
    # Avoid unnecessary parens for simple expressions
    return f"{left} {op} {right}"


def _visit_UnaryOp(self, node: ast.UnaryOp) -> str:
    operand = self._visit(node.operand)
    if isinstance(node.op, ast.Not):
        return f"not {operand}"
    elif isinstance(node.op, ast.USub):
        return f"-{operand}"
    elif isinstance(node.op, ast.Invert):
        return f"bnot {operand}"
    return operand


def _visit_BoolOp(self, node: ast.BoolOp) -> str:
    # Handle Python idiom: x or default_value
    # For simplicity, just use the left value (assuming it's set properly)
    if isinstance(node.op, ast.Or) and len(node.values) == 2:
        left = value_at(node, 0)
        right = value_at(node, 1)
        # If right is a constant (like [] or ""), this is a default value pattern
        # Just use the left value since init should have set it
        if isinstance(right, (ast.List, ast.Constant, ast.Dict)):
            return self._visit(left)
    op = " and " if isinstance(node.op, ast.And) else " or "
    return op.join(self._visit(v) for v in node.values)


def _visit_Compare(self, node: ast.Compare) -> str:
    result = self._visit(node.left)
    for op, comp in zip(node.ops, node.comparators, strict=False):
        result += f" {self._cmpop_symbol(op)} {self._visit(comp)}"
    return result


def _visit_List(self, node: ast.List) -> str:
    elements = ", ".join(self._visit(e) for e in node.elts)
    return f"[{elements}]"


def _visit_Dict(self, node: ast.Dict) -> str:
    pairs = []
    for k, v in zip(node.keys, node.values, strict=False):
        if k is not None:
            pairs.append(f"{self._visit(k)}: {self._visit(v)}")
        else:
            # **kwargs unpacking - skip for now
            pairs.append(f"**{self._visit(v)}")
    return "{" + ", ".join(pairs) + "}"


def _visit_Tuple(self, node: ast.Tuple) -> str:
    elements = ", ".join(self._visit(e) for e in node.elts)
    return f"({elements})"


def _binop_symbol(self, op: ast.operator) -> str:
    ops = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.FloorDiv: "//",
        ast.Mod: "%",
        ast.Pow: "**",
        ast.LShift: "shl",
        ast.RShift: "shr",
        ast.BitOr: "bor",
        ast.BitXor: "bxor",
        ast.BitAnd: "band",
    }
    return ops.get(type(op), "+")


def _cmpop_symbol(self, op: ast.cmpop) -> str:
    ops = {
        ast.Eq: "==",
        ast.NotEq: "!=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Is: "==",
        ast.IsNot: "!=",
        ast.In: "in",
        ast.NotIn: "not in",
    }
    return ops.get(type(op), "==")

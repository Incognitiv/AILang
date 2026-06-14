"""Python-to-AILang visitors for declarations, classes, functions, and assigns."""

from __future__ import annotations

import ast

from ast_access import body_at, target_at


def _get_type_name(self, annotation: ast.AST) -> str:
    """Extract type name from annotation"""
    if isinstance(annotation, ast.Name):
        name = annotation.id
        # Map Python types to AILang types
        if name in ("Any", "ASTNode", "list"):
            return "int"  # Use int for Any/ASTNode/list (simpler codegen)
        # Map AST class names to int (they're stored as pointers)
        if name in self.AST_CLASS_NAMES:
            return "int"
        return name
    elif isinstance(annotation, ast.Subscript):
        # List[int] -> int (AILang classes can't have array typed fields easily)
        base = self._get_type_name(annotation.value)
        if base == "List":
            return "int"  # Simplify to int
        elif base == "Optional":
            return self._get_type_name(annotation.slice)
        return "int"  # Default to int for complex types
    elif isinstance(annotation, ast.Constant):
        return str(annotation.value) if annotation.value else "void"
    elif isinstance(annotation, ast.Attribute):
        # Handle qualified names like typing.Optional
        return "int"
    return "int"  # Default to int instead of Any


def _visit_Module(self, node: ast.Module) -> str:
    lines = []
    first_item = True

    # Check if this is a library module (has classes/functions but no main)
    has_classes = any(isinstance(item, ast.ClassDef) for item in node.body)
    has_functions = any(isinstance(item, ast.FunctionDef) for item in node.body)
    has_main = any(
        isinstance(item, ast.FunctionDef) and item.name == "main" for item in node.body
    )

    # Add @library declaration for library modules (classes or functions, but no main)
    if (has_classes or has_functions) and not has_main:
        # Extract module name from docstring or use "module"
        module_name = "module"
        if (node.body and isinstance(body_at(node, 0), ast.Expr)) and isinstance(
            body_at(node, 0).value, ast.Constant
        ):
            doc = str(body_at(node, 0).value.value)
            # Try to extract name from docstring like "AILang AST - ..."
            if " - " in doc:
                module_name = doc.split(" - ")[0].strip().lower().replace(" ", "_")
            elif doc:
                module_name = doc.split()[0].lower()
        lines.append(f'@library("{module_name}")')
        lines.append("")

    for item in node.body:
        # Convert module-level docstrings to comments
        if (
            first_item
            and isinstance(item, ast.Expr)
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        ):
            docstring = item.value.value.strip()
            for doc_line in docstring.split("\n"):
                doc_line = doc_line.strip()
                if doc_line:
                    lines.append(f"// {doc_line}")
            first_item = False
            continue
        first_item = False
        result = self._visit(item)
        if result:
            lines.append(result)
    return "\n\n".join(lines)


def _visit_Import(self, node: ast.Import) -> str:
    """Convert import statements to use statements"""
    parts = []
    for alias in node.names:
        module_base = alias.name.split(".")[0]
        # Skip Python-specific modules
        if module_base in self.SKIP_IMPORTS or alias.name in self.SKIP_IMPORTS:
            parts.append(f"// skip: import {alias.name}")
            continue
        # Map to AILang equivalent
        mapped = self.MODULE_MAP.get(alias.name, alias.name)
        if alias.asname:
            parts.append(f"use {mapped} as {alias.asname}")
        else:
            parts.append(f"use {mapped}")
    return "\n".join(f"{self._indent()}{p}" for p in parts) if parts else ""


def _visit_ImportFrom(self, node: ast.ImportFrom) -> str:
    """Convert from...import to use"""
    module = node.module or ""
    module_base = module.split(".")[0]
    # Skip Python-specific modules
    if module_base in self.SKIP_IMPORTS or module in self.SKIP_IMPORTS:
        return f"{self._indent()}// skip: from {module} import ..."
    # Map to AILang equivalent
    mapped = self.MODULE_MAP.get(module, module)
    names = [a.asname if a.asname else a.name for a in node.names if a.name != "*"]
    if names:
        return f"{self._indent()}use {mapped}: {', '.join(names)}"
    return f"{self._indent()}use {mapped}"


def _visit_ClassDef(self, node: ast.ClassDef) -> str:
    # Check if class has methods beyond __init__
    methods = [
        item
        for item in node.body
        if isinstance(item, ast.FunctionDef) and item.name != "__init__"
    ]
    has_methods = len(methods) > 0

    # Find __init__
    init_method = None
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            init_method = item
            break

    # AILang distinction:
    # - record = data only (fields, no methods, constructor via new Point(x, y))
    # - class = fields + methods + init
    # If there's an init method or other methods, use class
    use_class = has_methods or (init_method and len(init_method.args.args) > 1)

    if use_class:
        lines = [f"class {node.name} then"]
    else:
        lines = [f"record {node.name} then"]

    self.indent += 1

    # Extract field types from __init__ - use recursive search for complex inits
    fields = []
    if init_method:
        # For complex inits, use recursive field finder
        if not self._is_simple_init(init_method):
            all_fields = self._find_field_assignments(init_method.body)
            for field_name, value_node in all_fields.items():
                # Try to infer type from parameter or use int
                field_type = "int"  # Default to int, not Any
                if isinstance(value_node, ast.Name):
                    param_name = value_node.id
                    for arg in init_method.args.args:
                        if arg.arg == param_name and arg.annotation:
                            field_type = self._get_type_name(arg.annotation)
                            break
                fields.append((field_type, field_name))
        else:
            # Extract fields from self.x = ... assignments (simple init)
            for stmt in init_method.body:
                if isinstance(stmt, ast.AnnAssign):
                    # self.x: type = value
                    target = stmt.target
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        field_name = target.attr
                        field_type = self._get_type_name(stmt.annotation)
                        fields.append((field_type, field_name))
                elif isinstance(stmt, ast.Assign):
                    # self.x = value (infer type from parameter)
                    for assign_target in stmt.targets:
                        if (
                            isinstance(assign_target, ast.Attribute)
                            and isinstance(assign_target.value, ast.Name)
                            and assign_target.value.id == "self"
                        ):
                            field_name = assign_target.attr
                            # Try to get type from parameter
                            field_type = "int"  # default
                            if isinstance(stmt.value, ast.Name):
                                param_name = stmt.value.id
                                # Find param type from __init__ signature
                                for arg in init_method.args.args:
                                    if arg.arg == param_name and arg.annotation:
                                        field_type = self._get_type_name(arg.annotation)
                                        break
                            fields.append((field_type, field_name))

    # Output fields with proper default values
    for field_type, field_name in fields:
        # Determine appropriate default value based on type
        default_val = self._get_default_for_type(field_type)
        safe_field_name = self._safe_name(field_name)
        if use_class:
            # Class syntax: public int x = 0
            lines.append(
                f"{self._indent()}public {field_type} {safe_field_name} = {default_val}"
            )
        else:
            # Record syntax: int x
            lines.append(f"{self._indent()}{field_type} {safe_field_name}")

    if fields and (has_methods or use_class):
        lines.append("")  # Blank line between fields and methods

    # Output init method for classes (records use positional constructor)
    if use_class and init_method:
        lines.append(self._visit_init_method(init_method))

    # Output other methods
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name != "__init__":
            lines.append("")
            lines.append(self._visit(item))

    self.indent -= 1
    lines.append("end")

    return "\n".join(lines)


def _get_default_for_type(self, type_name: str) -> str:
    """Get appropriate default value for a type"""
    numeric_types = {"int", "float", "double", "long", "short", "bool"}
    if type_name.lower() in numeric_types:
        return "0"
    if type_name == "str" or type_name == "string":
        return '""'
    if type_name.endswith("[]"):
        return "[]"
    # For Any, Optional, objects, etc. use 0 (null pointer)
    return "0"


def _is_simple_init(self, node: ast.FunctionDef) -> bool:
    """Check if __init__ only does simple field assignments.

    Simple init: only contains self.x = value statements where value is simple
    Complex init: contains if/for/while/try or method calls or subscripts
    """
    for stmt in node.body:
        # Annotated assignment: self.x: T = value
        if isinstance(stmt, ast.AnnAssign):
            if not (
                isinstance(stmt.target, ast.Attribute)
                and isinstance(stmt.target.value, ast.Name)
                and stmt.target.value.id == "self"
            ):
                return False
            # Check if value contains method calls or complex expressions
            if stmt.value and self._has_complex_expr(stmt.value):
                return False
            continue
        # Simple assignment: self.x = value
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    return False
            # Check if value contains method calls or complex expressions
            if self._has_complex_expr(stmt.value):
                return False
            continue
        # If statement - complex logic
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try)):
            return False
        # Expression statement (like docstring) - ok
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        # Anything else is complex
        return False
    return True


def _has_complex_expr(self, node: ast.AST) -> bool:
    """Check if expression contains method calls, subscripts, or other complex patterns"""
    for child in ast.walk(node):
        # Method call like x.method()
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            return True
        # Subscript like x[1:-1]
        if isinstance(child, ast.Subscript):
            return True
        # Binary ops with non-simple operands (chained calls)
        if isinstance(child, ast.BinOp):
            if isinstance(child.left, (ast.Call, ast.Subscript)):
                return True
            if isinstance(child.right, (ast.Call, ast.Subscript)):
                return True
    return False


def _visit_init_method(self, node: ast.FunctionDef) -> str:
    """Convert __init__ to AILang class init()"""
    # Build params with default values
    params = []
    defaults = node.args.defaults
    num_defaults = len(defaults)
    args = [arg for arg in node.args.args if arg.arg != "self"]

    for i, arg in enumerate(args):
        param_name = arg.arg
        # Check if this param has a default value
        default_idx = i - (len(args) - num_defaults)
        if default_idx >= 0 and default_idx < num_defaults:
            default_val = self._visit(defaults[default_idx])
            # Convert None to nil
            if default_val == "None":
                default_val = "0"  # null
            params.append(f"{self._safe_name(param_name)} = {default_val}")
        else:
            params.append(self._safe_name(param_name))

    params_str = ", ".join(params)

    # Class constructors need public def init
    lines = [f"{self._indent()}public def init({params_str}):"]

    self.indent += 1

    # If init has complex logic, add comment and simplified version
    if not self._is_simple_init(node):
        lines.append(f"{self._indent()}// NOTE: Complex init - may need manual review")
        # Find all field assignments recursively
        fields_assigned = self._find_field_assignments(node.body)
        for field, value_node in fields_assigned.items():
            safe_field = self._safe_name(field)
            if isinstance(value_node, ast.Name):
                # Direct parameter assignment
                safe_val = self._safe_name(value_node.id)
                lines.append(f"{self._indent()}this.{safe_field} = {safe_val}")
            else:
                # Complex expression - assign param if same name, else nil
                if field in [a.arg for a in node.args.args]:
                    lines.append(f"{self._indent()}this.{safe_field} = {safe_field}")
                else:
                    lines.append(
                        f"{self._indent()}this.{safe_field} = 0  // simplified (null)"
                    )
    else:
        for stmt in node.body:
            # Convert self.x = param to this.x = param
            result = self._visit(stmt)
            if result:
                # Replace self. with this.
                result = result.replace("self.", "this.")
                lines.append(result)

    self.indent -= 1
    lines.append(f"{self._indent()}end")
    return "\n".join(lines)


def _find_field_assignments(self, body: list[ast.stmt]) -> dict[str, ast.expr | None]:
    """Recursively find all self.field = value assignments"""
    fields: dict[str, ast.expr | None] = {}
    for stmt in body:
        if isinstance(stmt, ast.AnnAssign):
            if (
                isinstance(stmt.target, ast.Attribute)
                and isinstance(stmt.target.value, ast.Name)
                and stmt.target.value.id == "self"
            ):
                fields[stmt.target.attr] = stmt.value
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    fields[target.attr] = stmt.value
        elif isinstance(stmt, ast.If):
            # Check both branches
            fields.update(self._find_field_assignments(stmt.body))
            fields.update(self._find_field_assignments(stmt.orelse))
        elif isinstance(stmt, (ast.For, ast.While)):
            fields.update(self._find_field_assignments(stmt.body))
    return fields


def _visit_FunctionDef(self, node: ast.FunctionDef) -> str:
    # AILang function: def name(params):
    # Convert 'self' to 'this' in parameter list
    params = []
    for arg in node.args.args:
        if arg.arg == "self":
            params.append("this")
        else:
            params.append(arg.arg)
    params_str = ", ".join(params)

    # AILang doesn't use return type annotations - clean signature
    header = f"{self._indent()}def {node.name}({params_str}):"

    lines = [header]
    self.indent += 1

    # Function body
    first_stmt = True
    for stmt in node.body:
        # Convert docstrings to // comments
        if (
            isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
        ) and isinstance(stmt.value.value, str):
            if first_stmt:
                # Convert docstring to comment
                docstring = stmt.value.value.strip()
                for doc_line in docstring.split("\n"):
                    doc_line = doc_line.strip()
                    if doc_line:
                        lines.append(f"{self._indent()}// {doc_line}")
            first_stmt = False
            continue

        first_stmt = False
        result = self._visit(stmt)
        if result:
            # Convert self. to this. throughout
            result = result.replace("self.", "this.")
            lines.append(result)

    self.indent -= 1
    lines.append(f"{self._indent()}end")

    return "\n".join(lines)


def _visit_AnnAssign(self, node: ast.AnnAssign) -> str:
    """Annotated assignment: convert 'x: T = val' to just 'x = val'"""
    target = self._visit(node.target)
    value = self._visit(node.value) if node.value else "0"

    # AILang doesn't need type annotations - they're inferred!
    # Convert self.x to this.x for AILang
    if target.startswith("self."):
        field = target[5:]  # Remove 'self.'
        safe_field = self._safe_name(field)
        return f"{self._indent()}this.{safe_field} = {value}"
    else:
        return f"{self._indent()}{target} = {value}"


def _visit_Assign(self, node: ast.Assign) -> str:
    """Simple assignment - handles tuple unpacking cleanly"""

    # Skip type alias assignments like: ParsedType = Union[...]
    if (len(node.targets) == 1 and isinstance(target_at(node, 0), ast.Name)) and (
        isinstance(node.value, ast.Subscript)
        and (
            isinstance(node.value.value, ast.Name)
            and node.value.value.id in self.TYPE_NAMES
        )
    ):
        return ""  # Skip type alias

    # Handle tuple unpacking without extra parens
    def format_target(t: ast.AST) -> str:
        if isinstance(t, ast.Tuple):
            # No parentheses for destructuring
            return ", ".join(self._visit(e) for e in t.elts)
        return self._visit(t)

    targets = " = ".join(format_target(t) for t in node.targets)
    value = self._visit(node.value)
    return f"{self._indent()}{targets} = {value}"


def _visit_AugAssign(self, node: ast.AugAssign) -> str:
    """Augmented assignment: x += 1"""
    target = self._visit(node.target)
    op = self._binop_symbol(node.op)
    value = self._visit(node.value)
    return f"{self._indent()}{target} = {target} {op} {value}"


def _visit_Return(self, node: ast.Return) -> str:
    if node.value:
        return f"{self._indent()}return {self._visit(node.value)}"
    return f"{self._indent()}return"

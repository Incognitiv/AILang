"""Strict-extras: AST checks that ruff doesn't yet implement.

Currently covers two pylint-only rules so we can drop pylint from the
verifier without losing coverage:

- W0201 attribute-defined-outside-init: ``self.X = ...`` set in a method
  other than ``__init__`` for an X that was never bound in ``__init__``.
- W1114 arguments-out-of-order: a call site passes positional arguments
  whose names match the callee's parameter names but in a swapped order
  (e.g., ``func(b, a)`` where def is ``func(a, b)``). Heuristic; only
  fires when ALL positional arg names are also parameter names.

Both checks pure-AST, no type-inference. Sub-second on the whole source/
tree.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Tuple

from .common import read_file, validate_filepath


def _collect_init_attrs(class_node: ast.ClassDef) -> set[str]:
    """Return the set of attribute names defined for instances of this class.

    Includes:
      - class-body type annotations (``foo: int`` at class level) -- pylint
        treats these as defined
      - class-body assignments (``foo = 5`` at class level)
      - ``self.X`` assigned (or annotated) inside ``__init__``
    """
    init_attrs: set[str] = set()

    # Class-body level: annotations and bare assignments count as defined.
    for item in class_node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            init_attrs.add(item.target.id)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    init_attrs.add(target.id)

    # __init__ body: collect self.X assignments/annotations.
    for item in class_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name != "__init__":
            continue
        for sub in ast.walk(item):
            for target in _self_targets(sub):
                init_attrs.add(target.attr)
    return init_attrs


def _self_targets(node: ast.AST) -> List[ast.Attribute]:
    """Return ``self.X`` Attribute targets if ``node`` assigns to any."""
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets.append(node.target)
    out: List[ast.Attribute] = []
    for t in targets:
        if (
            isinstance(t, ast.Attribute)
            and isinstance(t.value, ast.Name)
            and t.value.id == "self"
        ):
            out.append(t)
    return out


def _is_mixin_or_protocol(class_node: ast.ClassDef) -> bool:
    """Heuristic: class is a mixin / protocol / proxy where W0201 should
    not apply.

    Skips W0201 for classes that look intentionally abstract -- where
    attributes are expected to be provided elsewhere:

    * Mixin classes (combined into another class via MRO).
    * Protocols (typing.Protocol).
    * Classes that immediately raise ``NotImplementedError`` in any
      method body.
    * **Proxy classes** -- classes that define both ``__getattr__`` and
      ``__setattr__`` to forward attribute access to a back-referenced
      object. Every ``self.X = Y`` outside ``__init__`` actually mutates
      the back-ref, not the proxy itself, so W0201 doesn't apply.

    Without cross-module inheritance graph analysis (which pylint does),
    this naming/structural heuristic catches the common patterns.
    """
    if "Mixin" in class_node.name or "Protocol" in class_node.name:
        return True
    # Proxy classes: define both __getattr__ and __setattr__.
    has_getattr = False
    has_setattr = False
    for item in class_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name == "__getattr__":
            has_getattr = True
        elif item.name == "__setattr__":
            has_setattr = True
    if has_getattr and has_setattr:
        return True
    # Any method body that immediately raises NotImplementedError marks
    # the class as abstract for our purposes.
    for item in class_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for stmt in item.body:
            if not isinstance(stmt, ast.Raise) or stmt.exc is None:
                continue
            exc = stmt.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                return True
            if (
                isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name)
                and exc.func.id == "NotImplementedError"
            ):
                return True
    return False


def _check_attribute_defined_outside_init(
    tree: ast.AST,
) -> List[Tuple[int, str]]:
    """W0201: ``self.X`` set outside ``__init__`` for X never set in ``__init__``."""
    issues: List[Tuple[int, str]] = []
    for class_node in ast.walk(tree):
        if not isinstance(class_node, ast.ClassDef):
            continue
        if _is_mixin_or_protocol(class_node):
            continue
        init_attrs = _collect_init_attrs(class_node)
        # Pylint reports every occurrence of an undeclared self.X assignment,
        # so we don't dedupe.
        for item in class_node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name == "__init__":
                continue
            for sub in ast.walk(item):
                if not isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    continue
                for target in _self_targets(sub):
                    if target.attr in init_attrs:
                        continue
                    issues.append(
                        (
                            sub.lineno,
                            f"Attribute '{target.attr}' defined outside __init__ "
                            f"(in method '{item.name}')",
                        )
                    )
    return issues


def _function_params_in_module(tree: ast.AST) -> Dict[str, List[str]]:
    """Map function/method names -> list of positional parameter names.

    For methods, the leading ``self``/``cls`` is dropped so call-site
    comparisons line up with the actual call signature.
    """
    params: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = [a.arg for a in node.args.args]
        if names and names[0] in ("self", "cls"):
            names = names[1:]
        # Last definition wins for overloaded names (pylint behavior).
        params[node.name] = names
    return params


def _check_arguments_out_of_order(tree: ast.AST) -> List[Tuple[int, str]]:
    """W1114: positional args whose names match params but in wrong positions."""
    func_params = _function_params_in_module(tree)
    issues: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Resolve the callee's "name" as either bare-name or attribute name.
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr
        else:
            continue
        if callee not in func_params:
            continue
        # Only consider positional args that are simple Names.
        if len(node.args) < 2:
            continue
        positional: List[ast.Name] = []
        for a in node.args:
            if not isinstance(a, ast.Name):
                positional = []
                break
            positional.append(a)
        if not positional:
            continue
        arg_names = [a.id for a in positional]
        param_names = func_params[callee]
        # Heuristic: every arg name must also be a param name (otherwise
        # it's coincidence — pylint also requires this).
        if not all(name in param_names for name in arg_names):
            continue
        # All arg names are params; if any is in the wrong position AND
        # the arg currently in that position belongs at this position,
        # flag a swap.
        flagged = False
        for i, arg_name in enumerate(arg_names):
            if i >= len(param_names):
                break
            expected_pos = param_names.index(arg_name)
            if expected_pos == i:
                continue
            if expected_pos >= len(arg_names):
                continue
            other = arg_names[expected_pos]
            if other in param_names and param_names.index(other) == i:
                flagged = True
                break
        if flagged:
            issues.append(
                (
                    node.lineno,
                    f"Positional arguments appear to be out of order in call to '{callee}' "
                    f"(passed: {', '.join(arg_names)}; expected order: {', '.join(param_names[: len(arg_names)])})",
                )
            )
    return issues


def run_strict_extras(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Verifier-shaped tool runner: returns ``{passed, issues}`` over W0201 + W1114."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}", "passed": False}
    try:
        code = read_file(validated_path)
    except OSError as exc:
        return {"error": str(exc), "passed": False}
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"error": f"syntax: {exc.msg}", "passed": False}

    issues: List[str] = []
    for line, msg in _check_attribute_defined_outside_init(tree):
        issues.append(f"Line {line}: W0201: {msg}")
    for line, msg in _check_arguments_out_of_order(tree):
        issues.append(f"Line {line}: W1114: {msg}")
    issues.sort()
    return {
        "issues_count": len(issues),
        "issues": issues[:100],
        "passed": not issues,
    }

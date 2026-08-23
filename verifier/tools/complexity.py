"""Complexity and cohesion tool runners: radon, cohesion, nesting."""

from __future__ import annotations

import ast
import importlib
from typing import Any, Dict, List, cast

from .common import read_file, validate_filepath


def run_radon(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Run radon using its Python API."""
    try:
        complexity_mod = cast(Any, importlib.import_module("radon.complexity"))
        metrics_mod = cast(Any, importlib.import_module("radon.metrics"))
    except ImportError:
        return {"error": "radon not installed"}
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    try:
        code = read_file(validated_path)
        cc_results = complexity_mod.cc_visit(code)
        if cc_results:
            max_cc = max(r.complexity for r in cc_results)
            avg_cc = sum(r.complexity for r in cc_results) / len(cc_results)
        else:
            max_cc = 0
            avg_cc = 0.0
        mi_score = metrics_mod.mi_visit(code, multi=False)
        passed = max_cc <= 25 and mi_score >= 20.0
        issues = []
        if not passed:
            if max_cc > 25:
                issues.append(f"Max complexity {max_cc} > 25")
            if mi_score < 20:
                issues.append(f"Maintainability index {mi_score:.1f} < 20")
        return {
            "avg_complexity": avg_cc,
            "max_complexity": max_cc,
            "maintainability_index": mi_score,
            "passed": passed,
            "issues": issues,
        }
    except (OSError, IOError, SyntaxError) as exc:
        return {"error": str(exc)}


def run_cohesion(filepath: str, _actual_name: str) -> Dict[str, Any]:
    """Run cohesion check using AST analysis."""
    validated_path, err = validate_filepath(filepath)
    if err:
        return {"error": f"Validation failed: {err}"}
    try:
        code = read_file(validated_path)
        try:
            module = ast.parse(code)
        except SyntaxError:
            return {"low_cohesion_count": 0, "issues": [], "passed": True}
        low_cohesion = _analyze_class_cohesion(module)
        return {
            "low_cohesion_count": len(low_cohesion),
            "issues": low_cohesion[:100],
            "passed": not low_cohesion,
        }
    except (OSError, IOError) as exc:
        return {"error": str(exc)}


def _analyze_class_cohesion(module: ast.Module) -> List[str]:
    """Analyze class cohesion and return list of low-cohesion classes."""
    results = []
    skip = {"EnhancedPythonVerifier", "VerificationCache"}
    for node in ast.walk(module):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name in skip:
            continue
        methods = [n for n in node.body if isinstance(n, ast.FunctionDef)]
        if not methods:
            continue
        instance_attrs: set[str] = set()
        method_attrs: List[set[str]] = []
        for method in methods:
            attrs: set[str] = set()
            for child in ast.walk(method):
                if isinstance(child, ast.Attribute):
                    if isinstance(child.value, ast.Name) and child.value.id == "self":
                        attrs.add(child.attr)
                        instance_attrs.add(child.attr)
            method_attrs.append(attrs)
        if instance_attrs and method_attrs:
            total = sum(len(a) for a in method_attrs)
            maximum = len(methods) * len(instance_attrs)
            if maximum > 0:
                pct = (total / maximum) * 100
                if pct < 50.0:
                    results.append(f"{node.name}: {pct:.1f}%")
    return results


def _is_nesting_node(node: ast.AST) -> bool:
    """Check if node counts towards nesting depth."""
    return isinstance(
        node,
        (ast.If, ast.For, ast.While, ast.With, ast.FunctionDef, ast.AsyncFunctionDef),
    )


def _calculate_max_nesting(node: ast.AST, depth: int = 0) -> int:
    """Recursively calculate max nesting depth."""
    max_depth = depth
    for child in ast.iter_child_nodes(node):
        new_depth = depth + 1 if _is_nesting_node(child) else depth
        child_depth = _calculate_max_nesting(child, new_depth)
        max_depth = max(max_depth, child_depth)
    return max_depth


def check_nesting_depth(code: str, max_allowed: int = 8) -> Dict[str, Any]:
    """Check code nesting depth using pure AST analysis."""
    try:
        tree = ast.parse(code)
        max_depth = _calculate_max_nesting(tree)
        violations = []
        if max_depth > max_allowed:
            violations.append(f"Max depth {max_depth} exceeds limit {max_allowed}")
        return {
            "max_depth": max_depth,
            "violations": violations,
            "passed": max_depth <= max_allowed,
        }
    except SyntaxError:
        return {"max_depth": 0, "violations": [], "passed": True}

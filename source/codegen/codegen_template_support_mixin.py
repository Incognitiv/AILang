"""Extracted responsibilities for :class:`_CodeGenSupportMixin`."""

from __future__ import annotations

from parser.ast import (
    TemplateBlock,
)
from typing import Any

from llvmlite import ir


class _CodeGenTemplateSupportMixin:
    def _compile_ast_template(self: Any, node: TemplateBlock) -> str:
        """Compile a TemplateBlock AST node to LLVM IR."""
        from .templates import TemplateBlock as TBlock
        from .templates import template_compiler

        tblock = TBlock(node.language, node.code, node.captured_vars)
        result = template_compiler.compile_template(tblock)
        return result or ""

    def _parse_template_func_sigs(
        self: Any, llvm_ir: str
    ) -> list[tuple[str, ir.Type, list[ir.Type]]]:
        """Parse function signatures from LLVM IR text.
        Returns list of (name, return_type, [param_types]).
        Handles lines like:
          define dso_local i32 @c_add(i32 noundef %0, i32 noundef %1) #0 {
          define i64 @compute(i64 %x, double %y) {
          define void @setup() {
        """
        type_map: dict[str, ir.Type] = {
            "void": ir.VoidType(),
            "i1": ir.IntType(1),
            "i8": ir.IntType(8),
            "i16": ir.IntType(16),
            "i32": ir.IntType(32),
            "i64": ir.IntType(64),
            "i128": ir.IntType(128),
            "float": ir.FloatType(),
            "double": ir.DoubleType(),
        }
        ptr_type = ir.IntType(8).as_pointer()
        results: list[tuple[str, ir.Type, list[ir.Type]]] = []
        for line in llvm_ir.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("define"):
                continue
            if "@" not in stripped:
                continue
            # Strip 'define' and optional linkage (dso_local, hidden, etc.)
            parts = stripped.split("@", 1)
            pre_at = parts[0].split()  # ['define', 'dso_local', 'i32'] etc.
            post_at = parts[1]  # 'c_add(i32 noundef %0, ...) #0 {'
            # Return type is last token before @
            ret_str = pre_at[-1] if pre_at else "void"
            if ret_str.endswith("*"):
                ret_type: ir.Type = ptr_type
            else:
                ret_type = type_map.get(ret_str, ir.IntType(64))
            # Function name is before '('
            name = post_at.split("(", 1)[0]
            # Parse param types from between ( and )
            param_section = ""
            if "(" in post_at and ")" in post_at:
                param_section = post_at.split("(", 1)[1].rsplit(")", 1)[0].strip()
            param_types: list[ir.Type] = []
            if param_section and param_section != "...":
                for param in param_section.split(","):
                    param = param.strip()
                    if not param or param == "...":
                        continue
                    # First word is the type
                    ptype_str = param.split()[0]
                    if ptype_str.endswith("*"):
                        param_types.append(ptr_type)
                    else:
                        param_types.append(type_map.get(ptype_str, ir.IntType(64)))
            results.append((name, ret_type, param_types))
        return results

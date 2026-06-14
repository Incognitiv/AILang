"""
AILang AST - Abstract Syntax Tree Node Definitions.
Defines all AST node types used by the parser.
"""

from typing import Any, Optional

# Type alias for parsed type specifications.
# Supported tuple forms:
# - ("array", inner_type)              -> dynamic array [T]
# - ("fixed_array", inner_type, size)  -> fixed array [T;N]
# - ("slice", inner_type)              -> read-only or view-like slice[T]
ParsedType = Any


def parsed_type_to_str(ptype: ParsedType) -> str:
    """Canonical string form for ParsedType.

    This keeps downstream code deterministic and avoids leaking Python's
    tuple representation (e.g. "('array', 'i64')") into type tables.
    """
    if isinstance(ptype, str):
        return ptype
    if not ptype:
        return ""
    if not isinstance(ptype, tuple):
        return str(ptype)
    tag, *_rest = ptype
    if tag == "array" and len(ptype) >= 2:
        _tag, elem_type = ptype
        return f"[{parsed_type_to_str(elem_type)}]"
    if tag == "fixed_array" and len(ptype) >= 3:
        _tag, elem_type, size = ptype
        return f"[{parsed_type_to_str(elem_type)};{size}]"
    if tag == "slice" and len(ptype) >= 2:
        _tag, elem_type = ptype
        return f"slice[{parsed_type_to_str(elem_type)}]"
    if tag == "fn" and len(ptype) >= 4:
        _tag, raw_params, ret_type, raw_decorators = ptype
        params = []
        for item in raw_params:
            if isinstance(item, tuple) and len(item) >= 2:
                param_name, param_type = item
                params.append(f"{param_name}: {parsed_type_to_str(param_type)}")
        ret = parsed_type_to_str(ret_type)
        decorators = " ".join(f"@{name}" for name in raw_decorators)
        suffix = f" {decorators}" if decorators else ""
        return f"fn({', '.join(params)}): {ret}{suffix}"
    return str(ptype)


class ASTNode:
    """Base class for all AST nodes.
    Attributes:
        line: Source line number (1-based), set by parser
        col: Source column number (1-based), set by parser
        _source_path: Set by modules.py on Function/ClassDef nodes when
            stamping the owning .ail file's path -- needed for the
            profiler's func -> file:line mapping. Optional; None means the
            node wasn't loaded through the module loader (e.g. raw test
            ASTs constructed in-process).
    """

    line: int
    col: int
    _source_path: Optional[str] = None

    def set_pos(self, line: int, col: int = 0) -> "ASTNode":
        """Set source position and return self for chaining."""
        self.line = line
        self.col = col
        return self


# ============================================================================
# Module System

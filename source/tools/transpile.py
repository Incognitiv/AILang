"""
AILang Bidirectional Transpiler

Philosophy:
- AILang = SIMPLE to write (minimal boilerplate, clean syntax)
- Python = COMPLETE output (full type hints, safety checks, docstrings)

The transpiler INFERS missing information:
- Parameter types from field assignments
- Return types from return statements
- Safety checks from operations

Python → AILang: Simplification (strip verbosity)
AILang → Python: Enhancement (add types, safety, docs)
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional

from .transpile_py2ai_control_expr import _binop_symbol as _m__binop_symbol
from .transpile_py2ai_control_expr import _cmpop_symbol as _m__cmpop_symbol
from .transpile_py2ai_control_expr import _visit_Assert as _m__visit_Assert
from .transpile_py2ai_control_expr import _visit_Attribute as _m__visit_Attribute
from .transpile_py2ai_control_expr import _visit_BinOp as _m__visit_BinOp
from .transpile_py2ai_control_expr import _visit_BoolOp as _m__visit_BoolOp
from .transpile_py2ai_control_expr import _visit_Break as _m__visit_Break
from .transpile_py2ai_control_expr import _visit_Call as _m__visit_Call
from .transpile_py2ai_control_expr import _visit_Compare as _m__visit_Compare
from .transpile_py2ai_control_expr import _visit_Constant as _m__visit_Constant
from .transpile_py2ai_control_expr import _visit_Continue as _m__visit_Continue
from .transpile_py2ai_control_expr import _visit_Dict as _m__visit_Dict
from .transpile_py2ai_control_expr import _visit_DictComp as _m__visit_DictComp
from .transpile_py2ai_control_expr import _visit_Expr as _m__visit_Expr
from .transpile_py2ai_control_expr import _visit_For as _m__visit_For
from .transpile_py2ai_control_expr import _visit_GeneratorExp as _m__visit_GeneratorExp
from .transpile_py2ai_control_expr import _visit_Global as _m__visit_Global
from .transpile_py2ai_control_expr import _visit_If as _m__visit_If
from .transpile_py2ai_control_expr import _visit_IfExp as _m__visit_IfExp
from .transpile_py2ai_control_expr import _visit_JoinedStr as _m__visit_JoinedStr
from .transpile_py2ai_control_expr import _visit_Lambda as _m__visit_Lambda
from .transpile_py2ai_control_expr import _visit_List as _m__visit_List
from .transpile_py2ai_control_expr import _visit_ListComp as _m__visit_ListComp
from .transpile_py2ai_control_expr import _visit_Name as _m__visit_Name
from .transpile_py2ai_control_expr import _visit_NamedExpr as _m__visit_NamedExpr
from .transpile_py2ai_control_expr import _visit_Nonlocal as _m__visit_Nonlocal
from .transpile_py2ai_control_expr import _visit_Pass as _m__visit_Pass
from .transpile_py2ai_control_expr import _visit_Raise as _m__visit_Raise
from .transpile_py2ai_control_expr import _visit_Set as _m__visit_Set
from .transpile_py2ai_control_expr import _visit_Slice as _m__visit_Slice
from .transpile_py2ai_control_expr import _visit_Starred as _m__visit_Starred
from .transpile_py2ai_control_expr import _visit_Subscript as _m__visit_Subscript
from .transpile_py2ai_control_expr import _visit_Try as _m__visit_Try
from .transpile_py2ai_control_expr import _visit_Tuple as _m__visit_Tuple
from .transpile_py2ai_control_expr import _visit_UnaryOp as _m__visit_UnaryOp
from .transpile_py2ai_control_expr import _visit_While as _m__visit_While
from .transpile_py2ai_control_expr import _visit_With as _m__visit_With
from .transpile_py2ai_declarations import (
    _find_field_assignments as _m__find_field_assignments,
)
from .transpile_py2ai_declarations import (
    _get_default_for_type as _m__get_default_for_type,
)
from .transpile_py2ai_declarations import _get_type_name as _m__get_type_name
from .transpile_py2ai_declarations import _has_complex_expr as _m__has_complex_expr
from .transpile_py2ai_declarations import _is_simple_init as _m__is_simple_init
from .transpile_py2ai_declarations import _visit_AnnAssign as _m__visit_AnnAssign
from .transpile_py2ai_declarations import _visit_Assign as _m__visit_Assign
from .transpile_py2ai_declarations import _visit_AugAssign as _m__visit_AugAssign
from .transpile_py2ai_declarations import _visit_ClassDef as _m__visit_ClassDef
from .transpile_py2ai_declarations import _visit_FunctionDef as _m__visit_FunctionDef
from .transpile_py2ai_declarations import _visit_Import as _m__visit_Import
from .transpile_py2ai_declarations import _visit_ImportFrom as _m__visit_ImportFrom
from .transpile_py2ai_declarations import _visit_init_method as _m__visit_init_method
from .transpile_py2ai_declarations import _visit_Module as _m__visit_Module
from .transpile_py2ai_declarations import _visit_Return as _m__visit_Return

# =============================================================================
# Type Inference Engine
# =============================================================================


@dataclass
class InferredType:
    """Represents an inferred or declared type"""

    name: str
    is_inferred: bool = False
    source: str = ""  # Where we inferred it from

    def __str__(self) -> str:
        return self.name


class TypeInferer:
    """Infers types from code patterns"""

    # Common type patterns
    LITERAL_TYPES: ClassVar[dict] = {
        "int": r"^-?\d+$",
        "float": r"^-?\d+\.\d*$",
        "str": r'^["\'].*["\']$',
        "bool": r"^(True|False|true|false)$",
        "list": r"^\[.*\]$",
        "dict": r"^\{.*\}$",
    }

    # Method name patterns that hint at types
    METHOD_HINTS: ClassVar[dict] = {
        "__init__": "None",
        "__str__": "str",
        "__repr__": "str",
        "__len__": "int",
        "__bool__": "bool",
        "__iter__": "Iterator",
        "__next__": "Any",
        "__eq__": "bool",
        "__lt__": "bool",
        "__gt__": "bool",
        "__le__": "bool",
        "__ge__": "bool",
        "__hash__": "int",
    }

    def __init__(self):
        self.field_types: Dict[str, str] = {}  # field_name -> type
        self.param_types: Dict[str, str] = {}  # param_name -> type
        self.local_types: Dict[str, str] = {}  # var_name -> type

    def infer_from_assignment(self, target: str, value_type: str) -> str:
        """Infer variable type from what's assigned to it"""
        # self.name: str = x  →  x is str
        if target.startswith("self."):
            field = target[5:]
            self.field_types[field] = value_type
        else:
            self.local_types[target] = value_type
        return value_type

    def infer_param_from_field(self, param: str, field_type: str) -> str:
        """If self.x: T = param, then param: T"""
        self.param_types[param] = field_type
        return field_type

    def infer_return_type(self, method_name: str, returns: List[str]) -> str:
        """Infer return type from method name or return statements"""
        # Check magic method hints first
        if method_name in self.METHOD_HINTS:
            return self.METHOD_HINTS[method_name]

        # Analyze return statements
        if not returns:
            return "None"

        # If all returns are same type, use that
        types = set(returns)
        if len(types) == 1:
            return types.pop()

        # Multiple types = Union (simplified to Any)
        return "Any"

    def infer_literal_type(self, value: str) -> str:
        """Infer type from literal value"""
        for type_name, pattern in self.LITERAL_TYPES.items():
            if re.match(pattern, value.strip()):
                return type_name
        return "Any"


# =============================================================================
# Python → AILang Transpiler (Simplification)
# =============================================================================


class PythonToAILang:
    """
    Transpile Python to AILang (simplified syntax)

    Removes:
    - Return type hints (inferred from method name/returns)
    - Verbose docstrings (optional)

    Converts:
    - Python 'name: type' to AILang 'type name' syntax
    - Python 'def f(x: int)' to AILang 'def f(int x)'
    """

    # AILang reserved words that cannot be used as identifiers
    RESERVED_WORDS: ClassVar[set] = {
        "end",
        "then",
        "if",
        "else",
        "elsif",
        "while",
        "for",
        "foreach",
        "loop",
        "def",
        "return",
        "break",
        "continue",
        "class",
        "record",
        "enum",
        "match",
        "case",
        "try",
        "catch",
        "except",
        "finally",
        "true",
        "false",
        "nil",
        "and",
        "or",
        "not",
        "in",
        "use",
        "import",
        "public",
        "private",
        "const",
        "new",
        "this",
    }

    def __init__(self, keep_field_types: bool = True, keep_docstrings: bool = False):
        self.keep_field_types = keep_field_types
        self.keep_docstrings = keep_docstrings
        self.indent = 0

    def _safe_name(self, name: str) -> str:
        """Rename identifier if it conflicts with AILang reserved words"""
        if name in self.RESERVED_WORDS:
            return f"{name}_"
        return name

    def transpile(self, source: str) -> str:
        """Transpile Python source to AILang"""
        tree = ast.parse(source)
        return self._visit(tree)

    def _indent(self) -> str:
        return "    " * self.indent

    def _visit(self, node: ast.AST) -> str:
        method = f"_visit_{node.__class__.__name__}"
        visitor = getattr(self, method, self._generic_visit)
        return visitor(node)

    def _generic_visit(self, node: ast.AST) -> str:
        return ""

    # Class names from ast.py that should be mapped to int (pointer)
    AST_CLASS_NAMES: ClassVar[set] = {
        "ASTNode",
        "Import",
        "FromImport",
        "Use",
        "Library",
        "Number",
        "Bool",
        "StringLit",
        "InterpolatedString",
        "ArrayLit",
        "ListComprehension",
        "Range",
        "DictLit",
        "DictAccess",
        "DictAssign",
        "Variable",
        "BinaryOp",
        "UnaryOp",
        "TernaryOp",
        "Call",
        "ArrayAccess",
        "Return",
        "Break",
        "Continue",
        "VarDecl",
        "Assign",
        "TupleAssign",
        "If",
        "While",
        "For",
        "Loop",
        "Foreach",
        "Repeat",
        "TryExcept",
        "Function",
        "Block",
        "BlockCall",
        "TemplateBlock",
        "Match",
        "Cast",
        "RecordDef",
        "EnumDef",
        "ClassDef",
        "NewExpr",
        "FieldAccess",
        "SafeFieldAccess",
        "FieldAssign",
        "MethodCall",
        "ThisExpr",
        "InlineAsm",
        "ParsedType",
    }

    # Python modules that shouldn't be transpiled to AILang use statements
    SKIP_IMPORTS: ClassVar[set] = {
        # Python internals
        "typing",
        "__future__",
        "dataclasses",
        "abc",
        "functools",
        "contextlib",
        "collections.abc",
        "collections",
        "enum",
        # FFI/runtime - AILang handles differently
        "ctypes",
        "cffi",
        # LLVM - AILang IS the compiler, doesn't need bindings
        "llvmlite",
        "llvmlite.ir",
        "llvmlite.binding",
        # Parsing - AILang has its own lexer/parser
        "ast",
        "tokenize",
        "re",
        # OS/sys - only basic support
        "sys",
        "os",
        "pathlib",
        "subprocess",
        "shutil",
        # Testing/debugging
        "unittest",
        "pytest",
        "logging",
        "traceback",
        "inspect",
        # Concurrency - not yet in AILang
        "threading",
        "multiprocessing",
        "asyncio",
        "concurrent",
        # Network/web - not yet in AILang
        "socket",
        "http",
        "urllib",
        "json",
        # Other heavy deps
        "numpy",
        "pandas",
    }

    # Map Python modules to AILang equivalents
    MODULE_MAP: ClassVar[dict] = {
        "math": "std.math",
        "io": "std.io",
        "string": "std.string",
    }

    # Type-related names to skip in assignments
    TYPE_NAMES: ClassVar[set] = {
        "Union",
        "Optional",
        "List",
        "Dict",
        "Tuple",
        "Set",
        "Any",
        "TypeVar",
    }

    # Expressions

    # Python built-in type conversions to AILang casts
    TYPE_CASTS: ClassVar[set] = {"int", "float", "str", "bool"}

    # Python string methods that need conversion to AILang builtins or comments
    # Maps method_name -> (ailang_func, needs_object_as_first_arg)
    STRING_METHODS: ClassVar[dict] = {
        "rstrip": ("str_rstrip", True),  # AILang: str_rstrip(s, chars)
        "lstrip": ("str_lstrip", True),
        "strip": ("str_strip", True),
        "replace": ("str_replace", True),  # AILang: str_replace(s, old, new)
        "lower": ("str_lower", True),
        "upper": ("str_upper", True),
        "isalpha": ("str_isalpha", True),
        "isdigit": ("str_isdigit", True),
        "startswith": ("str_startswith", True),
        "endswith": ("str_endswith", True),
        "split": ("str_split", True),
        "join": ("str_join", True),
        "find": ("str_find", True),
        "count": ("str_count", True),
    }

    _get_type_name = _m__get_type_name
    _visit_Module = _m__visit_Module
    _visit_Import = _m__visit_Import
    _visit_ImportFrom = _m__visit_ImportFrom
    _visit_ClassDef = _m__visit_ClassDef
    _get_default_for_type = _m__get_default_for_type
    _is_simple_init = _m__is_simple_init
    _has_complex_expr = _m__has_complex_expr
    _visit_init_method = _m__visit_init_method
    _find_field_assignments = _m__find_field_assignments
    _visit_FunctionDef = _m__visit_FunctionDef
    _visit_AnnAssign = _m__visit_AnnAssign
    _visit_Assign = _m__visit_Assign
    _visit_AugAssign = _m__visit_AugAssign
    _visit_Return = _m__visit_Return
    _visit_If = _m__visit_If
    _visit_While = _m__visit_While
    _visit_For = _m__visit_For
    _visit_Expr = _m__visit_Expr
    _visit_Pass = _m__visit_Pass
    _visit_Break = _m__visit_Break
    _visit_Continue = _m__visit_Continue
    _visit_Raise = _m__visit_Raise
    _visit_Try = _m__visit_Try
    _visit_With = _m__visit_With
    _visit_Assert = _m__visit_Assert
    _visit_Global = _m__visit_Global
    _visit_Nonlocal = _m__visit_Nonlocal
    _visit_Lambda = _m__visit_Lambda
    _visit_Name = _m__visit_Name
    _visit_Constant = _m__visit_Constant
    _visit_JoinedStr = _m__visit_JoinedStr
    _visit_Attribute = _m__visit_Attribute
    _visit_Subscript = _m__visit_Subscript
    _visit_Slice = _m__visit_Slice
    _visit_IfExp = _m__visit_IfExp
    _visit_Set = _m__visit_Set
    _visit_NamedExpr = _m__visit_NamedExpr
    _visit_Starred = _m__visit_Starred
    _visit_Call = _m__visit_Call
    _visit_ListComp = _m__visit_ListComp
    _visit_DictComp = _m__visit_DictComp
    _visit_GeneratorExp = _m__visit_GeneratorExp
    _visit_BinOp = _m__visit_BinOp
    _visit_UnaryOp = _m__visit_UnaryOp
    _visit_BoolOp = _m__visit_BoolOp
    _visit_Compare = _m__visit_Compare
    _visit_List = _m__visit_List
    _visit_Dict = _m__visit_Dict
    _visit_Tuple = _m__visit_Tuple
    _binop_symbol = _m__binop_symbol
    _cmpop_symbol = _m__cmpop_symbol


# =============================================================================
# AILang → Python Transpiler (Enhancement)
# =============================================================================


class AILangToPython:
    """
    Transpile AILang to Python (with full type hints and safety)

    Adds:
    - Parameter type hints (inferred from field assignments)
    - Return type hints (inferred from method name/returns)
    - Docstrings (auto-generated)
    - Type checking assertions (optional)
    """

    def __init__(self, add_docstrings: bool = True, add_assertions: bool = False):
        self.add_docstrings = add_docstrings
        self.add_assertions = add_assertions
        self.inferer = TypeInferer()
        self.indent = 0
        self.current_class: Optional[str] = None
        self.current_method: Optional[str] = None

    def transpile(self, source: str) -> str:
        """Transpile AILang source to Python"""
        # First pass: collect type information
        self._analyze_types(source)
        # Second pass: generate Python with types
        return self._generate_python(source)

    def _analyze_types(self, source: str) -> None:
        """First pass: analyze source for type inference"""
        lines = source.split("\n")

        for line in lines:
            stripped = line.strip()

            # Look for typed field assignments: self.x: T = param
            match = re.match(r"self\.(\w+):\s*(\w+)\s*=\s*(\w+)", stripped)
            if match:
                field, type_name, param = match.groups()
                self.inferer.field_types[field] = type_name
                self.inferer.param_types[param] = type_name

    def _generate_python(self, source: str) -> str:
        """Second pass: generate Python with inferred types"""
        lines = source.split("\n")
        output = []
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            indent_str = " " * indent

            # Class definition
            if stripped.startswith("class "):
                match = re.match(r"class\s+(\w+)(?:\(([^)]*)\))?:", stripped)
                if match:
                    name, bases = match.groups()
                    self.current_class = name
                    if bases:
                        output.append(f"{indent_str}class {name}({bases}):")
                    else:
                        output.append(f"{indent_str}class {name}:")

                    if self.add_docstrings:
                        output.append(f'{indent_str}    """{name} class"""')

            # Method definition (AILang style - no type hints)
            elif stripped.startswith("def "):
                match = re.match(r"def\s+(\w+)\(([^)]*)\):", stripped)
                if match:
                    name, params_str = match.groups()
                    self.current_method = name

                    # Add type hints to parameters
                    typed_params = self._add_param_types(params_str)

                    # Determine return type
                    return_type = self.inferer.infer_return_type(name, [])

                    output.append(
                        f"{indent_str}def {name}({typed_params}) -> {return_type}:"
                    )

                    if self.add_docstrings and name != "__init__":
                        output.append(
                            f'{indent_str}    """{name.replace("_", " ").title()}"""'
                        )

            # Field assignment with type: self.x: T = value
            elif re.match(r"\s*self\.\w+:\s*\w+\s*=", stripped):
                # Keep as-is (already has type)
                output.append(line)

            # Simple assignment that should get a type
            elif re.match(r"\s*self\.(\w+)\s*=\s*(\w+)\s*$", stripped):
                match = re.match(r"\s*self\.(\w+)\s*=\s*(\w+)\s*$", stripped)
                if match:
                    field, value = match.groups()
                    if field in self.inferer.field_types:
                        type_name = self.inferer.field_types[field]
                        output.append(
                            f"{indent_str}self.{field}: {type_name} = {value}"
                        )
                    else:
                        output.append(line)

            # Convert 'end' to pass (Python doesn't need it, but empty blocks do)
            elif stripped == "end":
                pass  # Skip 'end' keywords

            # Convert elsif to elif
            elif stripped.startswith("elsif "):
                condition = stripped[6:].rstrip(":").replace(" then", "")
                output.append(f"{indent_str}elif {condition}:")

            # Convert 'if ... then' to 'if ...:'
            elif stripped.startswith("if ") and "then" in stripped:
                condition = stripped[3:].replace(" then", "").rstrip(":")
                output.append(f"{indent_str}if {condition}:")

            # Convert 'while ... then' to 'while ...:'
            elif stripped.startswith("while ") and "then" in stripped:
                condition = stripped[6:].replace(" then", "").rstrip(":")
                output.append(f"{indent_str}while {condition}:")

            # Convert 'foreach x in y then' to 'for x in y:'
            elif stripped.startswith("foreach "):
                match = re.match(r"foreach\s+(\w+)\s+in\s+(.+?)\s*then", stripped)
                if match:
                    var, iterable = match.groups()
                    output.append(f"{indent_str}for {var} in {iterable}:")

            # Convert AILang constants
            elif "true" in stripped or "false" in stripped or "nil" in stripped:
                converted = (
                    stripped.replace("true", "True")
                    .replace("false", "False")
                    .replace("nil", "None")
                )
                output.append(f"{indent_str}{converted}")

            # Pass through other lines
            else:
                if stripped:  # Skip empty lines from 'end'
                    output.append(line)

            i += 1

        return "\n".join(output)

    def _add_param_types(self, params_str: str) -> str:
        """Add type hints to parameter string"""
        if not params_str.strip():
            return ""

        params = [p.strip() for p in params_str.split(",")]
        typed_params = []

        for param in params:
            if ":" in param:
                # Already has type
                typed_params.append(param)
            elif param == "self":
                typed_params.append("self")
            elif param in self.inferer.param_types:
                typed_params.append(f"{param}: {self.inferer.param_types[param]}")
            else:
                typed_params.append(f"{param}: Any")

        return ", ".join(typed_params)


# =============================================================================
# Convenience Functions
# =============================================================================


def python_to_ailang(source: str, keep_field_types: bool = True) -> str:
    """Convert Python source to AILang"""
    transpiler = PythonToAILang(keep_field_types=keep_field_types)
    return transpiler.transpile(source)


def ailang_to_python(source: str, add_docstrings: bool = True) -> str:
    """Convert AILang source to Python with full type hints"""
    transpiler = AILangToPython(add_docstrings=add_docstrings)
    return transpiler.transpile(source)


# =============================================================================
# Decorator for bidirectional code
# =============================================================================


class BidirectionalCode:
    """
    Wrapper that maintains both AILang and Python representations
    """

    def __init__(self, python_source: str):
        self._python = python_source
        self._ailang: Optional[str] = None

    @property
    def python(self) -> str:
        """Get Python representation (complete with types)"""
        return self._python

    @property
    def ailang(self) -> str:
        """Get AILang representation (simplified)"""
        if self._ailang is None:
            self._ailang = python_to_ailang(self._python)
        return self._ailang

    @classmethod
    def from_ailang(cls, ailang_source: str) -> "BidirectionalCode":
        """Create from AILang source"""
        python_source = ailang_to_python(ailang_source)
        obj = cls(python_source)
        obj._ailang = ailang_source
        return obj

    def save_python(self, path: str) -> None:
        """Save Python version"""
        with open(path, "w") as f:
            f.write(self.python)

    def save_ailang(self, path: str) -> None:
        """Save AILang version"""
        with open(path, "w") as f:
            f.write(self.ailang)


def bidirectional(source: str) -> BidirectionalCode:
    """Create bidirectional code from Python source"""
    return BidirectionalCode(source)


__all__ = [
    "AILangToPython",
    "BidirectionalCode",
    "PythonToAILang",
    "TypeInferer",
    "ailang_to_python",
    "bidirectional",
    "python_to_ailang",
]

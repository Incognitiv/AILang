"""Declaration/type/generic AST nodes."""

from __future__ import annotations

from typing import Any, Optional

from .ast_base import ASTNode, ParsedType


class Function(ASTNode):
    def __init__(
        self,
        name: str,
        params: list[tuple[str, ParsedType, Optional[Any]]],
        return_type: ParsedType,
        body: list[ASTNode],
        is_public: bool = False,
        decorators: Optional[list[str]] = None,
        is_async: bool = False,
        is_test: bool = False,
    ) -> None:
        self.name: str = name
        # params: [(name, type, default_value), ...] where default_value can be None
        self.params: list[tuple[str, ParsedType, Optional[Any]]] = params
        self.return_type: ParsedType = return_type
        self.body: list[ASTNode] = body
        self.is_public: bool = is_public
        self.decorators: list[str] = decorators or []
        self.is_async: bool = is_async  # True for async def functions
        self.is_test: bool = is_test  # True for test functions


class Block(ASTNode):
    """Ruby-style block: |params| then ... end"""

    def __init__(self, params: list[str], body: list[ASTNode]) -> None:
        self.params: list[str] = params  # Block parameter names
        self.body: list[ASTNode] = body


class BlockCall(ASTNode):
    """Method call with block: items.each |x| then ... end"""

    def __init__(
        self,
        object_expr: ASTNode,
        method_name: str,
        args: list[ASTNode],
        block: "Block",
    ) -> None:
        self.object_expr: ASTNode = object_expr
        self.method_name: str = method_name
        self.args: list[ASTNode] = args
        self.block: Block = block


class TemplateBlock(ASTNode):
    """Foreign code template block (#template ... #end)"""

    def __init__(
        self, language: str, code: str, captured_vars: Optional[list[str]] = None
    ) -> None:
        self.language: str = language
        self.code: str = code
        self.captured_vars: list[str] = captured_vars or []


class Match(ASTNode):
    """Match expression: match expr then case val: ... end"""

    def __init__(
        self,
        expr: ASTNode,
        cases: list[tuple[ASTNode, list[ASTNode]]],
        default_case: Optional[list[ASTNode]] = None,
    ) -> None:
        self.expr: ASTNode = expr
        self.cases: list[tuple[ASTNode, list[ASTNode]]] = cases  # [(value, body), ...]
        self.default_case: Optional[list[ASTNode]] = default_case  # body


class MatchPattern(ASTNode):
    """Pattern for match destructuring: EnumName.Variant(binding1, binding2)"""

    def __init__(
        self,
        enum_name: str,
        variant_name: str,
        bindings: list[str],
    ) -> None:
        self.enum_name: str = enum_name
        self.variant_name: str = variant_name
        self.bindings: list[str] = bindings  # Variable names to bind fields to


class Cast(ASTNode):
    """Type cast: (int)expr"""

    def __init__(self, target_type: str, expr: ASTNode) -> None:
        self.target_type: str = target_type
        self.expr: ASTNode = expr


# ============================================================================
# Records and Classes
# ============================================================================
class RecordDef(ASTNode):
    """Record definition: record Name then type field ... end"""

    def __init__(self, name: str, fields: list[tuple[str, str]]) -> None:
        self.name: str = name
        self.fields: list[tuple[str, str]] = fields  # [(field_name, field_type), ...]
        self.decorators: list[str] = []


class ExternRecordDef(ASTNode):
    """Imported/incomplete C record declaration.

    `opaque record Name` is pointer-only and has no known layout.
    `extern record Name` is pointer-only for values/calls but can carry generated
    C layout metadata for sizeof/alignof/offsetof without emitting the struct.
    """

    def __init__(
        self,
        name: str,
        fields: list[tuple[str, str]] | None = None,
        is_opaque: bool = True,
        c_name: str | None = None,
        c_name_explicit: bool = False,
        layout_size: int | None = None,
        layout_align: int | None = None,
        field_offsets: dict[str, int] | None = None,
        field_sizes: dict[str, int] | None = None,
        bitfields: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self.name: str = name
        self.fields: list[tuple[str, str]] = fields or []
        self.is_opaque: bool = is_opaque
        self.c_name: str = c_name or name
        self.c_name_explicit: bool = c_name_explicit
        self.layout_size: int | None = layout_size
        self.layout_align: int | None = layout_align
        self.field_offsets: dict[str, int] = field_offsets or {}
        self.field_sizes: dict[str, int] = field_sizes or {}
        self.bitfields: dict[str, dict[str, int]] = bitfields or {}
        self.decorators: list[str] = []


class EnumVariant(ASTNode):
    """Single variant of a data-carrying enum.
    Simple variant: Pending (no fields, just a tag)
    Data variant: Number(int value) or BinOp(AST left, string op, AST right)
    """

    def __init__(
        self,
        name: str,
        fields: list[tuple[str, str]] | None = None,
        value: int | None = None,
    ) -> None:
        self.name: str = name
        # Fields: [(field_name, field_type), ...] or None for simple variants
        self.fields: list[tuple[str, str]] | None = fields
        # Explicit integer value (for C-style enums), auto-assigned if None
        self.value: int | None = value

    def has_data(self) -> bool:
        """Return True if this variant carries data."""
        return self.fields is not None and len(self.fields) > 0


class EnumDef(ASTNode):
    """Enum definition with optional data-carrying variants.
    Simple enum (C-style):
        enum Status then Pending, Active, Done end
    Data-carrying enum (Rust-style):
        enum AST then
            Number(int value)
            BinOp(AST left, string op, AST right)
            Variable(string name)
        end
    """

    def __init__(
        self,
        name: str,
        variants: list[EnumVariant],
        values: list[tuple[str, int]] | None = None,
    ) -> None:
        self.name: str = name
        self.variants: list[EnumVariant] = variants
        # Legacy field for backwards compatibility with simple enums
        # Maintained for existing code that uses values list directly
        self.values: list[tuple[str, int]] = values or [
            (v.name, v.value if v.value is not None else i)
            for i, v in enumerate(variants)
        ]

    def has_data_variants(self) -> bool:
        """Return True if any variant carries data."""
        return any(v.has_data() for v in self.variants)


class EnumConstruct(ASTNode):
    """Construct an enum variant with data: AST.Number(42)"""

    def __init__(self, enum_name: str, variant_name: str, args: list[ASTNode]) -> None:
        self.enum_name: str = enum_name
        self.variant_name: str = variant_name
        self.args: list[ASTNode] = args  # Arguments to construct the variant


class EnumFieldAccess(ASTNode):
    """Access a field from an enum variant: node.value (when node is AST.Number)"""

    def __init__(self, expr: ASTNode, field_name: str) -> None:
        self.expr: ASTNode = expr
        self.field_name: str = field_name


class ClassDef(ASTNode):
    """Class definition: class Name then ... end"""

    def __init__(self, name: str, fields: list[Any], methods: list[Any]) -> None:
        self.name: str = name
        self.fields: list[Any] = (
            fields  # [(visibility, field_name, field_type, init_value), ...]
        )
        self.methods: list[Any] = methods  # [Function objects with visibility]
        self.decorators: list[str] = []


class NewExpr(ASTNode):
    """Create new instance: new TypeName(args)"""

    def __init__(self, type_name: str, args: list[ASTNode]) -> None:
        self.type_name: str = type_name
        self.args: list[ASTNode] = args  # [expr, ...]


class FieldAccess(ASTNode):
    """Field access: object.field"""

    def __init__(self, object_expr: ASTNode, field_name: str) -> None:
        self.object_expr: ASTNode = object_expr
        self.field_name: str = field_name


class SafeFieldAccess(ASTNode):
    """Safe field access: object?.field (returns nil if object is nil)"""

    def __init__(self, object_expr: ASTNode, field_name: str) -> None:
        self.object_expr: ASTNode = object_expr
        self.field_name: str = field_name


class FieldAssign(ASTNode):
    """Field assignment: object.field = value"""

    def __init__(self, object_expr: ASTNode, field_name: str, value: ASTNode) -> None:
        self.object_expr: ASTNode = object_expr
        self.field_name: str = field_name
        self.value: ASTNode = value


class MethodCall(ASTNode):
    """Method call: object.method(args)"""

    def __init__(
        self, object_expr: ASTNode, method_name: str, args: list[ASTNode]
    ) -> None:
        self.object_expr: ASTNode = object_expr
        self.method_name: str = method_name
        self.args: list[ASTNode] = args


class ThisExpr(ASTNode):
    """Reference to current instance: this"""

    ...


# ============================================================================
# Low-level / Systems Programming
# ============================================================================
class InlineAsm(ASTNode):
    """Inline assembly: asm("instruction")
    For OS/kernel development, allows direct CPU instructions.
    Example: asm("cli")  // disable interrupts
             asm("hlt")  // halt CPU
    """

    def __init__(
        self, code: str, outputs: str = "", inputs: str = "", clobbers: str = ""
    ) -> None:
        self.code: str = code
        self.outputs: str = outputs
        self.inputs: str = inputs
        self.clobbers: str = clobbers


# ============================================================================
# Generics / Parametric Polymorphism
# ============================================================================
class GenericParam(ASTNode):
    """Generic type parameter: T, T: Comparable, etc."""

    def __init__(
        self, name: str, constraint: Optional[str] = None, default: Optional[str] = None
    ) -> None:
        self.name: str = name  # Type parameter name (e.g., "T")
        self.constraint: Optional[str] = constraint  # Optional constraint
        self.default: Optional[str] = default  # Optional default type


class GenericFunction(ASTNode):
    """Generic function: def foo[T](x: T): T ... end"""

    def __init__(
        self,
        name: str,
        type_params: list["GenericParam"],
        params: list[tuple[str, Any, Optional[Any]]],
        return_type: Any,
        body: list[ASTNode],
        decorators: Optional[list[str]] = None,
    ) -> None:
        self.name: str = name
        self.type_params: list[GenericParam] = type_params
        self.params = params
        self.return_type = return_type
        self.body: list[ASTNode] = body
        self.decorators: list[str] = decorators or []


class GenericRecord(ASTNode):
    """Generic record: record Pair[T, U]: first: T, second: U end"""

    def __init__(
        self,
        name: str,
        type_params: list["GenericParam"],
        fields: list[tuple[str, str]],
    ) -> None:
        self.name: str = name
        self.type_params: list[GenericParam] = type_params
        self.fields: list[tuple[str, str]] = fields
        self.decorators: list[str] = []


class GenericClass(ASTNode):
    """Generic class: class Container[T]: ... end"""

    def __init__(
        self,
        name: str,
        type_params: list["GenericParam"],
        fields: list[Any],
        methods: list[Any],
    ) -> None:
        self.name: str = name
        self.type_params: list[GenericParam] = type_params
        self.fields: list[Any] = fields
        self.methods: list[Any] = methods
        self.decorators: list[str] = []


class GenericInstantiation(ASTNode):
    """Generic type instantiation: Vec[int], Map[string, int]"""

    def __init__(self, base_type: str, type_args: list[str]) -> None:
        self.base_type: str = base_type
        self.type_args: list[str] = type_args


# ============================================================================
# Compile-Time Evaluation (Comptime)
# ============================================================================
class ComptimeExpr(ASTNode):
    """Compile-time expression: comptime expr
    Evaluates expression at compile time and substitutes the result.
    Example: comptime factorial(10) becomes 3628800 at compile time.
    """

    def __init__(self, expr: ASTNode) -> None:
        self.expr: ASTNode = expr


class ComptimeBlock(ASTNode):
    """Compile-time block: comptime then ... end
    Executes statements at compile time.
    Useful for generating code, computing constants, etc.
    """

    def __init__(self, body: list[ASTNode]) -> None:
        self.body: list[ASTNode] = body


class ComptimeIf(ASTNode):
    """Compile-time conditional: comptime if cond then ... end
    Conditional compilation based on compile-time values.
    """

    def __init__(
        self, cond: ASTNode, then_body: list[ASTNode], else_body: list[ASTNode]
    ) -> None:
        self.cond: ASTNode = cond
        self.then_body: list[ASTNode] = then_body
        self.else_body: list[ASTNode] = else_body


class StaticAssert(ASTNode):
    """Static assertion: static_assert cond, "message"
    Compile-time assertion that fails compilation if false.
    """

    def __init__(self, condition: ASTNode, message: Optional[str] = None) -> None:
        self.condition: ASTNode = condition
        self.message: Optional[str] = message


class CInclude(ASTNode):
    """C header include directive: #cinclude [target] <header.h>.

    Emits a C #include directive in the generated output.
    Works in the C transpiler backend; LLVM/JIT cannot import C headers directly.
    """

    def __init__(
        self,
        path: str,
        is_system: bool = False,
        target_os: str | None = None,
        line: int = 1,
        column: int = 1,
        raw: str = "",
    ) -> None:
        self.path: str = path
        self.is_system: bool = is_system
        self.target_os: str | None = target_os
        self.line: int = line
        self.column: int = column
        self.raw: str = raw


class CImport(ASTNode):
    """C binding import directive: #cimport [target] "path".

    Supported path forms:
    - quoted local/system include-style path: "foo.bindings.ail", "pkg.probe.json"
    - absolute or relative filesystem path.
    - optional target prefix for OS filtering, e.g. ``#cimport windows "foo.probe.json"``
    """

    def __init__(
        self,
        path: str,
        target_os: str | None = None,
        line: int = 1,
        column: int = 1,
        raw: str = "",
    ) -> None:
        self.path: str = path
        self.target_os: str | None = target_os
        self.line: int = line
        self.column: int = column
        self.raw: str = raw


class CAbiDefine(ASTNode):
    """C ABI header integer/expression constant."""

    def __init__(self, name: str, value: str) -> None:
        self.name: str = name
        self.value: str = value


class CAbiInclude(ASTNode):
    """C ABI header include directive."""

    def __init__(
        self, path: str, is_system: bool = True, include_next: bool = False
    ) -> None:
        self.path: str = path
        self.is_system: bool = is_system
        self.include_next: bool = include_next


class CAbiTypedef(ASTNode):
    """C ABI typedef declaration."""

    def __init__(self, name: str, c_type: str) -> None:
        self.name: str = name
        self.c_type: str = c_type


class CAbiField(ASTNode):
    """C ABI struct field declaration."""

    def __init__(self, name: str, c_type: str) -> None:
        self.name: str = name
        self.c_type: str = c_type


class CAbiStruct(ASTNode):
    """C ABI struct declaration emitted into a generated header."""

    def __init__(self, name: str, fields: list[CAbiField]) -> None:
        self.name: str = name
        self.fields: list[CAbiField] = fields


class CAbiPrototype(ASTNode):
    """C ABI function prototype declaration."""

    def __init__(
        self,
        name: str,
        return_type: str,
        params: list[tuple[str, str]],
        variadic: bool = False,
    ) -> None:
        self.name: str = name
        self.return_type: str = return_type
        self.params: list[tuple[str, str]] = params
        self.variadic: bool = variadic


class CAbiInlineFunction(ASTNode):
    """Static inline C function emitted into an ABI bridge header."""

    def __init__(
        self,
        name: str,
        return_type: str,
        params: list[tuple[str, str]],
        body: str,
        variadic: bool = False,
    ) -> None:
        self.name: str = name
        self.return_type: str = return_type
        self.params: list[tuple[str, str]] = params
        self.body: str = body
        self.variadic: bool = variadic


class CAbiConditional(ASTNode):
    """Preprocessor conditional block in an ABI bridge header."""

    def __init__(
        self,
        directive: str,
        expression: str,
        entries: list[ASTNode],
        else_entries: list[ASTNode] | None = None,
    ) -> None:
        self.directive: str = directive
        self.expression: str = expression
        self.entries: list[ASTNode] = entries
        self.else_entries: list[ASTNode] = else_entries or []


class CAbiMacro(ASTNode):
    """C ABI macro wrapper for cases C APIs expose only as macros."""

    def __init__(self, name: str, params: list[str], body: str) -> None:
        self.name: str = name
        self.params: list[str] = params
        self.body: str = body


class CAbiHeader(ASTNode):
    """AILang-owned C ABI header description.

    This is intentionally not a general preprocessor. It describes the C ABI
    surface an embedding runtime needs to expose to unchanged C sources.
    """

    def __init__(
        self,
        path: str,
        guard: str | None = None,
        entries: list[ASTNode] | None = None,
    ) -> None:
        self.path: str = path
        self.guard: str | None = guard
        self.entries: list[ASTNode] = entries or []


class ExternFn(ASTNode):
    """Foreign function declaration: extern fn name(params): ret_type
    Declares a C function callable from AILang without a template block.
    Works in both LLVM (external linkage) and C (forward declaration) backends.
    """

    def __init__(
        self,
        name: str,
        params: list[tuple[str, str]],
        ret_type: str,
        variadic: bool = False,
    ) -> None:
        self.name: str = name
        self.params: list[tuple[str, str]] = params  # [(name, type), ...]
        self.ret_type: str = ret_type
        self.variadic: bool = variadic
        self.decorators: list[str] = []


class LinkDirective(ASTNode):
    """Link directive: #link [target] "flags".

    Tells the build system what libraries/objects to link.
    Consumed by C and LLVM AOT native build paths.
    """

    def __init__(
        self,
        flags: str,
        target_os: str | None = None,
        line: int = 1,
        column: int = 1,
        raw: str = "",
    ) -> None:
        self.flags: str = flags
        self.target_os: str | None = target_os
        self.line: int = line
        self.column: int = column
        self.raw: str = raw


class ExternVar(ASTNode):
    """External variable declaration: extern var name: type
    Declares a C global variable accessible from AILang.
    """

    def __init__(self, name: str, var_type: str) -> None:
        self.name: str = name
        self.var_type: str = var_type
        self.decorators: list[str] = []


class UnionDef(ASTNode):
    """Union definition: union Name then type field ... end
    C-compatible union type where all fields share the same memory.
    """

    def __init__(self, name: str, fields: list[tuple[str, str]]) -> None:
        self.name: str = name
        self.fields: list[tuple[str, str]] = fields  # [(field_name, field_type), ...]
        self.decorators: list[str] = []


class ReinterpretCast(ASTNode):
    """Reinterpret cast: reinterpret(target_type, expr) or bitcast(target_type, expr)
    Reinterprets the bit pattern of expr as target_type.
    """

    def __init__(self, target_type: str, value: "ASTNode") -> None:
        self.target_type: str = target_type
        self.value: ASTNode = value

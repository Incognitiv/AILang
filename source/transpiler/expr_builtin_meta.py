"""Type and builtin utility helpers for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` to reduce monolith size and keep
dispatch-related metadata grouped.
"""

from __future__ import annotations

from parser.ast import StringLit, parsed_type_to_str
from typing import Any

from llvmlite import ir
from target_info import os_from_triple
from transpiler.expr_common import ARG_FIRST, ExprGenError


class ExprBuiltinMetaEmitter:
    """Expression service for helper builtins and type-metadata conversions."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _dispatch_len(self, args) -> ir.Value:
        """Dispatch len() with arg count validation."""
        if len(args) != 1:
            raise ExprGenError("len() expects exactly 1 argument")
        return self.codegen.builtin_len(args[ARG_FIRST])

    def _dispatch_single_arg(self, args, name: str, handler) -> ir.Value:
        """Dispatch a single-arg builtin (hex, bin, oct)."""
        if len(args) != 1:
            raise ExprGenError(f"{name}() expects 1 argument")
        return handler(self.codegen.generate_expr(args[ARG_FIRST]))

    def _builtin_typeof(self, args):
        """Return the type name of an expression as a string.

        typeof(x) -> "int", "float", "string", etc.
        """
        if len(args) != 1:
            raise ExprGenError("typeof() expects exactly 1 argument")

        # Generate the expression to get its LLVM type
        value = self.generate_expr(args[ARG_FIRST])
        llvm_type = value.type

        # Map LLVM type to AILang type name
        type_name = self._llvm_type_to_name(llvm_type)
        return self.codegen.create_string_constant(type_name)

    def _builtin_sizeof(self, args):
        """Return the size in bytes of a type or expression.

        sizeof("int") -> 8, sizeof("ptr") -> 8, sizeof(x) -> size of x's type
        """
        if len(args) != 1:
            raise ExprGenError("sizeof() expects exactly 1 argument")

        arg = args[ARG_FIRST]
        # If argument is a string literal, treat as type name
        if isinstance(arg, StringLit):
            type_name = self._resolve_type_name(arg.value)
            layout = self._extern_record_layout(type_name)
            if layout is not None:
                return ir.Constant(ir.IntType(64), int(layout.get("size", 0)))
            if type_name in getattr(self.codegen, "union_field_types", {}):
                return ir.Constant(
                    ir.IntType(64), self._get_union_size_bytes(type_name)
                )
            llvm_type = self._type_name_to_llvm(type_name)
        else:
            value = self.generate_expr(arg)
            llvm_type = value.type

        return ir.Constant(ir.IntType(64), self._get_type_size_bytes(llvm_type))

    def _builtin_alignof(self, args):
        """Return the alignment in bytes of a type or expression.

        alignof("int") -> 8, alignof("short") -> 4
        """
        if len(args) != 1:
            raise ExprGenError("alignof() expects exactly 1 argument")

        arg = args[ARG_FIRST]
        if isinstance(arg, StringLit):
            type_name = self._resolve_type_name(arg.value)
            layout = self._extern_record_layout(type_name)
            if layout is not None:
                return ir.Constant(ir.IntType(64), int(layout.get("align", 0)))
            if type_name in getattr(self.codegen, "union_field_types", {}):
                return ir.Constant(
                    ir.IntType(64), self._get_union_align_bytes(type_name)
                )
            llvm_type = self._type_name_to_llvm(type_name)
        else:
            value = self.generate_expr(arg)
            llvm_type = value.type

        return ir.Constant(ir.IntType(64), self._get_type_align_bytes(llvm_type))

    def _builtin_offsetof(self, args):
        """Return the byte offset of a record/union field.

        offsetof("Packet", "value") -> byte offset of field `value`.
        """
        if len(args) != 2:
            raise ExprGenError("offsetof() expects exactly 2 arguments")
        type_arg, field_arg = args
        if not isinstance(type_arg, StringLit) or not isinstance(field_arg, StringLit):
            raise ExprGenError(
                'offsetof() expects string literals: offsetof("Type", "field")'
            )

        type_name = self._resolve_type_name(type_arg.value)
        field_name = field_arg.value
        layout = self._extern_record_layout(type_name)
        if layout is not None:
            fields = layout.get("fields", {})
            field_layout = fields.get(field_name) if isinstance(fields, dict) else None
            if not isinstance(field_layout, dict):
                raise ExprGenError(
                    f"Field '{field_name}' not found in imported record {type_name}"
                )
            return ir.Constant(ir.IntType(64), int(field_layout.get("offset", 0)))

        union_fields = getattr(self.codegen, "union_field_types", {})
        if type_name in union_fields:
            if field_name not in union_fields[type_name]:
                raise ExprGenError(
                    f"Field '{field_name}' not found in union {type_name}"
                )
            return ir.Constant(ir.IntType(64), 0)

        fields = getattr(self.codegen, "record_fields", {}).get(type_name)
        if fields is None:
            raise ExprGenError(f"offsetof() unknown record/union type: {type_name}")

        offset = 0
        for fname, ftype in fields:
            field_type = self.codegen.get_llvm_type(ftype)
            offset = self._align_to(offset, self._get_type_align_bytes(field_type))
            if fname == field_name:
                return ir.Constant(ir.IntType(64), offset)
            offset += self._get_type_size_bytes(field_type)
        raise ExprGenError(f"Field '{field_name}' not found in record {type_name}")

    def _builtin_target_os(self, args) -> ir.Value:
        """Return the normalized target OS name as a string."""
        if args:
            raise ExprGenError("target_os() expects no arguments")
        return self.codegen.create_string_constant(
            os_from_triple(self.codegen.module.triple)
        )

    def _builtin_target_backend(self, args) -> ir.Value:
        """Return the normalized backend family name as a string."""
        if args:
            raise ExprGenError("target_backend() expects no arguments")
        return self.codegen.create_string_constant("llvm")

    def _type_name_to_llvm(self, name: str):
        """Map AILang type name string to LLVM type."""
        type_map = {
            "tiny": ir.IntType(8),
            "i8": ir.IntType(8),
            "byte": ir.IntType(8),
            "u8": ir.IntType(8),
            "small": ir.IntType(16),
            "i16": ir.IntType(16),
            "short": ir.IntType(32),
            "i32": ir.IntType(32),
            "int": ir.IntType(64),
            "i64": ir.IntType(64),
            "uint": ir.IntType(64),
            "u64": ir.IntType(64),
            "long": ir.IntType(128),
            "i128": ir.IntType(128),
            "float": ir.FloatType(),
            "f32": ir.FloatType(),
            "double": ir.DoubleType(),
            "f64": ir.DoubleType(),
            "bool": ir.IntType(1),
            "string": ir.IntType(8).as_pointer(),
            "ptr": ir.IntType(8).as_pointer(),
            "ptrptr": ir.IntType(8).as_pointer().as_pointer(),
            "void": ir.VoidType(),
        }
        if name in type_map:
            return type_map[name]
        return self.codegen.get_llvm_type(name)

    def _resolve_type_name(self, name: str) -> str:
        """Resolve AILang type aliases for metadata builtins."""
        aliases = getattr(self.codegen, "type_aliases", {})
        current = name.strip()
        seen: set[str] = set()
        while isinstance(current, str) and current in aliases and current not in seen:
            seen.add(current)
            current = parsed_type_to_str(aliases[current]).strip()
        return current

    def _extern_record_layout(self, type_name: str) -> dict[str, Any] | None:
        layouts = getattr(self.codegen, "extern_record_layouts", {})
        layout = layouts.get(type_name)
        if isinstance(layout, dict):
            return layout
        return None

    @staticmethod
    def _align_to(value: int, alignment: int) -> int:
        if alignment <= 1:
            return value
        return ((value + alignment - 1) // alignment) * alignment

    def _get_union_size_bytes(self, type_name: str) -> int:
        fields = getattr(self.codegen, "union_field_types", {}).get(type_name, {})
        max_size = 1
        max_align = 1
        for field_type in fields.values():
            max_size = max(max_size, self._get_type_size_bytes(field_type))
            max_align = max(max_align, self._get_type_align_bytes(field_type))
        return self._align_to(max_size, max_align)

    def _get_union_align_bytes(self, type_name: str) -> int:
        fields = getattr(self.codegen, "union_field_types", {}).get(type_name, {})
        max_align = 1
        for field_type in fields.values():
            max_align = max(max_align, self._get_type_align_bytes(field_type))
        return max_align

    def _get_struct_layout(self, llvm_type) -> tuple[int, int, list[int]]:
        """Return size, alignment, and field offsets for an unpacked struct."""
        offset = 0
        max_align = 1
        offsets: list[int] = []
        for elem in llvm_type.elements:
            align = self._get_type_align_bytes(elem)
            max_align = max(max_align, align)
            offset = self._align_to(offset, align)
            offsets.append(offset)
            offset += self._get_type_size_bytes(elem)
        return self._align_to(offset, max_align), max_align, offsets

    def _get_type_size_bytes(self, llvm_type) -> int:
        """Get the ABI-like size of an LLVM type in bytes."""
        if isinstance(llvm_type, ir.IntType):
            return max((llvm_type.width + 7) // 8, 1)
        if isinstance(llvm_type, ir.FloatType):
            return 4
        if isinstance(llvm_type, ir.DoubleType):
            return 8
        if isinstance(llvm_type, ir.PointerType):
            return 8
        if isinstance(llvm_type, ir.VoidType):
            return 0
        if isinstance(llvm_type, ir.ArrayType):
            return self._get_type_size_bytes(llvm_type.element) * int(llvm_type.count)
        if isinstance(llvm_type, ir.VectorType):
            return self._get_type_size_bytes(llvm_type.element) * int(llvm_type.count)
        if isinstance(llvm_type, ir.LiteralStructType):
            size, _align, _offsets = self._get_struct_layout(llvm_type)
            return size
        return 8

    def _get_type_align_bytes(self, llvm_type) -> int:
        """Get an ABI-like alignment for LLVM metadata builtins."""
        if isinstance(llvm_type, ir.IntType):
            size = self._get_type_size_bytes(llvm_type)
            return min(size, 16)
        if isinstance(llvm_type, ir.FloatType):
            return 4
        if isinstance(llvm_type, ir.DoubleType):
            return 8
        if isinstance(llvm_type, ir.PointerType):
            return 8
        if isinstance(llvm_type, ir.VoidType):
            return 1
        if isinstance(llvm_type, ir.ArrayType):
            return self._get_type_align_bytes(llvm_type.element)
        if isinstance(llvm_type, ir.VectorType):
            return min(self._get_type_size_bytes(llvm_type), 16)
        if isinstance(llvm_type, ir.LiteralStructType):
            _size, align, _offsets = self._get_struct_layout(llvm_type)
            return align
        return 8

    def _get_type_size_bits(self, llvm_type) -> int:
        """Get the size of an LLVM type in bits."""
        return self._get_type_size_bytes(llvm_type) * 8

    def _llvm_type_to_name(self, llvm_type) -> str:
        """Convert LLVM type to AILang type name string.

        AILang integer types:
        - i8, i16 (short), i32, i64 (int), i128 (long)
        - i256, i512, i1024, i2048, i4096 (big integers)
        - bool (i1)
        """
        if isinstance(llvm_type, ir.IntType):
            width = llvm_type.width
            # Map specific widths to AILang type names (the "Adjective Ladder")
            type_map = {
                1: "bool",
                8: "tiny",  # AILang tiny = 8-bit (byte for unsigned)
                16: "small",  # AILang small = 16-bit
                32: "short",  # AILang short = 32-bit
                64: "int",  # AILang int = 64-bit
                128: "long",  # AILang long = 128-bit
                256: "wide",  # AILang wide = 256-bit
                512: "vast",  # AILang vast = 512-bit
                1024: "grand",  # AILang grand = 1024-bit
                2048: "giant",  # AILang giant = 2048-bit
                4096: "titan",  # AILang titan = 4096-bit
                8192: "colos",  # AILang colos = 8192-bit (1KB!)
            }
            return type_map.get(width, f"i{width}")
        if isinstance(llvm_type, ir.FloatType):
            return "float"
        if isinstance(llvm_type, ir.DoubleType):
            return "double"
        if isinstance(llvm_type, ir.PointerType):
            pointee = llvm_type.pointee
            if isinstance(pointee, ir.IntType) and pointee.width == 8:
                return "string"
            # Check if pointee is a known record/class type
            if isinstance(pointee, ir.LiteralStructType):
                type_id = id(pointee)
                if type_id in self.codegen.record_type_ids:
                    return self.codegen.record_type_ids[type_id]
            return "pointer"
        if isinstance(llvm_type, ir.VoidType):
            return "void"
        if isinstance(llvm_type, ir.ArrayType):
            return "array"
        if isinstance(llvm_type, ir.VectorType):
            return "vector"
        if isinstance(llvm_type, ir.LiteralStructType):
            # Check if it's a known record type
            type_id = id(llvm_type)
            if type_id in self.codegen.record_type_ids:
                return self.codegen.record_type_ids[type_id]
            return "struct"
        return "unknown"

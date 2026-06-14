"""Type lowering helpers for LLVM codegen."""

from __future__ import annotations

import sys
import warnings
from parser.ast import RangeType, parsed_type_to_str
from typing import Any, Optional

from callback_types import callback_parts, is_callback_type, resolve_callback_alias
from codegen.codegen import CodeGenError
from llvmlite import ir


class TypeLowering:
    """Service for AILang type-to-LLVM lowering and record metadata lookups."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def get_type_size(self, llvm_type: ir.Type) -> int:
        """Get size in bytes of an LLVM type (public API)."""
        return self._type_size(llvm_type)

    def _resolve_type_alias_spec(self, type_spec: Any) -> Any:
        """Resolve user aliases before LLVM type lowering."""
        aliases = getattr(self._cg, "type_aliases", {})
        if not isinstance(type_spec, str) or not isinstance(aliases, dict):
            return type_spec
        if resolve_callback_alias(type_spec, aliases) is not None:
            return type_spec
        seen: set[str] = set()
        current: Any = type_spec
        while isinstance(current, str) and current in aliases and current not in seen:
            seen.add(current)
            target = aliases[current]
            if is_callback_type(target):
                return current
            if isinstance(target, RangeType):
                return "i64"
            current = parsed_type_to_str(target)
        return current

    def _callback_type_to_llvm(self, spec: object) -> ir.FunctionType:
        params, ret_type, _decorators = callback_parts(spec)
        ret_ty = self.get_llvm_type(ret_type)
        param_tys = [self.get_llvm_type(ptype) for _pname, ptype in params]
        return ir.FunctionType(ret_ty, param_tys)

    def get_llvm_type(self, type_spec: Any) -> Any:
        """Convert AILang type to LLVM type"""
        aliases = getattr(self._cg, "type_aliases", {})
        if isinstance(type_spec, str) and isinstance(aliases, dict):
            callback_spec = resolve_callback_alias(type_spec, aliases)
            if callback_spec is not None:
                return self._callback_type_to_llvm(callback_spec).as_pointer()
        if is_callback_type(type_spec):
            return self._callback_type_to_llvm(type_spec).as_pointer()
        resolved_spec = self._resolve_type_alias_spec(type_spec)
        if resolved_spec != type_spec:
            return self.get_llvm_type(resolved_spec)
        if isinstance(type_spec, tuple) and type_spec[0] == "array":
            # Array type: return pointer to element type
            elem_type = self.get_llvm_type(type_spec[1])
            return elem_type.as_pointer()
        if isinstance(type_spec, tuple) and type_spec[0] == "fixed_array":
            # Fixed arrays are first-class values for layout-sensitive records.
            elem_type = self.get_llvm_type(type_spec[1])
            arr_len = int(type_spec[2]) if len(type_spec) > 2 else 0
            if arr_len > 0:
                return ir.ArrayType(elem_type, arr_len)
            return elem_type.as_pointer()
        if isinstance(type_spec, tuple) and type_spec[0] == "slice":
            # Conservative lowering for now: same shape as dynamic arrays.
            return ir.IntType(64).as_pointer()

        # Handle string types
        if isinstance(type_spec, str):
            type_lower = type_spec.lower()
            if type_lower.startswith("slice[") and type_lower.endswith("]"):
                return ir.IntType(64).as_pointer()
            if type_lower.startswith("view[") and type_lower.endswith("]"):
                return ir.IntType(64).as_pointer()
            if type_lower.startswith("[") and type_lower.endswith("]"):
                inner = type_lower[1:-1]
                if ";" in inner:
                    elem_part, size_part = inner.rsplit(";", 1)
                    elem_type = self.get_llvm_type(elem_part.strip())
                    size_text = size_part.strip()
                    if size_text.isdigit():
                        size = int(size_text)
                        if size > 0:
                            return ir.ArrayType(elem_type, size)
                elem_type = self.get_llvm_type(inner.strip())
                return elem_type.as_pointer()
            if type_lower.startswith("i") or type_lower.startswith("u"):
                try:
                    width = int(type_lower[1:])
                    return ir.IntType(width)
                except ValueError:
                    pass
            if type_lower.startswith("f"):
                if type_lower == "f32":
                    return ir.FloatType()
                if type_lower == "f64":
                    return ir.DoubleType()
                if type_lower == "f128":
                    # llvmlite doesn't support fp128, use double as fallback
                    return ir.DoubleType()
            if type_lower in ("bool", "i1"):
                return ir.IntType(1)
            # Complete integer type ladder (8-8192 bit)
            # 8-bit
            if type_lower in ("tiny", "i8"):
                return ir.IntType(8)
            if type_lower in ("byte", "u8"):
                return ir.IntType(8)
            # 16-bit
            if type_lower in ("small", "i16"):
                return ir.IntType(16)
            if type_lower in ("usmall", "u16"):
                return ir.IntType(16)
            # 32-bit
            if type_lower in ("short", "i32"):
                return ir.IntType(32)
            if type_lower in ("ushort", "u32"):
                return ir.IntType(32)
            # 64-bit
            if type_lower in ("int", "i64"):
                return ir.IntType(64)
            if type_lower in ("uint", "u64"):
                return ir.IntType(64)
            # 128-bit
            if type_lower in ("long", "i128"):
                return ir.IntType(128)
            if type_lower in ("ulong", "u128"):
                return ir.IntType(128)
            # 256-bit
            if type_lower in ("wide", "i256"):
                return ir.IntType(256)
            if type_lower in ("uwide", "u256"):
                return ir.IntType(256)
            # 512-bit
            if type_lower in ("vast", "i512"):
                return ir.IntType(512)
            if type_lower in ("uvast", "u512"):
                return ir.IntType(512)
            # 1024-bit
            if type_lower in ("grand", "i1024"):
                return ir.IntType(1024)
            if type_lower in ("ugrand", "u1024"):
                return ir.IntType(1024)
            # 2048-bit
            if type_lower in ("giant", "i2048"):
                return ir.IntType(2048)
            if type_lower in ("ugiant", "u2048"):
                return ir.IntType(2048)
            # 4096-bit
            if type_lower in ("titan", "i4096"):
                return ir.IntType(4096)
            if type_lower in ("utitan", "u4096"):
                return ir.IntType(4096)
            # 8192-bit (1KB integers!)
            if type_lower in ("colos", "i8192"):
                return ir.IntType(8192)
            if type_lower in ("ucolos", "u8192"):
                return ir.IntType(8192)
            # Arbitrary precision (unbounded) - pointer to bigint struct
            if type_lower == "unbounded":
                return self._get_bigint_type()
            if type_lower == "float":
                return ir.FloatType()
            if type_lower == "double":
                return ir.DoubleType()
            if type_lower == "quad":
                # llvmlite doesn't support fp128, use double as fallback
                # M7 fix: Emit warning so users know about precision loss

                print(
                    "Warning: 'quad' type uses double precision (64-bit) "
                    "as llvmlite lacks fp128 support",
                    file=sys.stderr,
                )
                return ir.DoubleType()
            if type_lower == "string":
                return ir.IntType(8).as_pointer()
            if type_lower == "array":
                # Dynamic array is a pointer to i64 (element storage)
                return ir.IntType(64).as_pointer()
            if type_lower == "str_array":
                # Dynamic string array is pointer to pointer (i8**)
                return ir.IntType(8).as_pointer().as_pointer()
            if type_lower == "dict":
                return self.get_dict_type().as_pointer()
            if type_lower == "ptr":
                return ir.IntType(8).as_pointer()
            if type_lower == "ptrptr":
                return ir.IntType(8).as_pointer().as_pointer()
            if type_lower in {"charpp", "size_tp"}:
                return ir.IntType(8).as_pointer().as_pointer()
            if type_lower == "fileptr":
                return ir.IntType(8).as_pointer()
            if type_lower == "void":
                return ir.VoidType()
            # SIMD vector types (SSE/AVX/AVX-512)
            # Byte vectors (for text processing, SIMD lexer)
            if type_lower == "vec16b":  # SSE: 16 bytes
                return ir.VectorType(ir.IntType(8), 16)
            if type_lower == "vec32b":  # AVX2: 32 bytes
                return ir.VectorType(ir.IntType(8), 32)
            if type_lower == "vec64b":  # AVX-512: 64 bytes
                return ir.VectorType(ir.IntType(8), 64)
            # Integer vectors
            if type_lower == "vec4i":  # SSE: 4 x i32
                return ir.VectorType(ir.IntType(32), 4)
            if type_lower == "vec8i":  # AVX2: 8 x i32
                return ir.VectorType(ir.IntType(32), 8)
            if type_lower == "vec16i":  # AVX-512: 16 x i32
                return ir.VectorType(ir.IntType(32), 16)
            # Long vectors
            if type_lower == "vec2l":  # SSE: 2 x i64
                return ir.VectorType(ir.IntType(64), 2)
            if type_lower == "vec4l":  # AVX2: 4 x i64
                return ir.VectorType(ir.IntType(64), 4)
            if type_lower == "vec8l":  # AVX-512: 8 x i64
                return ir.VectorType(ir.IntType(64), 8)
            # Float vectors
            if type_lower == "vec4f":  # SSE: 4 x f32
                return ir.VectorType(ir.FloatType(), 4)
            if type_lower == "vec8f":  # AVX2: 8 x f32
                return ir.VectorType(ir.FloatType(), 8)
            # Double vectors
            if type_lower == "vec2d":  # SSE: 2 x f64
                return ir.VectorType(ir.DoubleType(), 2)
            if type_lower == "vec4d":  # AVX2: 4 x f64
                return ir.VectorType(ir.DoubleType(), 4)
            # Check for class types FIRST (pointer to class struct)
            # Classes are also in record_types, but we want pointer semantics for params
            if type_spec in self.class_types:
                return self.class_types[type_spec].as_pointer()
            # Check for data-carrying enums (pointer to tagged union)
            if type_spec in self.data_enum_types:
                return self.data_enum_types[type_spec].as_pointer()
            if type_spec in getattr(self, "opaque_record_names", set()):
                return ir.IntType(8).as_pointer()
            if type_spec in self.record_types:
                return self.record_types[type_spec]
            # PascalCase identifiers are assumed to be forward-declared class types
            # Return i8* (generic pointer) and let codegen resolve later
            if type_spec and type_spec[0].isupper() and not type_spec.isupper():
                return ir.IntType(8).as_pointer()

        # Unknown type - warn and fall back to i64 for compatibility.
        # This should ideally be a hard error, but some stdlib C function
        # declarations rely on the fallback.  Emit a warning so callers
        # notice the silent coercion.

        warnings.warn(
            f"get_llvm_type: unknown type '{type_spec}', defaulting to i64",
            stacklevel=2,
        )
        return ir.IntType(64)

    def get_variable_class_type(self, var_name: str) -> Optional[str]:
        """Get the class type of a variable if it was annotated with a class type.

        Checks:
        1. Current function's parameter type annotations
        2. Local variable type tracking (TODO)

        Returns the class name or None if not a class type.
        """
        # Check current function's parameter annotations
        if self.current_function:
            func_name = self.current_function.name
            # Remove underscore prefix if private function
            if func_name.startswith("_"):
                func_name = func_name[1:]

            key = (func_name, var_name)
            if key in self.param_class_types:
                return self.param_class_types[key]
        local_type = getattr(self, "local_decl_types", {}).get(var_name)
        if isinstance(local_type, str) and local_type in getattr(
            self, "class_types", {}
        ):
            return local_type
        return None

    def get_record_name_from_type(self, struct_type: ir.Type) -> str:
        """Finds the record name corresponding to an LLVM struct type."""
        name = self.record_type_ids.get(id(struct_type))
        if name:
            return name
        for n, rtype in self.record_types.items():
            if rtype is struct_type:
                return n
        raise CodeGenError("Unknown record type")

    def get_field_info(self, record_name: str, field_name: str) -> tuple[int, str]:
        """Gets the index and type string for a field in a record."""
        fields = self.record_fields[record_name]
        for i, (fname, ftype) in enumerate(fields):
            if fname == field_name:
                return i, ftype
        raise CodeGenError(f"Field '{field_name}' not found in {record_name}")

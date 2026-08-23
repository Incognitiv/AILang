"""Array/collection/comptime visitors for LLVM expression generation.

Extracted from ``emit_expressions.py`` as part of the LLVM-side
ExprGenerator decomposition. Method bodies are unchanged.
"""

from __future__ import annotations

from parser.ast import ArrayAccess, Cast, DictAccess, DictLit, StringSlice, Variable
from typing import Any

from llvmlite import ir
from target_info import os_from_triple
from transpiler.arithmetic_literal_proofs import int_literal_in_range
from transpiler.expr_common import ExprGenError
from transpiler.llvm_fixed_dicts import try_fixed_dict_access


class ExprCollectionEmitter:
    """Array/dict/tuple/generic/comptime expression service for ``ExprGenerator``."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def visit_ArrayAccess(self, node: ArrayAccess):
        """Handle array[index] or dict[key] access.

        Determines at runtime whether this is array or dict access based on:
        1. If index is a string -> dict access
        2. If container is a dict type -> dict access
        3. Otherwise -> array access

        If node.unsafe is True, bounds checking is skipped (user approved).
        """
        fixed_dict_value = try_fixed_dict_access(self.codegen, node.array, node.index)
        if fixed_dict_value is not None:
            return fixed_dict_value

        # First, generate the key/index expression to check its type
        key_val = self.generate_expr(node.index)

        # Check if key is a string (pointer to i8) - indicates dict access
        is_string_key = isinstance(
            key_val.type, ir.PointerType
        ) and key_val.type.pointee == ir.IntType(8)

        if is_string_key:
            # This is dict access: dict[string_key]
            dict_ptr = self.generate_expr(node.array)
            dict_get = self.codegen.get_dict_get_func()
            result = self.builder.call(dict_get, [dict_ptr, key_val], name="dict_val")
            return result

        # Regular array access. LLVM GEP accepts any integer index width; keep
        # proven-narrow indices narrow instead of forcing an i64 sext.
        index_val = _gep_index_value(self, key_val)

        if (
            isinstance(node.array, Variable)
            and node.array.name in self.codegen.array_metadata
        ):
            arr_name = node.array.name
            array_len, elem_type = self.codegen.array_metadata[arr_name]

            # Check if array is local or global
            if arr_name in self.codegen.locals:
                storage = self.codegen.locals[arr_name]
                if isinstance(storage.type, ir.PointerType):
                    if isinstance(storage.type.pointee, ir.ArrayType):
                        array_ptr = self.builder.gep(
                            storage,
                            [
                                ir.Constant(ir.IntType(32), 0),
                                ir.Constant(ir.IntType(32), 0),
                            ],
                            name=f"{arr_name}_ptr",
                        )
                    else:
                        array_ptr = self.builder.load(storage, name=f"{arr_name}_ptr")
                else:
                    array_ptr = storage
            elif arr_name in self.codegen.globals:
                # Global array - get pointer to first element
                global_var = self.codegen.globals[arr_name]
                array_ptr = self.builder.gep(
                    global_var,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    name=f"{arr_name}_ptr",
                )
            else:
                raise TypeError(f"Array {arr_name} not found in locals or globals")

            typed_ptr = self.builder.bitcast(
                array_ptr, elem_type.as_pointer(), name="array_typed"
            )
            if not getattr(node, "unsafe", False):
                can_elide, _reason = self._can_elide_array_bounds(node, array_len)
                if not can_elide:
                    self.codegen.check_bounds(index_val, array_len)
            elem_ptr = self.builder.gep(typed_ptr, [index_val], name="elem_ptr")
            return self.builder.load(elem_ptr, name="elem_val")

        array_val = self.generate_expr(node.array)
        if not isinstance(array_val.type, ir.PointerType):
            raise TypeError("Array access on non-pointer value")

        # Dynamic bounds check for arrays without compile-time metadata
        # Assumes dynamic array with header: [length, capacity, data...]
        # Skip if unsafe flag is set
        if not getattr(node, "unsafe", False):
            array_len = self._known_dynamic_array_len(node)
            can_elide = False
            if array_len is not None:
                can_elide, _reason = self._can_elide_array_bounds(node, array_len)
            if not can_elide:
                # Load length from header (offset -2 from data pointer)
                hdr_ptr = self.builder.gep(
                    array_val,
                    [ir.Constant(ir.IntType(32), -2)],
                    name="dyn_arr_hdr",
                )
                arr_length = self.builder.load(hdr_ptr, name="dyn_arr_len")
                self.codegen.check_bounds_dynamic(index_val, arr_length)

        elem_ptr = self.builder.gep(array_val, [index_val], name="elem_ptr")
        return self.builder.load(elem_ptr, name="elem_val")

    def _known_dynamic_array_len(self, node: ArrayAccess) -> int | None:
        if not isinstance(node.array, Variable):
            return None
        facts = getattr(self.codegen, "range_facts", None)
        if facts is None or not hasattr(facts, "get_array_info"):
            return None
        scope = getattr(self.codegen, "_current_function_name", None)
        try:
            info = facts.get_array_info(node.array.name, scope)
        except Exception:
            return None
        if info is None:
            return None
        _elem_range, array_len = info
        return int(array_len)

    def _can_elide_array_bounds(
        self, node: ArrayAccess, array_len: int
    ) -> tuple[bool, str]:
        if int_literal_in_range(node.index, 0, array_len):
            return True, "literal_index_in_bounds"
        facts = getattr(self.codegen, "range_facts", None)
        if facts is None:
            return False, "facts_missing"
        scope = getattr(self.codegen, "_current_function_name", None)
        try:
            if hasattr(facts, "explain_index_in_bounds"):
                proven, reason = facts.explain_index_in_bounds(
                    array_len, node.index, scope
                )
                return bool(proven), str(reason)
            proven = facts.can_prove_index_in_bounds(array_len, node.index, scope)
        except Exception:
            return False, "proof_failed"
        return bool(proven), ("range_proven" if proven else "range_unknown")

    def visit_StringSlice(self, node: StringSlice):
        """Handle string slicing: s[start:end] -> substr(s, start, end-start)

        If end is None, slices to the end of the string.
        Bounds are clamped to [0, strlen] to prevent OOB reads.
        """
        # Get the string pointer and its length
        s_ptr = self.generate_expr(node.target)
        start_val = self.ensure_int64(self.generate_expr(node.start))

        i64 = ir.IntType(64)
        zero = ir.Constant(i64, 0)

        # Get actual string length for clamping
        s_len = self.builder.call(self.codegen.get_strlen(), [s_ptr], name="slice_slen")

        # Clamp start to [0, strlen]
        start_neg = self.builder.icmp_signed("<", start_val, zero)
        start_clamped = self.builder.select(start_neg, zero, start_val, name="s_lo")
        start_too_big = self.builder.icmp_signed(">", start_clamped, s_len)
        start_clamped = self.builder.select(
            start_too_big, s_len, start_clamped, name="s_clamp"
        )

        if node.end is None:
            # s[start:] - slice to end of string
            length = self.builder.sub(s_len, start_clamped, name="slice_len")
        else:
            # s[start:end] - slice from start to end
            end_val = self.ensure_int64(self.generate_expr(node.end))
            # Clamp end to [start, strlen]
            end_lo = self.builder.icmp_signed("<", end_val, start_clamped)
            end_clamped = self.builder.select(
                end_lo, start_clamped, end_val, name="e_lo"
            )
            end_too_big = self.builder.icmp_signed(">", end_clamped, s_len)
            end_clamped = self.builder.select(
                end_too_big, s_len, end_clamped, name="e_clamp"
            )
            length = self.builder.sub(end_clamped, start_clamped, name="slice_len")

        # Allocate buffer: length + 1 for null terminator
        one = ir.Constant(i64, 1)
        alloc_size = self.builder.add(length, one, name="alloc_size")
        buf = self.codegen.string_alloc(alloc_size, "slice_buf")

        # Copy substring using memcpy
        src_ptr = self.builder.gep(
            s_ptr,
            [self.builder.trunc(start_clamped, ir.IntType(32))],
            name="slice_src",
        )
        self.builder.call(self.codegen.get_memcpy(), [buf, src_ptr, length])

        # Null-terminate
        term_ptr = self.builder.gep(
            buf,
            [self.builder.trunc(length, ir.IntType(32))],
            name="slice_term",
        )
        self.builder.store(ir.Constant(ir.IntType(8), 0), term_ptr)

        return buf

    def _get_dict_type_tag(self, val: ir.Value) -> ir.Constant:
        """Determine the type tag for a dict value.

        Type tags: 0=int, 1=float, 2=string, 3=bool, 4=pointer/array
        """
        val_type = val.type
        if isinstance(val_type, ir.IntType):
            if val_type.width == 1:
                return ir.Constant(ir.IntType(8), 3)  # bool
            return ir.Constant(ir.IntType(8), 0)  # int
        if isinstance(val_type, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(ir.IntType(8), 1)  # float
        if isinstance(val_type, ir.PointerType):
            # Check if it's a string (i8*) or other pointer
            if isinstance(val_type.pointee, ir.IntType) and val_type.pointee.width == 8:
                return ir.Constant(ir.IntType(8), 2)  # string (i8*)
            return ir.Constant(ir.IntType(8), 4)  # pointer/array
        return ir.Constant(ir.IntType(8), 0)  # default to int

    def _convert_dict_value(self, val: ir.Value) -> ir.Value:
        """Convert a value to i64 for dict storage, preserving bits.

        Floats are bitcast, pointers are ptrtoint, ints are extended/truncated.
        """
        val_type = val.type
        i64 = ir.IntType(64)

        if val_type == i64:
            return val
        if isinstance(val_type, ir.IntType):
            if val_type.width < 64:
                return self.builder.zext(val, i64, name="dict_int_ext")
            if val_type.width > 64:
                return self.builder.trunc(val, i64, name="dict_int_trunc")
            return val
        if isinstance(val_type, ir.FloatType):
            # float (32-bit) -> bitcast to i32 -> zext to i64
            i32 = ir.IntType(32)
            as_i32 = self.builder.bitcast(val, i32, name="f32_to_i32")
            return self.builder.zext(as_i32, i64, name="dict_float_ext")
        if isinstance(val_type, ir.DoubleType):
            # double (64-bit) -> bitcast to i64
            return self.builder.bitcast(val, i64, name="f64_to_i64")
        if isinstance(val_type, ir.PointerType):
            return self.builder.ptrtoint(val, i64, name="dict_ptr_int")
        # Fallback: attempt cast
        return self.cast_value(val, i64)

    def visit_DictLit(self, node: DictLit):
        """Create a dictionary literal with auto-type detection.

        Dict structure (smart dict with type tags):
        - capacity: i64
        - size: i64
        - keys: i8** (array of string pointers)
        - values: i64* (array of i64 - stores all types as bits)
        - types: i8* (array of type tags)

        Type tags: 0=int, 1=float, 2=string, 3=bool, 4=pointer/array
        """
        # Get or declare dict helper functions
        dict_create = self.codegen.get_dict_create_func()
        dict_set = self.codegen.get_dict_set_func()

        # Create empty dict with capacity for all pairs (+ some extra)
        capacity = ir.Constant(ir.IntType(64), max(len(node.pairs) * 2, 16))
        dict_ptr = self.builder.call(dict_create, [capacity], name="dict_ptr")

        # Insert all key-value pairs
        for key_expr, val_expr in node.pairs:
            key_val = self.generate_expr(key_expr)
            val_val = self.generate_expr(val_expr)

            # Convert key to i8* (string pointer)
            if isinstance(key_val.type, ir.PointerType):
                key_ptr = key_val
            else:
                # For non-string keys, we'd need to convert - for now assume strings
                raise ExprGenError("Dict keys must be strings")

            # Detect type and get tag BEFORE converting to i64
            type_tag = self._get_dict_type_tag(val_val)

            # Convert value to i64 (preserving bits for float/pointer)
            val_i64 = self._convert_dict_value(val_val)

            self.builder.call(dict_set, [dict_ptr, key_ptr, val_i64, type_tag])

        return dict_ptr

    def visit_DictAccess(self, node: DictAccess):
        """Access dictionary: dict[key]"""
        fixed_dict_value = try_fixed_dict_access(
            self.codegen, node.dict_expr, node.key_expr
        )
        if fixed_dict_value is not None:
            return fixed_dict_value

        dict_ptr = self.generate_expr(node.dict_expr)
        key_val = self.generate_expr(node.key_expr)

        # Get dict_get function
        dict_get = self.codegen.get_dict_get_func()

        # Key must be string pointer
        if isinstance(key_val.type, ir.PointerType):
            key_ptr = key_val
        else:
            raise ExprGenError("Dict keys must be strings")

        result = self.builder.call(dict_get, [dict_ptr, key_ptr], name="dict_val")
        return result

    def visit_TupleLit(self, node):
        """Create a tuple literal: (a, b, c)

        Tuples are represented as LLVM struct types with heterogeneous elements.
        Returns a pointer to the allocated tuple.
        """
        if not node.elements:
            raise ExprGenError("Empty tuple literals are not supported")

        # Generate all element values first to determine their types
        element_values = [self.generate_expr(elem) for elem in node.elements]
        element_types = [val.type for val in element_values]

        # Create struct type for this tuple
        tuple_type = ir.LiteralStructType(element_types)
        tuple_ptr = self.builder.alloca(tuple_type, name="tuple_lit")

        # Store each element
        for i, val in enumerate(element_values):
            elem_ptr = self.builder.gep(
                tuple_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)],
                name=f"tuple_elem_{i}_ptr",
            )
            self.builder.store(val, elem_ptr)

        return tuple_ptr

    def visit_TupleAccess(self, node):
        """Access tuple element: tuple.0, tuple.1, etc."""
        tuple_val = self.generate_expr(node.tuple_expr)

        # tuple_val should be a pointer to a struct
        if not isinstance(tuple_val.type, ir.PointerType):
            raise ExprGenError(f"Tuple access requires pointer, got {tuple_val.type}")

        # Get the element at the specified index
        elem_ptr = self.builder.gep(
            tuple_val,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), node.index)],
            name=f"tuple_access_{node.index}",
        )
        return self.builder.load(elem_ptr, name=f"tuple_val_{node.index}")

    def visit_GenericInstantiation(self, node):
        """Handle generic type instantiation: Vec[int], Pair[int, string].

        Triggers monomorphization to create a specialized type.
        Returns the mangled type name for use in NewExpr.
        """
        from parser import ast as A

        # Use the monomorphizer to get or create the specialized type
        mangled_name = self.codegen.monomorphizer.instantiate(
            node.base_type, node.type_args
        )

        # Generate any newly specialized definitions
        for specialized in self.codegen.monomorphizer.get_specialized_definitions():
            if specialized.name == mangled_name:
                if isinstance(specialized, A.RecordDef):
                    self.codegen.generate_record(specialized)
                elif isinstance(specialized, A.ClassDef):
                    self.codegen.generate_class(specialized)

        return mangled_name

    def visit_ComptimeExpr(self, node):
        """Evaluate compile-time expression.

        Computes the expression at compile time and returns a constant.
        """
        result = self._evaluate_comptime(node.expr)
        if result is not None:
            if isinstance(result, bool):
                return ir.Constant(ir.IntType(1), 1 if result else 0)
            if isinstance(result, int):
                return ir.Constant(ir.IntType(64), result)
            if isinstance(result, float):
                return ir.Constant(ir.DoubleType(), result)
            if isinstance(result, str):
                return self.codegen.create_string_constant(result)

        # If can't evaluate at compile time, fall back to runtime
        return self.generate_expr(node.expr)

    def _evaluate_comptime(self, expr):
        """Evaluate expression at compile time if possible."""
        from parser import ast as A

        if isinstance(expr, A.Number):
            if expr.is_float:
                return float(expr.value)
            return int(expr.value)

        if isinstance(expr, A.Bool):
            return expr.value

        if isinstance(expr, A.StringLit):
            return expr.value

        if isinstance(expr, A.Call):
            if expr.args:
                return None
            if expr.name == "target_os":
                return os_from_triple(self.codegen.module.triple)
            if expr.name == "target_backend":
                return "llvm"

        if isinstance(expr, A.BinaryOp):
            left = self._evaluate_comptime(expr.left)
            right = self._evaluate_comptime(expr.right)
            if left is None or right is None:
                return None

            op = expr.op
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                if right == 0:
                    return None
                return left // right if isinstance(left, int) else left / right
            if op == "%":
                return left % right
            if op == "**":
                return left**right
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == "<":
                return left < right
            if op == ">":
                return left > right
            if op == "<=":
                return left <= right
            if op == ">=":
                return left >= right

        if isinstance(expr, A.UnaryOp):
            operand = self._evaluate_comptime(expr.operand)
            if operand is None:
                return None
            if expr.op == "-":
                return -operand
            if expr.op in ("not", "!"):
                return not operand

        return None

    def visit_Cast(self, node: Cast):
        value = self.generate_expr(node.expr)
        target_type = self.codegen.get_llvm_type(node.target_type)
        return self.cast_value(
            value, target_type, unsigned=self.codegen.is_unsigned_value(value)
        )


def _gep_index_value(emitter: ExprCollectionEmitter, value: ir.Value) -> ir.Value:
    if isinstance(value.type, ir.IntType):
        return value
    return emitter.ensure_int64(value)

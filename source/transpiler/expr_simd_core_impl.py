"""Core SIMD/vector builtin implementations."""

from __future__ import annotations

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ARG_THIRD, ExprGenError
from transpiler.expr_simd_host import _SimdBuiltinsHostMixin


class _SimdBuiltinsCoreMixin(_SimdBuiltinsHostMixin):

    def _builtin_vec_load(self, args):
        """Load a vector from memory: vec_load(ptr, vec_type) -> vector
        Example: v = vec_load(data_ptr, "vec32b")  // Load 32 bytes
        """
        if len(args) < 1:
            raise ExprGenError("vec_load() expects at least (ptr)")
        ptr = self.generate_expr(args[ARG_FIRST])
        # Determine vector type from second arg or default to vec32b
        if len(args) >= 2 and hasattr(args[ARG_SECOND], "value"):
            vec_type = self.codegen.get_llvm_type(args[ARG_SECOND].value)
        else:
            vec_type = ir.VectorType(ir.IntType(8), 32)  # Default: AVX2 32 bytes
        # Cast pointer to vector pointer
        vec_ptr_type = vec_type.as_pointer()
        vec_ptr = self.builder.bitcast(ptr, vec_ptr_type, name="vec_ptr")
        # L8 fix: Use proper alignment based on vector size for optimal SIMD perf
        # Vector bytes = element count * element size in bits / 8
        elem_bits = vec_type.element.width if hasattr(vec_type.element, "width") else 8
        vec_bytes = vec_type.count * elem_bits // 8
        # Alignment should be min(vector_bytes, 64) for cache line efficiency
        alignment = min(vec_bytes, 64)
        return self.builder.load(vec_ptr, name="vec_load", align=alignment)

    def _builtin_vec_loadu(self, args):
        """Load a vector from UNALIGNED memory: vec_loadu(ptr, vec_type).
        Same as vec_load but emits an unaligned load (align=1) so the
        pointer can come from malloc / alloc which only guarantee 8 or 16
        byte alignment. Slightly slower than aligned vec_load on older
        architectures; near-identical on modern x86 (Haswell+).
        """
        if len(args) < 1:
            raise ExprGenError("vec_loadu() expects at least (ptr)")
        ptr = self.generate_expr(args[ARG_FIRST])
        if len(args) >= 2 and hasattr(args[ARG_SECOND], "value"):
            vec_type = self.codegen.get_llvm_type(args[ARG_SECOND].value)
        else:
            vec_type = ir.VectorType(ir.IntType(8), 32)
        vec_ptr_type = vec_type.as_pointer()
        vec_ptr = self.builder.bitcast(ptr, vec_ptr_type, name="vec_uptr")
        return self.builder.load(vec_ptr, name="vec_loadu", align=1)

    def _builtin_vec_storeu(self, args):
        """Store a vector to UNALIGNED memory: vec_storeu(ptr, vec).
        Companion to vec_loadu. Use for malloc-backed buffers.
        """
        if len(args) != 2:
            raise ExprGenError("vec_storeu() expects (ptr, vector)")
        ptr = self.generate_expr(args[ARG_FIRST])
        vec = self.generate_expr(args[ARG_SECOND])
        vec_ptr_type = vec.type.as_pointer()
        vec_ptr = self.builder.bitcast(ptr, vec_ptr_type, name="vec_storeu_ptr")
        self.builder.store(vec, vec_ptr, align=1)
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_vec_store(self, args):
        """Store a vector to memory: vec_store(ptr, vec)
        Example: vec_store(dest_ptr, v)
        """
        if len(args) != 2:
            raise ExprGenError("vec_store() expects (ptr, vector)")
        ptr = self.generate_expr(args[ARG_FIRST])
        vec = self.generate_expr(args[ARG_SECOND])
        # Cast pointer to vector pointer
        vec_ptr_type = vec.type.as_pointer()
        vec_ptr = self.builder.bitcast(ptr, vec_ptr_type, name="vec_store_ptr")
        # L8 fix: Use proper alignment based on vector size for optimal SIMD perf
        vec_type = vec.type
        elem_bits = vec_type.element.width if hasattr(vec_type.element, "width") else 8
        vec_bytes = vec_type.count * elem_bits // 8
        alignment = min(vec_bytes, 64)
        self.builder.store(vec, vec_ptr, align=alignment)
        return ir.Constant(ir.IntType(64), 0)

    def _builtin_vec_broadcast(self, args):
        """Broadcast a scalar to all vector lanes: vec_broadcast(val, vec_type) -> vector
        Example: v = vec_broadcast(32, "vec32b")  // Fill 32 bytes with space char
        """
        if len(args) < 1:
            raise ExprGenError("vec_broadcast() expects at least (value)")
        val = self.generate_expr(args[ARG_FIRST])
        # Determine vector type
        if len(args) >= 2 and hasattr(args[ARG_SECOND], "value"):
            vec_type = self.codegen.get_llvm_type(args[ARG_SECOND].value)
        else:
            vec_type = ir.VectorType(ir.IntType(8), 32)  # Default: vec32b
        elem_type = vec_type.element
        count = vec_type.count
        # Truncate/extend value to element type
        if isinstance(val.type, ir.IntType) and isinstance(elem_type, ir.IntType):
            if val.type.width > elem_type.width:
                val = self.builder.trunc(val, elem_type, name="broadcast_trunc")
            elif val.type.width < elem_type.width:
                val = self.builder.zext(val, elem_type, name="broadcast_zext")
        # Create broadcast using insertelement + shufflevector
        undef = ir.Constant(vec_type, ir.Undefined)
        vec = self.builder.insert_element(
            undef, val, ir.Constant(ir.IntType(32), 0), name="broadcast_insert"
        )
        # Shuffle to broadcast to all lanes
        mask = ir.Constant(ir.VectorType(ir.IntType(32), count), [0] * count)
        return self.builder.shuffle_vector(vec, undef, mask, name="broadcast")

    def _builtin_vec_binop(self, args, op: str):
        """Binary vector operation: vec_add/sub/mul/and/or/xor(a, b) -> vector
        Example: result = vec_add(v1, v2)
        """
        if len(args) != 2:
            raise ExprGenError(f"vec_{op}() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        if op == "add":
            return self.builder.add(a, b, name="vec_add")
        if op == "sub":
            return self.builder.sub(a, b, name="vec_sub")
        if op == "mul":
            return self.builder.mul(a, b, name="vec_mul")
        if op == "and":
            return self.builder.and_(a, b, name="vec_and")
        if op == "or":
            return self.builder.or_(a, b, name="vec_or")
        if op == "xor":
            return self.builder.xor(a, b, name="vec_xor")
        if op == "nand":
            # NAND = NOT(a AND b)
            and_result = self.builder.and_(a, b, name="vec_nand_and")
            all_ones = ir.Constant(a.type, [-1] * a.type.count)
            return self.builder.xor(and_result, all_ones, name="vec_nand")
        if op == "nor":
            # NOR = NOT(a OR b)
            or_result = self.builder.or_(a, b, name="vec_nor_or")
            all_ones = ir.Constant(a.type, [-1] * a.type.count)
            return self.builder.xor(or_result, all_ones, name="vec_nor")
        if op == "xnor":
            # XNOR = NOT(a XOR b) = a XNOR b
            xor_result = self.builder.xor(a, b, name="vec_xnor_xor")
            all_ones = ir.Constant(a.type, [-1] * a.type.count)
            return self.builder.xor(xor_result, all_ones, name="vec_xnor")
        if op == "shr":
            # Logical (zero-fill) shift right. For arithmetic/sign-preserving
            # shift, a separate vec_sar would use builder.ashr.
            return self.builder.lshr(a, b, name="vec_shr")
        if op == "shl":
            return self.builder.shl(a, b, name="vec_shl")
        raise ExprGenError(f"Unknown vec op: {op}")

    def _builtin_vec_not(self, args):
        """Vector NOT (invert all bits): vec_not(a) -> vector
        Example: inverted = vec_not(mask)
        """
        if len(args) != 1:
            raise ExprGenError("vec_not() expects (vector)")
        a = self.generate_expr(args[ARG_FIRST])
        all_ones = ir.Constant(a.type, [-1] * a.type.count)
        return self.builder.xor(a, all_ones, name="vec_not")

    def _builtin_vec_cmp(self, args, pred: str):
        """Compare vectors element-wise: vec_cmpeq(a, b) -> mask vector
        Returns a vector where each element is all-1s (true) or all-0s (false).
        Example: mask = vec_cmpeq(data, spaces)  // Find all spaces
        """
        if len(args) != 2:
            raise ExprGenError(f"vec_cmp{pred}() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        # LLVM vector comparison returns <N x i1>, we need to sext to <N x i8>
        if pred == "==":
            cmp_result = self.builder.icmp_unsigned("==", a, b, name="vec_cmpeq_i1")
        elif pred == ">":
            cmp_result = self.builder.icmp_signed(">", a, b, name="vec_cmpgt_i1")
        elif pred == "<":
            cmp_result = self.builder.icmp_signed("<", a, b, name="vec_cmplt_i1")
        else:
            raise ExprGenError(f"Unknown vec cmp: {pred}")
        # Sign-extend i1 to i8 (0 -> 0x00, 1 -> 0xFF)
        return self.builder.sext(cmp_result, a.type, name="vec_cmp_mask")

    def _builtin_vec_movemask(self, args):
        """Extract high bits from each byte into an integer: vec_movemask(v) -> int
        This is the key to SIMD string processing - converts vector comparison
        result to a bitmask for fast scanning.
        Example:
            mask = vec_cmpeq(data, newlines)  // Compare 32 bytes
            bits = vec_movemask(mask)          // Get 32-bit mask
            // bits has bit N set if byte N matched
        Implementation: Uses native x86 intrinsics (pmovmskb) when available,
        falls back to portable implementation otherwise.
        """
        if len(args) != 1:
            raise ExprGenError("vec_movemask() expects (vector)")
        vec = self.generate_expr(args[ARG_FIRST])
        if not isinstance(vec.type, ir.VectorType):
            raise ExprGenError(f"vec_movemask expects vector, got {vec.type}")
        count = vec.type.count
        # Try to use native x86 intrinsics for maximum JIT performance
        # These compile to single instructions: pmovmskb (SSE2), vpmovmskb (AVX2)
        if (
            count == 16
            and isinstance(vec.type.element, ir.IntType)
            and vec.type.element.width == 8
        ):
            # SSE2: pmovmskb - 16 bytes -> 16-bit mask
            return self._vec_movemask_sse2(vec)
        if (
            count == 32
            and isinstance(vec.type.element, ir.IntType)
            and vec.type.element.width == 8
        ):
            # AVX2: vpmovmskb - 32 bytes -> 32-bit mask
            return self._vec_movemask_avx2(vec)
        if (
            count == 64
            and isinstance(vec.type.element, ir.IntType)
            and vec.type.element.width == 8
        ):
            # AVX-512: Use two AVX2 operations or portable fallback
            return self._vec_movemask_avx512(vec)
        # Portable fallback for non-standard vector sizes
        return self._vec_movemask_portable(vec)

    def _vec_movemask_sse2(self, vec):
        """SSE2 pmovmskb: <16 x i8> -> i32 (16-bit mask)"""
        intrinsic_name = "llvm.x86.sse2.pmovmskb.128"
        if intrinsic_name not in self.codegen.functions:
            # pmovmskb takes <16 x i8>, returns i32
            func_ty = ir.FunctionType(
                ir.IntType(32), [ir.VectorType(ir.IntType(8), 16)]
            )
            func = ir.Function(self.codegen.module, func_ty, intrinsic_name)
            self.codegen.functions[intrinsic_name] = func
        func = self.codegen.functions[intrinsic_name]
        result = self.builder.call(func, [vec], name="pmovmskb")
        return self.builder.zext(result, ir.IntType(64), name="movemask_i64")

    def _vec_movemask_avx2(self, vec):
        """AVX2 vpmovmskb: <32 x i8> -> i32 (32-bit mask)"""
        intrinsic_name = "llvm.x86.avx2.pmovmskb"
        if intrinsic_name not in self.codegen.functions:
            # vpmovmskb takes <32 x i8>, returns i32
            func_ty = ir.FunctionType(
                ir.IntType(32), [ir.VectorType(ir.IntType(8), 32)]
            )
            func = ir.Function(self.codegen.module, func_ty, intrinsic_name)
            self.codegen.functions[intrinsic_name] = func
        func = self.codegen.functions[intrinsic_name]
        result = self.builder.call(func, [vec], name="vpmovmskb")
        return self.builder.zext(result, ir.IntType(64), name="movemask_i64")

    def _vec_movemask_avx512(self, vec):
        """AVX-512: Use native vpmovmskb.512 or fallback to two AVX2 ops"""
        # Try native AVX-512 intrinsic first
        intrinsic_name = "llvm.x86.avx512.cvtb2mask.512"  # Modern AVX-512 way
        try:
            if intrinsic_name not in self.codegen.functions:
                # This intrinsic takes <64 x i8> and returns i64 mask
                func_ty = ir.FunctionType(
                    ir.IntType(64), [ir.VectorType(ir.IntType(8), 64)]
                )
                func = ir.Function(self.codegen.module, func_ty, intrinsic_name)
                self.codegen.functions[intrinsic_name] = func
            func = self.codegen.functions[intrinsic_name]
            return self.builder.call(func, [vec], name="avx512_mask")
        except (KeyError, AttributeError, TypeError):
            # Expected fallback: AVX-512 intrinsic may not be available
            pass
        # Fallback: Split into two AVX2 operations
        # Extract low 32 bytes (indices 0-31)
        low_indices = list(range(32))
        undef = ir.Constant(vec.type, ir.Undefined)
        low_vec = self.builder.shuffle_vector(
            vec,
            undef,
            ir.Constant(ir.VectorType(ir.IntType(32), 32), low_indices),
            name="avx512_low",
        )
        # Extract high 32 bytes (indices 32-63)
        high_indices = [i + 32 for i in range(32)]
        high_vec = self.builder.shuffle_vector(
            vec,
            undef,
            ir.Constant(ir.VectorType(ir.IntType(32), 32), high_indices),
            name="avx512_high",
        )
        # Get movemask for each half
        low_mask = self._vec_movemask_avx2(low_vec)
        high_mask = self._vec_movemask_avx2(high_vec)
        # Combine: high_mask << 32 | low_mask
        high_shifted = self.builder.shl(
            high_mask, ir.Constant(ir.IntType(64), 32), name="high_shift"
        )
        return self.builder.or_(low_mask, high_shifted, name="avx512_mask")

    def _vec_movemask_portable(self, vec):
        """Portable movemask fallback for non-x86 or non-standard vectors"""
        count = vec.type.count
        result = ir.Constant(ir.IntType(64), 0)
        for i in range(count):
            elem = self.builder.extract_element(
                vec, ir.Constant(ir.IntType(32), i), name=f"mm_elem_{i}"
            )
            if isinstance(elem.type, ir.IntType) and elem.type.width == 8:
                shifted = self.builder.lshr(
                    elem, ir.Constant(ir.IntType(8), 7), name=f"mm_bit_{i}"
                )
                bit = self.builder.zext(shifted, ir.IntType(64), name=f"mm_zext_{i}")
                bit = self.builder.and_(
                    bit, ir.Constant(ir.IntType(64), 1), name=f"mm_mask_{i}"
                )
                bit = self.builder.shl(
                    bit, ir.Constant(ir.IntType(64), i), name=f"mm_shift_{i}"
                )
                result = self.builder.or_(result, bit, name=f"mm_or_{i}")
        return result

    def _builtin_vec_shuffle(self, args):
        """Shuffle/permute vector elements: vec_shuffle(vec, mask) -> vector
        Example: shuffled = vec_shuffle(v, mask)
        """
        if len(args) != 2:
            raise ExprGenError("vec_shuffle() expects (vector, mask)")
        vec = self.generate_expr(args[ARG_FIRST])
        mask = self.generate_expr(args[ARG_SECOND])
        undef = ir.Constant(vec.type, ir.Undefined)
        return self.builder.shuffle_vector(vec, undef, mask, name="vec_shuffle")

    def _builtin_vec_extract(self, args):
        """Extract a single element from vector: vec_extract(vec, idx) -> scalar
        Example: byte = vec_extract(v, 5)  // Get 5th byte
        """
        if len(args) != 2:
            raise ExprGenError("vec_extract() expects (vector, index)")
        vec = self.generate_expr(args[ARG_FIRST])
        idx = self.generate_expr(args[ARG_SECOND])
        # Truncate index to i32
        if isinstance(idx.type, ir.IntType) and idx.type.width > 32:
            idx = self.builder.trunc(idx, ir.IntType(32), name="extract_idx")
        elem = self.builder.extract_element(vec, idx, name="vec_extract")
        # Extend to i64 for consistency
        if isinstance(elem.type, ir.IntType) and elem.type.width < 64:
            return self.builder.zext(elem, ir.IntType(64), name="extract_ext")
        return elem

    def _builtin_vec_insert(self, args):
        """Insert a scalar into vector: vec_insert(vec, val, idx) -> vector
        Example: v = vec_insert(v, 65, 0)  // Set first byte to 'A'
        """
        if len(args) != 3:
            raise ExprGenError("vec_insert() expects (vector, value, index)")
        vec = self.generate_expr(args[ARG_FIRST])
        val = self.generate_expr(args[ARG_SECOND])
        idx = self.generate_expr(args[ARG_THIRD])
        # Convert value to element type
        elem_type = vec.type.element
        if isinstance(val.type, ir.IntType) and isinstance(elem_type, ir.IntType):
            if val.type.width > elem_type.width:
                val = self.builder.trunc(val, elem_type, name="insert_trunc")
            elif val.type.width < elem_type.width:
                val = self.builder.zext(val, elem_type, name="insert_zext")
        # Truncate index to i32
        if isinstance(idx.type, ir.IntType) and idx.type.width > 32:
            idx = self.builder.trunc(idx, ir.IntType(32), name="insert_idx")
        return self.builder.insert_element(vec, val, idx, name="vec_insert")

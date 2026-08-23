"""Advanced SIMD/vector builtin implementations."""

from __future__ import annotations

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ARG_THIRD, ExprGenError
from transpiler.expr_simd_host import _SimdBuiltinsHostMixin


class _SimdBuiltinsAdvancedMixin(_SimdBuiltinsHostMixin):

    def _builtin_vec_minmax(self, args, op: str):
        """Element-wise min/max: vec_min(a, b) or vec_max(a, b) -> vector
        SSE2: pminub/pmaxub for unsigned bytes, pminsd/pmaxsd for signed ints.
        Example:
            a = vec_broadcast(10, "vec16b")
            b = vec_broadcast(20, "vec16b")
            min_vec = vec_min(a, b)  // All elements = 10
        """
        if len(args) != 2:
            raise ExprGenError(f"vec_{op}() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        # Use LLVM's select with comparison
        if op == "min":
            cmp = self.builder.icmp_unsigned("<", a, b, name="min_cmp")
            return self.builder.select(cmp, a, b, name="vec_min")
        # max
        cmp = self.builder.icmp_unsigned(">", a, b, name="max_cmp")
        return self.builder.select(cmp, a, b, name="vec_max")

    def _builtin_vec_avg(self, args):
        """Unsigned average with rounding: vec_avg(a, b) -> vector
        SSE2: pavgb - computes (a + b + 1) >> 1 for each byte.
        Useful for image processing, blending.
        Example:
            a = vec_broadcast(100, "vec16b")
            b = vec_broadcast(200, "vec16b")
            avg = vec_avg(a, b)  // All elements = 150
        """
        if len(args) != 2:
            raise ExprGenError("vec_avg() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        # Extend to prevent overflow, add, round, shift
        elem_type = a.type.element
        if isinstance(elem_type, ir.IntType):
            width = elem_type.width
            wide_type = ir.IntType(width * 2)
            vec_wide_type = ir.VectorType(wide_type, a.type.count)
            a_wide = self.builder.zext(a, vec_wide_type, name="avg_a_wide")
            b_wide = self.builder.zext(b, vec_wide_type, name="avg_b_wide")
            one = ir.Constant(vec_wide_type, [1] * a.type.count)
            sum_val = self.builder.add(a_wide, b_wide, name="avg_sum")
            sum_round = self.builder.add(sum_val, one, name="avg_round")
            shifted = self.builder.lshr(
                sum_round,
                ir.Constant(vec_wide_type, [1] * a.type.count),
                name="avg_shift",
            )
            return self.builder.trunc(shifted, a.type, name="vec_avg")
        raise ExprGenError(f"vec_avg requires integer vector, got {a.type}")

    def _builtin_vec_sad(self, args):
        """Sum of Absolute Differences: vec_sad(a, b) -> int
        SSE2: psadbw - computes sum of |a[i] - b[i]| for all bytes.
        Critical for video encoding (motion estimation), image comparison.
        Example:
            a = vec_load(block1, "vec16b")
            b = vec_load(block2, "vec16b")
            diff = vec_sad(a, b)  // Total difference between blocks
        """
        if len(args) != 2:
            raise ExprGenError("vec_sad() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        # Compute |a - b| for each element, then sum
        count = a.type.count
        result = ir.Constant(ir.IntType(64), 0)
        for i in range(count):
            idx = ir.Constant(ir.IntType(32), i)
            ai = self.builder.extract_element(a, idx, name=f"sad_a_{i}")
            bi = self.builder.extract_element(b, idx, name=f"sad_b_{i}")
            # |a - b| = max(a, b) - min(a, b)
            cmp = self.builder.icmp_unsigned(">", ai, bi)
            max_val = self.builder.select(cmp, ai, bi)
            min_val = self.builder.select(cmp, bi, ai)
            diff = self.builder.sub(max_val, min_val, name=f"sad_diff_{i}")
            diff_ext = self.builder.zext(diff, ir.IntType(64), name=f"sad_ext_{i}")
            result = self.builder.add(result, diff_ext, name=f"sad_sum_{i}")
        return result

    def _builtin_vec_hadd(self, args):
        """Horizontal add: vec_hadd(a, b) -> vector
        SSE3: phaddw/phaddd - adds adjacent pairs of elements.
        Result[i] = a[2i] + a[2i+1] or b[2i] + b[2i+1]
        Example:
            a = [1, 2, 3, 4]  // vec4i
            b = [5, 6, 7, 8]
            result = vec_hadd(a, b)  // [3, 7, 11, 15]
        """
        if len(args) != 2:
            raise ExprGenError("vec_hadd() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        count = a.type.count
        result_elems = []
        # First half from a: a[0]+a[1], a[2]+a[3], ...
        for i in range(0, count, 2):
            idx0 = ir.Constant(ir.IntType(32), i)
            idx1 = ir.Constant(ir.IntType(32), i + 1)
            e0 = self.builder.extract_element(a, idx0)
            e1 = self.builder.extract_element(a, idx1)
            result_elems.append(self.builder.add(e0, e1))
        # Second half from b
        for i in range(0, count, 2):
            idx0 = ir.Constant(ir.IntType(32), i)
            idx1 = ir.Constant(ir.IntType(32), i + 1)
            e0 = self.builder.extract_element(b, idx0)
            e1 = self.builder.extract_element(b, idx1)
            result_elems.append(self.builder.add(e0, e1))
        # Build result vector
        result = ir.Constant(a.type, ir.Undefined)
        for i, elem in enumerate(result_elems):
            result = self.builder.insert_element(
                result, elem, ir.Constant(ir.IntType(32), i), name=f"hadd_{i}"
            )
        return result

    def _builtin_vec_shuffle_bytes(self, args):
        """Byte-level shuffle: vec_shuffle_bytes(data, indices) -> vector
        SSSE3: pshufb - each byte of indices selects a byte from data.
        If high bit of index is set, result byte is 0.
        Example:
            data = [a, b, c, d, ...]
            indices = [3, 2, 1, 0, ...]
            result = [d, c, b, a, ...]  // Reversed first 4 bytes
        """
        if len(args) != 2:
            raise ExprGenError("vec_shuffle_bytes() expects (data, indices)")
        data = self.generate_expr(args[ARG_FIRST])
        indices = self.generate_expr(args[ARG_SECOND])
        count = data.type.count
        result = ir.Constant(data.type, ir.Undefined)
        for i in range(count):
            idx_pos = ir.Constant(ir.IntType(32), i)
            idx_val = self.builder.extract_element(
                indices, idx_pos, name=f"shuf_idx_{i}"
            )
            # Check high bit for zero
            idx_i64 = self.builder.zext(idx_val, ir.IntType(64))
            high_bit = self.builder.and_(idx_i64, ir.Constant(ir.IntType(64), 0x80))
            is_zero = self.builder.icmp_unsigned(
                "!=", high_bit, ir.Constant(ir.IntType(64), 0)
            )
            # Get data element at index (masked to valid range)
            idx_masked = self.builder.and_(
                idx_val, ir.Constant(idx_val.type, count - 1)
            )
            idx_i32 = (
                self.builder.zext(idx_masked, ir.IntType(32))
                if idx_masked.type.width < 32
                else idx_masked
            )
            data_elem = self.builder.extract_element(
                data, idx_i32, name=f"shuf_data_{i}"
            )
            # Select 0 or data based on high bit
            zero = ir.Constant(data.type.element, 0)
            selected = self.builder.select(
                is_zero, zero, data_elem, name=f"shuf_sel_{i}"
            )
            result = self.builder.insert_element(
                result, selected, idx_pos, name=f"shuf_res_{i}"
            )
        return result

    def _builtin_vec_abs(self, args):
        """Absolute value: vec_abs(a) -> vector
        SSSE3: pabsb/pabsw/pabsd - absolute value of each signed element.
        Example:
            a = [-5, 3, -10, 7]
            result = vec_abs(a)  // [5, 3, 10, 7]
        """
        if len(args) != 1:
            raise ExprGenError("vec_abs() expects (vector)")
        a = self.generate_expr(args[ARG_FIRST])
        # abs(x) = x < 0 ? -x : x
        zero = ir.Constant(a.type, [0] * a.type.count)
        neg = self.builder.sub(zero, a, name="abs_neg")
        cmp = self.builder.icmp_signed("<", a, zero, name="abs_cmp")
        return self.builder.select(cmp, neg, a, name="vec_abs")

    def _builtin_vec_blend(self, args):
        """Conditional blend: vec_blend(a, b, mask) -> vector
        SSE4.1: pblendvb - selects elements from a or b based on mask.
        If mask[i] high bit is set, select b[i], else a[i].
        Example:
            a = [1, 2, 3, 4]
            b = [5, 6, 7, 8]
            mask = [0, 0xFF, 0, 0xFF]  // High bit set on elements 1, 3
            result = vec_blend(a, b, mask)  // [1, 6, 3, 8]
        """
        if len(args) != 3:
            raise ExprGenError("vec_blend() expects (vec_a, vec_b, mask)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        mask = self.generate_expr(args[ARG_THIRD])
        count = a.type.count
        result = ir.Constant(a.type, ir.Undefined)
        for i in range(count):
            idx = ir.Constant(ir.IntType(32), i)
            ai = self.builder.extract_element(a, idx)
            bi = self.builder.extract_element(b, idx)
            mi = self.builder.extract_element(mask, idx)
            # Check high bit
            mi_ext = self.builder.zext(mi, ir.IntType(64)) if mi.type.width < 64 else mi
            high_bit = self.builder.and_(mi_ext, ir.Constant(ir.IntType(64), 0x80))
            use_b = self.builder.icmp_unsigned(
                "!=", high_bit, ir.Constant(ir.IntType(64), 0)
            )
            selected = self.builder.select(use_b, bi, ai, name=f"blend_{i}")
            result = self.builder.insert_element(result, selected, idx)
        return result

    def _builtin_vec_dot(self, args):
        """Dot product: vec_dot(a, b) -> scalar
        SSE4.1: dpps/dppd - dot product with mask for which elements to include.
        Example:
            a = [1.0, 2.0, 3.0, 4.0]
            b = [5.0, 6.0, 7.0, 8.0]
            result = vec_dot(a, b)  // 1*5 + 2*6 + 3*7 + 4*8 = 70
        """
        if len(args) != 2:
            raise ExprGenError("vec_dot() expects (vec_a, vec_b)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        # Multiply element-wise, then sum
        prod = (
            self.builder.fmul(a, b, name="dot_prod")
            if isinstance(a.type.element, (ir.FloatType, ir.DoubleType))
            else self.builder.mul(a, b, name="dot_prod")
        )
        # Sum all elements
        count = a.type.count
        if isinstance(a.type.element, (ir.FloatType, ir.DoubleType)):
            result = ir.Constant(a.type.element, 0.0)
            for i in range(count):
                elem = self.builder.extract_element(
                    prod, ir.Constant(ir.IntType(32), i)
                )
                result = self.builder.fadd(result, elem, name=f"dot_sum_{i}")
        else:
            result = ir.Constant(ir.IntType(64), 0)
            for i in range(count):
                elem = self.builder.extract_element(
                    prod, ir.Constant(ir.IntType(32), i)
                )
                elem_ext = (
                    self.builder.zext(elem, ir.IntType(64))
                    if elem.type.width < 64
                    else elem
                )
                result = self.builder.add(result, elem_ext, name=f"dot_sum_{i}")
        return result

    def _builtin_vec_cmpstr(self, args):
        """String comparison: vec_cmpstr(str1, str2, mode) -> int
        SSE4.2: pcmpestri/pcmpistrm - compares strings in parallel.
        Returns index of first match/mismatch or mask.
        Modes:
            0 = Equal each (strcmp-like)
            1 = Equal any (strchr-like - find any char from set)
            2 = Equal ordered (strstr-like - find substring)
            3 = Equal ranges (check if chars in range)
        Note: Simplified implementation - full SSE4.2 string ops require
        inline assembly for best performance.
        """
        if len(args) < 2:
            raise ExprGenError("vec_cmpstr() expects (str1, str2, [mode])")
        str1 = self.generate_expr(args[ARG_FIRST])
        str2 = self.generate_expr(args[ARG_SECOND])
        # Simple equal-each comparison (mode 0)
        # Returns index of first difference or vector length if equal
        count = str1.type.count
        for i in range(count):
            idx = ir.Constant(ir.IntType(32), i)
            c1 = self.builder.extract_element(str1, idx)
            c2 = self.builder.extract_element(str2, idx)
            ne = self.builder.icmp_unsigned("!=", c1, c2, name=f"cmpstr_{i}")
            # If not equal, return index
            if i == 0:
                result_idx = self.builder.select(
                    ne,
                    ir.Constant(ir.IntType(64), i),
                    ir.Constant(ir.IntType(64), count),
                )
            else:
                prev_equal = self.builder.icmp_unsigned(
                    "==", result_idx, ir.Constant(ir.IntType(64), count)
                )
                should_update = self.builder.and_(prev_equal, ne)
                result_idx = self.builder.select(
                    should_update, ir.Constant(ir.IntType(64), i), result_idx
                )
        return result_idx

    def _builtin_vec_permute(self, args):
        """Cross-lane permute: vec_permute(vec, indices) -> vector
        AVX2: vpermq/vpermd - permute elements across 128-bit lanes.
        Unlike shuffle, can move data between high/low halves.
        Example:
            a = [0, 1, 2, 3, 4, 5, 6, 7]  // vec8i
            idx = [7, 6, 5, 4, 3, 2, 1, 0]
            result = vec_permute(a, idx)  // [7, 6, 5, 4, 3, 2, 1, 0]
        """
        if len(args) != 2:
            raise ExprGenError("vec_permute() expects (vector, indices)")
        vec = self.generate_expr(args[ARG_FIRST])
        indices = self.generate_expr(args[ARG_SECOND])
        count = vec.type.count
        result = ir.Constant(vec.type, ir.Undefined)
        for i in range(count):
            idx_pos = ir.Constant(ir.IntType(32), i)
            idx_val = self.builder.extract_element(indices, idx_pos)
            # Mask index to valid range
            idx_masked = self.builder.and_(
                idx_val, ir.Constant(idx_val.type, count - 1)
            )
            idx_i32 = (
                self.builder.zext(idx_masked, ir.IntType(32))
                if idx_masked.type.width < 32
                else (
                    self.builder.trunc(idx_masked, ir.IntType(32))
                    if idx_masked.type.width > 32
                    else idx_masked
                )
            )
            elem = self.builder.extract_element(vec, idx_i32, name=f"perm_elem_{i}")
            result = self.builder.insert_element(
                result, elem, idx_pos, name=f"perm_res_{i}"
            )
        return result

    def _builtin_vec_gather(self, args):
        """Gather from memory: vec_gather(base_ptr, indices, scale) -> vector
        AVX2: vpgatherdd/vpgatherdq - gather elements from non-contiguous memory.
        Loads base[indices[i] * scale] for each element.
        Example:
            data = [10, 20, 30, 40, 50]
            indices = [4, 2, 0, 3]
            result = vec_gather(data, indices, 1)  // [50, 30, 10, 40]
        """
        if len(args) < 2:
            raise ExprGenError("vec_gather() expects (base_ptr, indices, [scale])")
        base_ptr = self.generate_expr(args[ARG_FIRST])
        indices = self.generate_expr(args[ARG_SECOND])
        scale = 1
        if len(args) >= 3:
            scale_val = self.generate_expr(args[ARG_THIRD])
            if isinstance(scale_val, ir.Constant):
                scale = scale_val.constant
        count = indices.type.count
        elem_type = ir.IntType(64)  # Default to i64
        result_type = ir.VectorType(elem_type, count)
        result = ir.Constant(result_type, ir.Undefined)
        for i in range(count):
            idx_pos = ir.Constant(ir.IntType(32), i)
            idx_val = self.builder.extract_element(indices, idx_pos)
            # Compute address: base + idx * scale
            idx_i64 = (
                self.builder.zext(idx_val, ir.IntType(64))
                if idx_val.type.width < 64
                else idx_val
            )
            offset = self.builder.mul(idx_i64, ir.Constant(ir.IntType(64), scale))
            elem_ptr = self.builder.gep(base_ptr, [offset], name=f"gather_ptr_{i}")
            elem = self.builder.load(elem_ptr, name=f"gather_elem_{i}")
            if elem.type != elem_type:
                elem = (
                    self.builder.zext(elem, elem_type)
                    if isinstance(elem.type, ir.IntType)
                    else elem
                )
            result = self.builder.insert_element(
                result, elem, idx_pos, name=f"gather_res_{i}"
            )
        return result

    def _builtin_vec_compress(self, args):
        """Compress: vec_compress(vec, mask) -> vector
        AVX-512: vpcompressd/q - pack elements where mask bit is set.
        Elements with mask=1 are packed to the beginning.
        Example:
            vec = [a, b, c, d, e, f, g, h]
            mask = 0b10110010  // bits 1, 4, 5, 7 set
            result = [b, e, f, h, ?, ?, ?, ?]  // Compressed
        """
        if len(args) != 2:
            raise ExprGenError("vec_compress() expects (vector, mask)")
        vec = self.generate_expr(args[ARG_FIRST])
        mask = self.generate_expr(args[ARG_SECOND])
        count = vec.type.count
        result = ir.Constant(vec.type, ir.Undefined)
        # Track output position
        out_idx = self.builder.alloca(ir.IntType(32), name="compress_idx")
        self.builder.store(ir.Constant(ir.IntType(32), 0), out_idx)
        for i in range(count):
            # Check if bit i is set in mask
            bit = self.builder.and_(mask, ir.Constant(mask.type, 1 << i))
            is_set = self.builder.icmp_unsigned("!=", bit, ir.Constant(mask.type, 0))
            # If set, copy element to output position and increment
            with self.builder.if_then(is_set):
                cur_out = self.builder.load(out_idx)
                elem = self.builder.extract_element(vec, ir.Constant(ir.IntType(32), i))
                result = self.builder.insert_element(result, elem, cur_out)
                new_out = self.builder.add(cur_out, ir.Constant(ir.IntType(32), 1))
                self.builder.store(new_out, out_idx)
        return result

    def _builtin_vec_expand(self, args):
        """Expand: vec_expand(vec, mask) -> vector
        AVX-512: vpexpandd/q - opposite of compress.
        Takes packed elements and expands to positions where mask=1.
        Example:
            vec = [a, b, c, d, ?, ?, ?, ?]  // Only first 4 valid
            mask = 0b10110010
            result = [?, a, ?, ?, b, c, ?, d]  // Expanded
        """
        if len(args) != 2:
            raise ExprGenError("vec_expand() expects (vector, mask)")
        vec = self.generate_expr(args[ARG_FIRST])
        mask = self.generate_expr(args[ARG_SECOND])
        count = vec.type.count
        result = ir.Constant(vec.type, [0] * count)
        # Track input position
        in_idx = self.builder.alloca(ir.IntType(32), name="expand_idx")
        self.builder.store(ir.Constant(ir.IntType(32), 0), in_idx)
        for i in range(count):
            # Check if bit i is set in mask
            bit = self.builder.and_(mask, ir.Constant(mask.type, 1 << i))
            is_set = self.builder.icmp_unsigned("!=", bit, ir.Constant(mask.type, 0))
            with self.builder.if_then(is_set):
                cur_in = self.builder.load(in_idx)
                elem = self.builder.extract_element(vec, cur_in)
                result = self.builder.insert_element(
                    result, elem, ir.Constant(ir.IntType(32), i)
                )
                new_in = self.builder.add(cur_in, ir.Constant(ir.IntType(32), 1))
                self.builder.store(new_in, in_idx)
        return result

    def _builtin_vec_fma(self, args):
        """Fused Multiply-Add: vec_fma(a, b, c) -> a*b + c
        FMA3: vfmadd - computes a*b+c in one operation with single rounding.
        More accurate and faster than separate mul+add.
        Example:
            a = [1.0, 2.0, 3.0, 4.0]
            b = [2.0, 2.0, 2.0, 2.0]
            c = [0.5, 0.5, 0.5, 0.5]
            result = vec_fma(a, b, c)  // [2.5, 4.5, 6.5, 8.5]
        """
        if len(args) != 3:
            raise ExprGenError("vec_fma() expects (a, b, c)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        c = self.generate_expr(args[ARG_THIRD])
        # Use LLVM's fmuladd intrinsic if float, else emulate
        if isinstance(a.type.element, (ir.FloatType, ir.DoubleType)):
            # LLVM fmuladd
            elem_bits = "32" if isinstance(a.type.element, ir.FloatType) else "64"
            intrinsic_name = f"llvm.fmuladd.v{a.type.count}f{elem_bits}"
            if intrinsic_name not in self.codegen.functions:
                fma_ty = ir.FunctionType(a.type, [a.type, a.type, a.type])
                fma_func = ir.Function(self.codegen.module, fma_ty, intrinsic_name)
                self.codegen.functions[intrinsic_name] = fma_func
            else:
                fma_func = self.codegen.functions[intrinsic_name]
            return self.builder.call(fma_func, [a, b, c], name="vec_fma")
        # Integer: a*b + c
        prod = self.builder.mul(a, b, name="fma_mul")
        return self.builder.add(prod, c, name="vec_fma")

    def _builtin_vec_fms(self, args):
        """Fused Multiply-Subtract: vec_fms(a, b, c) -> a*b - c
        FMA3: vfmsub - computes a*b-c in one operation.
        Example:
            a = [4.0, 6.0, 8.0, 10.0]
            b = [2.0, 2.0, 2.0, 2.0]
            c = [0.5, 0.5, 0.5, 0.5]
            result = vec_fms(a, b, c)  // [7.5, 11.5, 15.5, 19.5]
        """
        if len(args) != 3:
            raise ExprGenError("vec_fms() expects (a, b, c)")
        a = self.generate_expr(args[ARG_FIRST])
        b = self.generate_expr(args[ARG_SECOND])
        c = self.generate_expr(args[ARG_THIRD])
        if isinstance(a.type.element, (ir.FloatType, ir.DoubleType)):
            # Negate c and use fma: a*b + (-c) = a*b - c
            neg_c = self.builder.fsub(
                ir.Constant(a.type, [0.0] * a.type.count), c, name="fms_neg"
            )
            elem_bits = "32" if isinstance(a.type.element, ir.FloatType) else "64"
            intrinsic_name = f"llvm.fmuladd.v{a.type.count}f{elem_bits}"
            if intrinsic_name not in self.codegen.functions:
                fma_ty = ir.FunctionType(a.type, [a.type, a.type, a.type])
                fma_func = ir.Function(self.codegen.module, fma_ty, intrinsic_name)
                self.codegen.functions[intrinsic_name] = fma_func
            else:
                fma_func = self.codegen.functions[intrinsic_name]
            return self.builder.call(fma_func, [a, b, neg_c], name="vec_fms")
        # Integer: a*b - c
        prod = self.builder.mul(a, b, name="fms_mul")
        return self.builder.sub(prod, c, name="vec_fms")

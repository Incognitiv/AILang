"""Low-level and math/system utility builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

import sys
from parser import ast as A

from ast_access import arg_at
from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ExprGenError

_ENOSYS = -38


def _as_i64(self, value: ir.Value, name: str) -> ir.Value:
    """Normalize integer-like values to AILang's i64 syscall ABI."""
    i64 = ir.IntType(64)
    if isinstance(value.type, ir.IntType):
        if value.type.width == 64:
            return value
        if value.type.width < 64:
            if value.type.width == 1:
                return self.builder.zext(value, i64, name=name)
            return self.builder.sext(value, i64, name=name)
        return self.builder.trunc(value, i64, name=name)
    if isinstance(value.type, ir.PointerType):
        return self.builder.ptrtoint(value, i64, name=name)
    raise ExprGenError(f"syscall argument must be integer/pointer, got {value.type}")


def _builtin_poke(self, args):
    """Write a byte to a memory address: poke(address, value)

    Used for memory-mapped I/O like VGA text buffer at 0xB8000.
    Example: poke(0xB8000, 72)  // Write 'H' to VGA
    """
    if len(args) != 2:
        raise ExprGenError("poke() expects address and value")

    addr = self.generate_expr(args[ARG_FIRST])
    value = self.generate_expr(args[ARG_SECOND])

    # Convert address to pointer
    ptr_type = ir.IntType(8).as_pointer()
    ptr = self.builder.inttoptr(addr, ptr_type, name="poke_ptr")

    # Truncate value to 8 bits if needed
    if isinstance(value.type, ir.IntType) and value.type.width > 8:
        value = self.builder.trunc(value, ir.IntType(8), name="poke_val")

    # Store with volatile (ensures memory-mapped I/O isn't optimized away)
    store = self.builder.store(value, ptr)
    store.set_metadata("volatile", self.codegen.module.add_metadata([]))

    return ir.Constant(ir.IntType(64), 0)


def _builtin_peek(self, args):
    """Read a byte from a memory address: peek(address) -> int

    Used for memory-mapped I/O.
    Example: val = peek(0xB8000)  // Read from VGA buffer
    """
    if len(args) != 1:
        raise ExprGenError("peek() expects address")

    addr = self.generate_expr(args[ARG_FIRST])

    # Convert address to pointer
    ptr_type = ir.IntType(8).as_pointer()
    ptr = self.builder.inttoptr(addr, ptr_type, name="peek_ptr")

    # Load with volatile
    val = self.builder.load(ptr, name="peek_val")
    val.set_metadata("volatile", self.codegen.module.add_metadata([]))

    # Zero-extend to i64
    return self.builder.zext(val, ir.IntType(64), name="peek_result")


def _builtin_outb(self, args):
    """Write a byte to an I/O port (x86): outb(port, value)

    Used for hardware I/O like serial ports.
    Example: outb(0x3F8, 65)  // Write 'A' to COM1

    Note: Generates inline assembly for x86.
    """
    if len(args) != 2:
        raise ExprGenError("outb() expects port and value")

    port = self.generate_expr(args[ARG_FIRST])
    value = self.generate_expr(args[ARG_SECOND])

    # Truncate to appropriate sizes
    if isinstance(port.type, ir.IntType) and port.type.width > 16:
        port = self.builder.trunc(port, ir.IntType(16), name="outb_port")
    if isinstance(value.type, ir.IntType) and value.type.width > 8:
        value = self.builder.trunc(value, ir.IntType(8), name="outb_val")

    # Inline assembly: outb %al, %dx
    # Use {al} and {dx} for explicit register allocation
    asm_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8), ir.IntType(16)])
    asm = ir.InlineAsm(asm_ty, "outb %al, %dx", "{al},{dx}", side_effect=True)
    self.builder.call(asm, [value, port])

    return ir.Constant(ir.IntType(64), 0)


def _builtin_inb(self, args):
    """Read a byte from an I/O port (x86): inb(port) -> int

    Used for hardware I/O.
    Example: status = inb(0x3FD)  // Read COM1 status

    Note: Generates inline assembly for x86.
    """
    if len(args) != 1:
        raise ExprGenError("inb() expects port")

    port = self.generate_expr(args[ARG_FIRST])

    # Truncate to 16 bits
    if isinstance(port.type, ir.IntType) and port.type.width > 16:
        port = self.builder.trunc(port, ir.IntType(16), name="inb_port")

    # Inline assembly: inb %dx, %al
    # Use {dx} for input and ={al} for output
    asm_ty = ir.FunctionType(ir.IntType(8), [ir.IntType(16)])
    asm = ir.InlineAsm(asm_ty, "inb %dx, %al", "={al},{dx}", side_effect=True)
    val = self.builder.call(asm, [port], name="inb_val")

    # Zero-extend to i64
    return self.builder.zext(val, ir.IntType(64), name="inb_result")


def _builtin_syscall(self, args) -> ir.Value:
    """Native syscall trap: syscall(number, arg0, ... arg5) -> int.

    This is intentionally a unified AILang surface. The implementation is
    target-adapted internally because syscall numbers and ABIs are OS-specific.
    Unsupported targets compile successfully and return -ENOSYS.
    """
    if not 1 <= len(args) <= 7:
        raise ExprGenError("syscall() expects a syscall number and up to 6 arguments")

    i64 = ir.IntType(64)
    triple = str(getattr(self.codegen.module, "triple", "")).lower()
    is_linux = "linux" in triple or sys.platform.startswith("linux")
    if not is_linux:
        return ir.Constant(i64, _ENOSYS)

    values = [
        _as_i64(self, self.generate_expr(arg), f"syscall_arg{idx}")
        for idx, arg in enumerate(args)
    ]

    syscall_fn = self.codegen.module.globals.get("syscall")
    if syscall_fn is None:
        syscall_ty = ir.FunctionType(i64, [i64], var_arg=True)
        syscall_fn = ir.Function(self.codegen.module, syscall_ty, "syscall")

    return self.builder.call(syscall_fn, values, name="syscall_result")


def _builtin_getpid(self, args) -> ir.Value:
    """Portable process id helper: getpid() -> int."""
    if args:
        raise ExprGenError("getpid() takes no arguments")

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    triple = str(getattr(self.codegen.module, "triple", "")).lower()
    is_windows = "windows" in triple or sys.platform == "win32"
    if is_windows:
        func = self.codegen.module.globals.get("GetCurrentProcessId")
        if func is None:
            func_ty = ir.FunctionType(i32, [])
            func = ir.Function(self.codegen.module, func_ty, "GetCurrentProcessId")
        value = self.builder.call(func, [], name="getpid_value")
        return self.builder.zext(value, i64, name="getpid_i64")

    func = self.codegen.module.globals.get("getpid")
    if func is None:
        func_ty = ir.FunctionType(i32, [])
        func = ir.Function(self.codegen.module, func_ty, "getpid")
    value = self.builder.call(func, [], name="getpid_value")
    return self.builder.sext(value, i64, name="getpid_i64")


# ------------------------------------------------------------------
# Pointer Arithmetic
# ------------------------------------------------------------------
def _builtin_ptr_add(self, args):
    """Add offset to pointer: ptr_add(ptr, offset) -> ptr

    Example: new_ptr = ptr_add(data, 32)  // Advance 32 bytes
    """
    if len(args) != 2:
        raise ExprGenError("ptr_add() expects (ptr, offset)")

    ptr = self.generate_expr(args[ARG_FIRST])
    offset = self.generate_expr(args[ARG_SECOND])

    # Ensure ptr is a pointer type
    if not isinstance(ptr.type, ir.PointerType):
        # Try to treat as i8* if it's an int (address)
        if isinstance(ptr.type, ir.IntType):
            ptr = self.builder.inttoptr(
                ptr, ir.IntType(8).as_pointer(), name="ptr_from_int"
            )
        else:
            raise ExprGenError(f"ptr_add expects pointer, got {ptr.type}")

    # Use GEP for pointer arithmetic
    return self.builder.gep(ptr, [offset], name="ptr_add")


def _builtin_ptr_sub(self, args):
    """Subtract offset from pointer: ptr_sub(ptr, offset) -> ptr

    Example: new_ptr = ptr_sub(data, 32)  // Go back 32 bytes
    """
    if len(args) != 2:
        raise ExprGenError("ptr_sub() expects (ptr, offset)")

    ptr = self.generate_expr(args[ARG_FIRST])
    offset = self.generate_expr(args[ARG_SECOND])

    # Negate offset
    neg_offset = self.builder.neg(offset, name="neg_offset")

    # Ensure ptr is a pointer type
    if not isinstance(ptr.type, ir.PointerType):
        if isinstance(ptr.type, ir.IntType):
            ptr = self.builder.inttoptr(
                ptr, ir.IntType(8).as_pointer(), name="ptr_from_int"
            )
        else:
            raise ExprGenError(f"ptr_sub expects pointer, got {ptr.type}")

    # Use GEP for pointer arithmetic
    return self.builder.gep(ptr, [neg_offset], name="ptr_sub")


# ------------------------------------------------------------------
# Timing Functions
# ------------------------------------------------------------------
def _builtin_math_unary(self, args, func_name: str):
    """Handle unary math functions: exp, log, sqrt, sin, cos, tan, tanh, floor, ceil, fabs"""
    if len(args) != 1:
        raise ExprGenError(f"{func_name}() expects exactly 1 argument")

    arg_val = self.generate_expr(args[ARG_FIRST])

    # Convert to double if needed
    double_ty = ir.DoubleType()
    if isinstance(arg_val.type, ir.IntType):
        arg_val = self.builder.sitofp(arg_val, double_ty, name="to_double")
    elif isinstance(arg_val.type, ir.FloatType):
        arg_val = self.builder.fpext(arg_val, double_ty, name="to_double")
    elif not isinstance(arg_val.type, ir.DoubleType):
        raise ExprGenError(f"{func_name}() requires numeric argument")

    # Get the appropriate libm function
    func_map = {
        "exp": self.codegen.get_exp,
        "log": self.codegen.get_log,
        "sqrt": self.codegen.get_sqrt,
        "sin": self.codegen.get_sin,
        "cos": self.codegen.get_cos,
        "tan": self.codegen.get_tan,
        "tanh": self.codegen.get_tanh,
        "floor": self.codegen.get_floor,
        "ceil": self.codegen.get_ceil,
        "fabs": self.codegen.get_fabs,
    }

    libm_func = func_map[func_name]()
    result = self.builder.call(libm_func, [arg_val], name=f"{func_name}_result")
    return result


def _builtin_pow(self, args):
    """Handle pow(base, exponent) - power function"""
    if len(args) != 2:
        raise ExprGenError("pow() expects exactly 2 arguments")

    base_val = self.generate_expr(args[ARG_FIRST])
    exp_val = self.generate_expr(args[ARG_SECOND])

    double_ty = ir.DoubleType()

    # Convert base to double if needed
    if isinstance(base_val.type, ir.IntType):
        base_val = self.builder.sitofp(base_val, double_ty, name="base_double")
    elif isinstance(base_val.type, ir.FloatType):
        base_val = self.builder.fpext(base_val, double_ty, name="base_double")

    # Convert exponent to double if needed
    if isinstance(exp_val.type, ir.IntType):
        exp_val = self.builder.sitofp(exp_val, double_ty, name="exp_double")
    elif isinstance(exp_val.type, ir.FloatType):
        exp_val = self.builder.fpext(exp_val, double_ty, name="exp_double")

    pow_func = self.codegen.get_pow()
    result = self.builder.call(pow_func, [base_val, exp_val], name="pow_result")
    return result


def _literal_bytes(value: str) -> bytes | None:
    """Return UTF-8 bytes when strcmp literal semantics are exact."""
    if "\0" in value:
        return None
    return value.encode("utf-8")


def _emit_literal_ptr_compare(
    self,
    ptr: ir.Value,
    literal_raw: bytes,
    *,
    require_nul: bool,
    name_prefix: str,
) -> ir.Value:
    """Compare a string pointer against fixed bytes without libc strcmp."""
    int64 = ir.IntType(64)
    i8 = ir.IntType(8)
    sequence = list(literal_raw)
    if require_nul:
        sequence.append(0)
    if not sequence:
        return ir.Constant(int64, 1)

    func = self.function
    fail_block = func.append_basic_block(f"{name_prefix}_fail")
    success_block = func.append_basic_block(f"{name_prefix}_ok")
    merge_block = func.append_basic_block(f"{name_prefix}_merge")

    last_index = len(sequence) - 1
    for offset, expected in enumerate(sequence):
        byte_ptr = self.builder.gep(
            ptr, [ir.Constant(int64, offset)], name=f"{name_prefix}_ptr"
        )
        actual = self.builder.load(byte_ptr, name=f"{name_prefix}_ch")
        matches = self.builder.icmp_unsigned(
            "==", actual, ir.Constant(i8, expected), name=f"{name_prefix}_eq"
        )
        if offset == last_index:
            next_block = success_block
        else:
            next_block = func.append_basic_block(f"{name_prefix}_next")
        self.builder.cbranch(matches, next_block, fail_block)
        if offset != last_index:
            self.builder.position_at_end(next_block)

    self.builder.position_at_end(fail_block)
    self.builder.branch(merge_block)
    fail_end = self.builder.block

    self.builder.position_at_end(success_block)
    self.builder.branch(merge_block)
    success_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(int64, name=f"{name_prefix}_result")
    phi.add_incoming(ir.Constant(int64, 0), fail_end)
    phi.add_incoming(ir.Constant(int64, 1), success_end)
    return phi


def _emit_substr_literal_compare(self, node: A.Call, literal_raw: bytes) -> ir.Value:
    """Compare substr(s, start, len) to a literal without allocating substr."""
    int64 = ir.IntType(64)
    zero = ir.Constant(int64, 0)
    lit_len = ir.Constant(int64, len(literal_raw))

    source = self.generate_expr(arg_at(node, 0))
    start = self.ensure_int64(self.generate_expr(arg_at(node, 1)))
    length = self.ensure_int64(self.generate_expr(arg_at(node, 2)))
    slen = self.builder.call(self.codegen.get_strlen(), [source], name="streq_slen")

    func = self.function
    start_ok_block = func.append_basic_block("streq_slice_start_ok")
    start_bad_block = func.append_basic_block("streq_slice_start_bad")
    merge_block = func.append_basic_block("streq_slice_done")

    start_ge0 = self.builder.icmp_signed(">=", start, zero, name="streq_start_ge0")
    self.builder.cbranch(start_ge0, start_ok_block, start_bad_block)

    self.builder.position_at_end(start_bad_block)
    bad_value = ir.Constant(int64, 1 if len(literal_raw) == 0 else 0)
    self.builder.branch(merge_block)
    start_bad_end = self.builder.block

    self.builder.position_at_end(start_ok_block)
    start_in_range = self.builder.icmp_signed("<", start, slen, name="streq_start_lt")
    substr_start = self.builder.select(
        start_in_range, start, slen, name="streq_substr_start"
    )
    max_len = self.builder.sub(slen, substr_start, name="streq_substr_max")
    len_nonneg = self.builder.select(
        self.builder.icmp_signed(">=", length, zero, name="streq_len_ge0"),
        length,
        zero,
        name="streq_len_nonneg",
    )
    use_len = self.builder.select(
        self.builder.icmp_signed("<=", len_nonneg, max_len, name="streq_len_fits"),
        len_nonneg,
        max_len,
        name="streq_use_len",
    )
    len_matches = self.builder.icmp_signed("==", use_len, lit_len, name="streq_len_eq")
    compare_block = func.append_basic_block("streq_slice_compare")
    len_fail_block = func.append_basic_block("streq_slice_len_fail")
    self.builder.cbranch(len_matches, compare_block, len_fail_block)

    self.builder.position_at_end(len_fail_block)
    self.builder.branch(merge_block)
    len_fail_end = self.builder.block

    self.builder.position_at_end(compare_block)
    compare_ptr = self.builder.gep(source, [substr_start], name="streq_slice_ptr")
    compare_value = _emit_literal_ptr_compare(
        self,
        compare_ptr,
        literal_raw,
        require_nul=False,
        name_prefix="streq_slice_lit",
    )
    self.builder.branch(merge_block)
    compare_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(int64, name="streq_slice_result")
    phi.add_incoming(bad_value, start_bad_end)
    phi.add_incoming(ir.Constant(int64, 0), len_fail_end)
    phi.add_incoming(compare_value, compare_end)
    return phi


def _emit_streq_literal_fastpath(
    self, value_node: A.ASTNode, literal: str
) -> ir.Value | None:
    literal_raw = _literal_bytes(literal)
    if literal_raw is None:
        return None
    if (
        isinstance(value_node, A.Call)
        and value_node.name == "substr"
        and len(value_node.args) == 3
    ):
        return _emit_substr_literal_compare(self, value_node, literal_raw)
    value = self.generate_expr(value_node)
    return _emit_literal_ptr_compare(
        self,
        value,
        literal_raw,
        require_nul=True,
        name_prefix="streq_lit",
    )


def _builtin_streq(self, args):
    """String equality: streq(a, b) -> bool (1 if equal, 0 if not)."""
    if len(args) != 2:
        raise ExprGenError("streq() expects 2 arguments (str1, str2)")
    left_node = args[ARG_FIRST]
    right_node = args[ARG_SECOND]
    if isinstance(right_node, A.StringLit):
        fast = _emit_streq_literal_fastpath(self, left_node, right_node.value)
        if fast is not None:
            return fast
    if isinstance(left_node, A.StringLit):
        fast = _emit_streq_literal_fastpath(self, right_node, left_node.value)
        if fast is not None:
            return fast
    left = self.generate_expr(left_node)
    right = self.generate_expr(right_node)
    strcmp_fn = self.codegen.get_strcmp()
    cmp_result = self.builder.call(strcmp_fn, [left, right], name="streq_cmp")
    zero = ir.Constant(cmp_result.type, 0)
    is_eq = self.builder.icmp_signed("==", cmp_result, zero, name="streq_eq")
    return self.builder.zext(is_eq, ir.IntType(64), name="streq_result")


def _builtin_parse_int(self, args):
    """Parse integer from string: parse_int(str) -> int

    Uses strtoll from libc for parsing.
    """
    if len(args) != 1:
        raise ExprGenError("parse_int() expects exactly 1 argument")

    # Declare strtoll if not already done
    strtoll_name = "strtoll"
    if strtoll_name not in self.codegen.functions:
        char_ptr = ir.IntType(8).as_pointer()
        char_ptr_ptr = char_ptr.as_pointer()
        strtoll_ty = ir.FunctionType(
            ir.IntType(64), [char_ptr, char_ptr_ptr, ir.IntType(32)]
        )
        strtoll_func = ir.Function(self.codegen.module, strtoll_ty, strtoll_name)
        self.codegen.functions[strtoll_name] = strtoll_func
    strtoll_func = self.codegen.functions[strtoll_name]

    # Get the string argument
    str_ptr = self.generate_expr(args[ARG_FIRST])

    # Call strtoll(str, NULL, 10) - base 10
    char_ptr = ir.IntType(8).as_pointer()
    null_ptr = ir.Constant(char_ptr.as_pointer(), None)
    base_10 = ir.Constant(ir.IntType(32), 10)

    result = self.builder.call(
        strtoll_func, [str_ptr, null_ptr, base_10], name="parsed_int"
    )

    return result


def _builtin_popcount(self, args):
    """Count number of set bits: popcount(x) -> int

    Uses LLVM's ctpop intrinsic which compiles to native POPCNT instruction
    on modern CPUs (SSE4.2+, Nehalem 2008+).

    Example:
        bits = 0xFF00FF00  // 16 bits set
        count = popcount(bits)  // Returns 16
    """
    if len(args) != 1:
        raise ExprGenError("popcount() expects 1 argument")

    val = self.generate_expr(args[ARG_FIRST])

    # Get or create the ctpop intrinsic for this type
    if isinstance(val.type, ir.IntType):
        width = val.type.width
        intrinsic_name = f"llvm.ctpop.i{width}"

        if intrinsic_name not in self.codegen.functions:
            ctpop_ty = ir.FunctionType(val.type, [val.type])
            ctpop_func = ir.Function(self.codegen.module, ctpop_ty, intrinsic_name)
            self.codegen.functions[intrinsic_name] = ctpop_func
        else:
            ctpop_func = self.codegen.functions[intrinsic_name]

        result = self.builder.call(ctpop_func, [val], name="popcount")

        # Extend to i64 for consistency
        if width < 64:
            return self.builder.zext(result, ir.IntType(64), name="popcount_ext")
        return result

    raise ExprGenError(f"popcount() requires integer type, got {val.type}")


def _builtin_clz(self, args):
    """Count leading zeros: clz(x) -> int

    Uses LLVM's ctlz intrinsic which compiles to native LZCNT/BSR instruction.

    Example:
        x = 0x00FF0000
        leading = clz(x)  // Returns 8 (8 leading zero bits in 32-bit)
    """
    if len(args) != 1:
        raise ExprGenError("clz() expects 1 argument")

    val = self.generate_expr(args[ARG_FIRST])

    if isinstance(val.type, ir.IntType):
        width = val.type.width
        intrinsic_name = f"llvm.ctlz.i{width}"

        if intrinsic_name not in self.codegen.functions:
            # ctlz takes (val, is_zero_poison) - we set is_zero_poison=false
            ctlz_ty = ir.FunctionType(val.type, [val.type, ir.IntType(1)])
            ctlz_func = ir.Function(self.codegen.module, ctlz_ty, intrinsic_name)
            self.codegen.functions[intrinsic_name] = ctlz_func
        else:
            ctlz_func = self.codegen.functions[intrinsic_name]

        # is_zero_poison = false (return width if input is 0)
        result = self.builder.call(
            ctlz_func, [val, ir.Constant(ir.IntType(1), 0)], name="clz"
        )

        if width < 64:
            return self.builder.zext(result, ir.IntType(64), name="clz_ext")
        return result

    raise ExprGenError(f"clz() requires integer type, got {val.type}")


def _builtin_ctz(self, args):
    """Count trailing zeros: ctz(x) -> int

    Uses LLVM's cttz intrinsic which compiles to native TZCNT/BSF instruction.
    Useful for finding the position of the first set bit (first match in movemask).

    Example:
        mask = 0b00101000  // Bits 3 and 5 are set
        first = ctz(mask)  // Returns 3 (position of first set bit)
    """
    if len(args) != 1:
        raise ExprGenError("ctz() expects 1 argument")

    val = self.generate_expr(args[ARG_FIRST])

    if isinstance(val.type, ir.IntType):
        width = val.type.width
        intrinsic_name = f"llvm.cttz.i{width}"

        if intrinsic_name not in self.codegen.functions:
            # cttz takes (val, is_zero_poison) - we set is_zero_poison=false
            cttz_ty = ir.FunctionType(val.type, [val.type, ir.IntType(1)])
            cttz_func = ir.Function(self.codegen.module, cttz_ty, intrinsic_name)
            self.codegen.functions[intrinsic_name] = cttz_func
        else:
            cttz_func = self.codegen.functions[intrinsic_name]

        # is_zero_poison = false (return width if input is 0)
        result = self.builder.call(
            cttz_func, [val, ir.Constant(ir.IntType(1), 0)], name="ctz"
        )

        if width < 64:
            return self.builder.zext(result, ir.IntType(64), name="ctz_ext")
        return result

    raise ExprGenError(f"ctz() requires integer type, got {val.type}")


# ------------------------------------------------------------------
# Timing Intrinsics
# ------------------------------------------------------------------
def _builtin_rdtsc(self, args):
    """Read CPU timestamp counter: rdtsc() -> int

    Returns the number of CPU cycles since reset.
    Very fast (~20 cycles), good for micro-benchmarks.

    Example:
        start = rdtsc()
        // ... code to benchmark ...
        end = rdtsc()
        cycles = end - start
    """
    if len(args) != 0:
        raise ExprGenError("rdtsc() takes no arguments")

    # Use inline assembly for rdtsc
    # Returns 64-bit value: (EDX << 32) | EAX
    asm_ty = ir.FunctionType(ir.IntType(64), [])
    asm = ir.InlineAsm(
        asm_ty,
        "rdtsc\n\tshl $$32, %rdx\n\tor %rdx, %rax",
        "={ax},~{dx}",
        side_effect=True,
    )
    return self.builder.call(asm, [], name="rdtsc")

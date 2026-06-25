"""String split helper builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

from parser.ast import Variable
from typing import Any

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ARG_THIRD, ExprGenError


def _decl_type_for_arg(self, arg) -> str:
    if isinstance(arg, Variable):
        declared = getattr(self.codegen, "local_decl_types", {}).get(arg.name)
        if isinstance(declared, str):
            return declared.strip().lower()
    return ""


def _builtin_split(self, args):
    """Split string by delimiter: split(str, delim) -> array of strings

    Returns an array where each element is a pointer to a string.
    Uses strtok from libc for tokenization.
    """
    if len(args) < 1 or len(args) > 2:
        raise ExprGenError("split() expects 1 or 2 arguments: split(str[, delim])")

    # Declare C library functions
    self._ensure_libc_functions()

    # Get arguments
    str_ptr = self.generate_expr(args[ARG_FIRST])
    if len(args) > 1:
        delim_ptr = self.generate_expr(args[ARG_SECOND])
    else:
        # Default delimiter is space
        delim_ptr = self.codegen.create_string_constant(" ")

    # Create the split helper function if not exists
    split_helper_name = "ailang_split_helper"
    if split_helper_name not in self.codegen.functions:
        self._create_split_helper()
    split_func = self.codegen.functions[split_helper_name]

    # Call the helper - returns pointer to array of string pointers
    result = self.builder.call(split_func, [str_ptr, delim_ptr], name="split_arr")

    return result


def _builtin_split_ints(self, args):
    """Split string and parse as integers: split_ints(str, delim) -> array of int

    Returns an array of integers parsed from the split string.
    Uses strtok and strtoll from libc.
    """
    if len(args) < 1 or len(args) > 2:
        raise ExprGenError(
            "split_ints() expects 1 or 2 arguments: split_ints(str[, delim])"
        )

    # Declare C library functions
    self._ensure_libc_functions()

    # Get arguments
    str_ptr = self.generate_expr(args[ARG_FIRST])
    if len(args) > 1:
        delim_ptr = self.generate_expr(args[ARG_SECOND])
    else:
        # Default delimiter is space
        delim_ptr = self.codegen.create_string_constant(" ")

    # Create the split_ints helper function if not exists
    split_ints_helper_name = "ailang_split_ints_helper"
    if split_ints_helper_name not in self.codegen.functions:
        self._create_split_ints_helper()
    split_ints_func = self.codegen.functions[split_ints_helper_name]

    # Call the helper - returns pointer to array of i64
    result = self.builder.call(
        split_ints_func, [str_ptr, delim_ptr], name="split_ints_arr"
    )

    return result


def _builtin_split_len(self, args):
    """Get length of split result: split_len(arr) -> int

    Returns the length from the split array struct.
    """
    if len(args) != 1:
        raise ExprGenError("split_len() expects exactly 1 argument")
    if _decl_type_for_arg(self, args[ARG_FIRST]) == "str_array":
        return self.codegen.builtin_str_array_len(args)

    i64 = ir.IntType(64)
    i64_ptr = i64.as_pointer()

    arr_ptr = self.generate_expr(args[ARG_FIRST])
    arr_i64_ptr = self.builder.bitcast(arr_ptr, i64_ptr, name="arr_i64")
    length = self.builder.load(arr_i64_ptr, name="split_len")

    return length


def _builtin_split_get(self, args):
    """Get element from split_ints result: split_get(arr, idx) -> int

    Returns the integer at the given index from a split_ints result.
    """
    if len(args) != 2:
        raise ExprGenError("split_get() expects 2 arguments: split_get(arr, idx)")

    i64 = ir.IntType(64)
    i64_ptr = i64.as_pointer()

    arr_ptr = self.generate_expr(args[ARG_FIRST])
    idx = self.generate_expr(args[ARG_SECOND])

    # Array struct: [length, capacity, data_ptr]
    arr_i64_ptr = self.builder.bitcast(arr_ptr, i64_ptr, name="arr_i64")

    # Get data pointer (at offset 2)
    data_slot = self.builder.gep(arr_i64_ptr, [ir.Constant(i64, 2)], name="data_slot")
    data_as_i64 = self.builder.load(data_slot, name="data_as_i64")
    data_ptr = self.builder.inttoptr(data_as_i64, i64_ptr, name="data_ptr")

    # Get element at index
    elem_ptr = self.builder.gep(data_ptr, [idx], name="elem_ptr")
    elem = self.builder.load(elem_ptr, name="elem")

    return elem


def _builtin_split_str_get(self, args):
    """Get string element from split() result: split_str_get(arr, idx) -> string

    Returns the string at the given index from a split() result.
    """
    if len(args) != 2:
        raise ExprGenError(
            "split_str_get() expects 2 arguments: split_str_get(arr, idx)"
        )
    if _decl_type_for_arg(self, args[ARG_FIRST]) == "str_array":
        return self.codegen.builtin_str_array_get(args)

    i64 = ir.IntType(64)
    i64_ptr = i64.as_pointer()
    char_ptr = ir.IntType(8).as_pointer()
    char_ptr_ptr = char_ptr.as_pointer()

    arr_ptr = self.generate_expr(args[ARG_FIRST])
    idx = self.generate_expr(args[ARG_SECOND])
    idx64 = self.codegen.ensure_int64(idx)

    # Array struct: [length, capacity, data_ptr]
    arr_i64_ptr = self.builder.bitcast(arr_ptr, i64_ptr, name="sarr_i64")

    # Get data pointer (at offset 2)
    data_slot = self.builder.gep(arr_i64_ptr, [ir.Constant(i64, 2)], name="sdata_slot")
    data_as_i64 = self.builder.load(data_slot, name="sdata_as_i64")
    data_ptr = self.builder.inttoptr(data_as_i64, char_ptr_ptr, name="sdata_ptr")

    # Get string pointer at index
    elem_ptr = self.builder.gep(data_ptr, [idx64], name="selem_ptr")
    elem = self.builder.load(elem_ptr, name="selem")

    return elem


def _builtin_split_set(self, args):
    """Set element in split_ints result: split_set(arr, idx, val)

    Writes an integer at the given index in a split_ints result array.
    """
    if len(args) != 3:
        raise ExprGenError("split_set() expects 3 arguments: split_set(arr, idx, val)")

    i64 = ir.IntType(64)
    i64_ptr = i64.as_pointer()

    arr_ptr = self.generate_expr(args[ARG_FIRST])
    idx = self.codegen.ensure_int64(self.generate_expr(args[ARG_SECOND]))
    val = self.codegen.ensure_int64(self.generate_expr(args[ARG_THIRD]))

    # Array struct: [length, capacity, data_ptr]
    arr_i64_ptr = self.builder.bitcast(arr_ptr, i64_ptr, name="set_i64")

    # Get data pointer (at offset 2)
    data_slot = self.builder.gep(
        arr_i64_ptr, [ir.Constant(i64, 2)], name="set_data_slot"
    )
    data_as_i64 = self.builder.load(data_slot, name="set_data_i64")
    data_ptr = self.builder.inttoptr(data_as_i64, i64_ptr, name="set_data_ptr")

    # Set element at index
    elem_ptr = self.builder.gep(data_ptr, [idx], name="set_elem_ptr")
    self.builder.store(val, elem_ptr)

    return ir.Constant(i64, 0)


def _ensure_libc_functions(self):
    """Ensure common libc functions are declared.

    Idempotent across declaration paths: some of these names (strlen,
    malloc, etc.) are also declared lazily by `codegen.get_*` accessors
    that cache on dedicated attributes rather than `self.functions`.
    Without the module-globals fallback below, importing two libraries
    that each trigger a different declaration path triggers llvmlite's
    DuplicatedNameError when the second path tries to register the same
    external function again.
    """
    char_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    def declare(cache_key: str, c_name: str, func_ty: ir.FunctionType):
        if cache_key in self.codegen.functions:
            return self.codegen.functions[cache_key]
        existing = self.codegen.module.globals.get(c_name)
        if isinstance(existing, ir.Function):
            self.codegen.functions[cache_key] = existing
            return existing
        new_func = ir.Function(self.codegen.module, func_ty, c_name)
        self.codegen.functions[cache_key] = new_func
        return new_func

    # strlen
    declare("strlen", "strlen", ir.FunctionType(i64, [char_ptr]))

    # strcpy
    declare("strcpy", "strcpy", ir.FunctionType(char_ptr, [char_ptr, char_ptr]))

    # Thread-safe tokenizer (fixes M11)
    # strtok_s on Windows/MSVC, strtok_r on POSIX
    import platform

    is_windows = platform.system() == "Windows"
    strtok_name = "strtok_s" if is_windows else "strtok_r"
    char_ptr_ptr = char_ptr.as_pointer()
    strtok_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr, char_ptr_ptr])
    strtok_func = declare("strtok_safe", strtok_name, strtok_ty)
    # Mirror under the platform-specific name too so callers that look it
    # up by either alias find the same function object.
    if strtok_name not in self.codegen.functions:
        self.codegen.functions[strtok_name] = strtok_func

    # strtoll
    declare(
        "strtoll",
        "strtoll",
        ir.FunctionType(i64, [char_ptr, char_ptr.as_pointer(), i32]),
    )

    # malloc - use codegen's centralized declaration
    if "malloc" not in self.codegen.functions:
        self.codegen.get_malloc()

    # free
    declare("free", "free", ir.FunctionType(ir.VoidType(), [char_ptr]))


def _create_split_common(self, helper_name: str) -> dict[str, Any]:
    char_ptr = ir.IntType(8).as_pointer()
    char_ptr_ptr = char_ptr.as_pointer()
    i64 = ir.IntType(64)
    i64_ptr = i64.as_pointer()

    func_ty = ir.FunctionType(i64_ptr, [char_ptr, char_ptr])
    func = ir.Function(self.codegen.module, func_ty, helper_name)
    self.codegen.functions[helper_name] = func

    str_param, delim_param = func.args
    str_param.name = "str"
    delim_param.name = "delim"

    entry = func.append_basic_block("entry")
    count_loop = func.append_basic_block("count_loop")
    count_body = func.append_basic_block("count_body")
    alloc_block = func.append_basic_block("alloc")
    parse_loop = func.append_basic_block("parse_loop")
    parse_body = func.append_basic_block("parse_body")
    exit_block = func.append_basic_block("exit")
    builder = ir.IRBuilder(entry)

    strlen_func = self.codegen.functions["strlen"]
    strcpy_func = self.codegen.functions["strcpy"]
    strtok_r_func = self.codegen.functions["strtok_safe"]
    malloc_func = self.codegen.functions["malloc"]
    free_func = self.codegen._get_free()

    str_len = builder.call(strlen_func, [str_param], name="str_len")
    copy_size = builder.add(str_len, ir.Constant(i64, 1), name="copy_size")
    str_copy = builder.call(malloc_func, [copy_size], name="str_copy")
    builder.call(strcpy_func, [str_copy, str_param])
    str_copy_storage = builder.alloca(char_ptr, name="str_copy_storage")
    builder.store(str_copy, str_copy_storage)

    str_copy2 = builder.call(malloc_func, [copy_size], name="str_copy2")
    builder.call(strcpy_func, [str_copy2, str_param])
    str_copy2_storage = builder.alloca(char_ptr, name="str_copy2_storage")
    builder.store(str_copy2, str_copy2_storage)

    saveptr1 = builder.alloca(char_ptr, name="saveptr1")
    saveptr2 = builder.alloca(char_ptr, name="saveptr2")
    count_ptr = builder.alloca(i64, name="count")
    builder.store(ir.Constant(i64, 0), count_ptr)

    first_token = builder.call(
        strtok_r_func, [str_copy, delim_param, saveptr1], name="first"
    )
    builder.branch(count_loop)

    builder.position_at_end(count_loop)
    token_phi = builder.phi(char_ptr, name="token")
    token_phi.add_incoming(first_token, entry)
    is_null = builder.icmp_unsigned(
        "==", token_phi, ir.Constant(char_ptr, None), name="is_null"
    )
    builder.cbranch(is_null, alloc_block, count_body)

    builder.position_at_end(count_body)
    count_val = builder.load(count_ptr, name="count_val")
    new_count = builder.add(count_val, ir.Constant(i64, 1), name="new_count")
    builder.store(new_count, count_ptr)
    null_ptr = ir.Constant(char_ptr, None)
    next_token = builder.call(
        strtok_r_func, [null_ptr, delim_param, saveptr1], name="next"
    )
    token_phi.add_incoming(next_token, count_body)
    builder.branch(count_loop)

    builder.position_at_end(alloc_block)
    final_count = builder.load(count_ptr, name="final_count")
    return {
        "alloc_block": alloc_block,
        "builder": builder,
        "char_ptr": char_ptr,
        "char_ptr_ptr": char_ptr_ptr,
        "delim_param": delim_param,
        "exit_block": exit_block,
        "final_count": final_count,
        "free_func": free_func,
        "i64": i64,
        "i64_ptr": i64_ptr,
        "malloc_func": malloc_func,
        "null_ptr": null_ptr,
        "parse_body": parse_body,
        "parse_loop": parse_loop,
        "saveptr2": saveptr2,
        "str_copy2": str_copy2,
        "str_copy2_storage": str_copy2_storage,
        "str_copy_storage": str_copy_storage,
        "strcpy_func": strcpy_func,
        "strlen_func": strlen_func,
        "strtok_r_func": strtok_r_func,
    }


def _begin_split_parse_loop(self, ctx: dict[str, Any]):
    builder = ctx["builder"]
    char_ptr = ctx["char_ptr"]
    delim_param = ctx["delim_param"]
    i64 = ctx["i64"]
    idx_ptr = builder.alloca(i64, name="idx")
    builder.store(ir.Constant(i64, 0), idx_ptr)
    first_token = builder.call(
        ctx["strtok_r_func"],
        [ctx["str_copy2"], delim_param, ctx["saveptr2"]],
        name="first2",
    )
    builder.branch(ctx["parse_loop"])
    builder.position_at_end(ctx["parse_loop"])
    token_phi = builder.phi(char_ptr, name="token2")
    token_phi.add_incoming(first_token, ctx["alloc_block"])
    is_null = builder.icmp_unsigned(
        "==", token_phi, ir.Constant(char_ptr, None), name="is_null2"
    )
    builder.cbranch(is_null, ctx["exit_block"], ctx["parse_body"])
    builder.position_at_end(ctx["parse_body"])
    return idx_ptr, token_phi, builder.load(idx_ptr, name="idx_val")


def _continue_split_parse_loop(
    self,
    ctx: dict[str, Any],
    idx_ptr: ir.Value,
    idx_val: ir.Value,
    token_phi: ir.Value,
) -> None:
    builder = ctx["builder"]
    i64 = ctx["i64"]
    new_idx = builder.add(idx_val, ir.Constant(i64, 1), name="new_idx")
    builder.store(new_idx, idx_ptr)
    next_token = builder.call(
        ctx["strtok_r_func"],
        [ctx["null_ptr"], ctx["delim_param"], ctx["saveptr2"]],
        name="next2",
    )
    token_phi.add_incoming(next_token, ctx["parse_body"])
    builder.branch(ctx["parse_loop"])


def _finish_split_helper(self, ctx: dict[str, Any], result_i64_ptr: ir.Value) -> None:
    builder = ctx["builder"]
    builder.position_at_end(ctx["exit_block"])
    str_copy_to_free = builder.load(ctx["str_copy_storage"], name="str_copy_to_free")
    builder.call(ctx["free_func"], [str_copy_to_free])
    str_copy2_to_free = builder.load(ctx["str_copy2_storage"], name="str_copy2_to_free")
    builder.call(ctx["free_func"], [str_copy2_to_free])
    builder.ret(result_i64_ptr)


def _create_split_helper(self):
    """Create the split() helper function in LLVM IR.

    This creates a function that splits a string by delimiter and returns
    an array structure with: [length, capacity, ptr to string array]
    """
    ctx = self._create_split_common("ailang_split_helper")
    builder = ctx["builder"]
    char_ptr_ptr = ctx["char_ptr_ptr"]
    final_count = ctx["final_count"]
    i64 = ctx["i64"]
    i64_ptr = ctx["i64_ptr"]
    malloc_func = ctx["malloc_func"]
    strcpy_func = ctx["strcpy_func"]
    strlen_func = ctx["strlen_func"]

    # Array struct: [length, capacity, data_ptr]
    struct_size = ir.Constant(i64, 24)  # 3 * 8 bytes
    result_ptr = builder.call(malloc_func, [struct_size], name="result")
    result_i64_ptr = builder.bitcast(result_ptr, i64_ptr, name="result_i64")

    # Store length and capacity
    builder.store(final_count, result_i64_ptr)
    cap_ptr = builder.gep(result_i64_ptr, [ir.Constant(i64, 1)], name="cap_ptr")
    builder.store(final_count, cap_ptr)

    # Allocate data array (array of string pointers)
    ptr_size = ir.Constant(i64, 8)
    data_size = builder.mul(final_count, ptr_size, name="data_size")
    data_ptr = builder.call(malloc_func, [data_size], name="data")
    data_ptr_ptr = builder.bitcast(data_ptr, char_ptr_ptr, name="data_ptr_ptr")

    # Store data pointer
    data_slot = builder.gep(result_i64_ptr, [ir.Constant(i64, 2)], name="data_slot")
    data_ptr_as_i64 = builder.ptrtoint(data_ptr, i64, name="data_as_i64")
    builder.store(data_ptr_as_i64, data_slot)

    idx_ptr, token_phi2, idx_val = self._begin_split_parse_loop(ctx)
    token_len = builder.call(strlen_func, [token_phi2], name="token_len")
    token_size = builder.add(token_len, ir.Constant(i64, 1), name="token_size")
    token_copy = builder.call(malloc_func, [token_size], name="token_copy")
    builder.call(strcpy_func, [token_copy, token_phi2])
    slot_ptr = builder.gep(data_ptr_ptr, [idx_val], name="slot_ptr")
    builder.store(token_copy, slot_ptr)
    self._continue_split_parse_loop(ctx, idx_ptr, idx_val, token_phi2)
    self._finish_split_helper(ctx, result_i64_ptr)


def _create_split_ints_helper(self):
    """Create the split_ints() helper function in LLVM IR.

    Returns array of integers parsed from split string.
    """
    ctx = self._create_split_common("ailang_split_ints_helper")
    builder = ctx["builder"]
    char_ptr_ptr = ctx["char_ptr_ptr"]
    final_count = ctx["final_count"]
    i32 = ir.IntType(32)
    i64 = ctx["i64"]
    i64_ptr = ctx["i64_ptr"]
    malloc_func = ctx["malloc_func"]
    strtoll_func = self.codegen.functions["strtoll"]

    # Array struct: [length, capacity, data_ptr]
    struct_size = ir.Constant(i64, 24)  # 3 * 8 bytes
    result_ptr = builder.call(malloc_func, [struct_size], name="result")
    result_i64_ptr = builder.bitcast(result_ptr, i64_ptr, name="result_i64")

    # Store length and capacity
    builder.store(final_count, result_i64_ptr)
    cap_ptr = builder.gep(result_i64_ptr, [ir.Constant(i64, 1)], name="cap_ptr")
    builder.store(final_count, cap_ptr)

    # Allocate data array (array of i64)
    data_size = builder.mul(final_count, ir.Constant(i64, 8), name="data_size")
    data_ptr = builder.call(malloc_func, [data_size], name="data")
    data_i64_ptr = builder.bitcast(data_ptr, i64_ptr, name="data_i64_ptr")

    # Store data pointer
    data_slot = builder.gep(result_i64_ptr, [ir.Constant(i64, 2)], name="data_slot")
    data_ptr_as_i64 = builder.ptrtoint(data_ptr, i64, name="data_as_i64")
    builder.store(data_ptr_as_i64, data_slot)

    idx_ptr, token_phi2, idx_val = self._begin_split_parse_loop(ctx)
    null_ptr_ptr = ir.Constant(char_ptr_ptr, None)
    base_10 = ir.Constant(i32, 10)
    int_val = builder.call(
        strtoll_func, [token_phi2, null_ptr_ptr, base_10], name="int_val"
    )
    slot_ptr = builder.gep(data_i64_ptr, [idx_val], name="slot_ptr")
    builder.store(int_val, slot_ptr)
    self._continue_split_parse_loop(ctx, idx_ptr, idx_val, token_phi2)
    self._finish_split_helper(ctx, result_i64_ptr)

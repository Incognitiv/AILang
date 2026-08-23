"""I/O, file-system, and file-bridge builtins for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression split.
"""

from __future__ import annotations

import sys
from parser.ast import StringLit
from typing import Any

from codegen.strlen_fact_cache import lookup_strlen_fact, register_value_strlen_fact
from codegen.strlen_scalarization import try_emit_known_string_length
from llvmlite import ir
from runtime.modes import CompilationContext
from transpiler.expr_builtin_file_ops import (
    _builtin_access,
    _builtin_delete_file,
    _builtin_file_can_execute,
    _builtin_file_exists,
    _builtin_file_size,
    _builtin_make_dir,
    _builtin_move_file,
    _builtin_read_bytes,
    _builtin_write_bytes,
)
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ExprGenError


class ExprBuiltinFileEmitter:
    """File / I/O builtin expression service for ``ExprGenerator``."""

    READ_ALLOC_CAP_BYTES = 1024 * 1024 * 1024

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    # Filesystem operation builtins live in expr_builtin_file_ops.py.
    _builtin_access = _builtin_access
    _builtin_delete_file = _builtin_delete_file
    _builtin_file_can_execute = _builtin_file_can_execute
    _builtin_file_exists = _builtin_file_exists
    _builtin_file_size = _builtin_file_size
    _builtin_make_dir = _builtin_make_dir
    _builtin_move_file = _builtin_move_file
    _builtin_read_bytes = _builtin_read_bytes
    _builtin_write_bytes = _builtin_write_bytes

    def _open_binary_read(
        self, filename: ir.Value, prefix: str
    ) -> tuple[ir.Value, ir.Block, ir.Block, ir.Block]:
        file_ptr = self.builder.call(
            self.codegen.get_fopen(),
            [filename, self.codegen.create_string_constant("rb")],
            name="fopen",
        )
        null_ptr = ir.Constant(file_ptr.type, None)
        file_ok = self.builder.icmp_unsigned("!=", file_ptr, null_ptr, name="file_ok")
        success_block = self.function.append_basic_block(f"{prefix}_success")
        error_block = self.function.append_basic_block(f"{prefix}_error")
        merge_block = self.function.append_basic_block(f"{prefix}_merge")
        self.builder.cbranch(file_ok, success_block, error_block)
        return file_ptr, success_block, error_block, merge_block

    def _read_file_size(self, file_ptr: ir.Value) -> ir.Value:
        zero64 = ir.Constant(ir.IntType(64), 0)
        seek_end = ir.Constant(ir.IntType(32), 2)
        seek_set = ir.Constant(ir.IntType(32), 0)
        self.builder.call(self.codegen.get_fseek(), [file_ptr, zero64, seek_end])
        file_size = self.builder.call(
            self.codegen.get_ftell(), [file_ptr], name="file_size"
        )
        self.builder.call(self.codegen.get_fseek(), [file_ptr, zero64, seek_set])
        return file_size

    def _guard_read_allocation_size(
        self, file_size: ir.Value, builtin_name: str, ok_name: str, fail_name: str
    ) -> ir.Block:
        max_alloc = ir.Constant(ir.IntType(64), self.READ_ALLOC_CAP_BYTES)
        size_ok = self.builder.icmp_signed(
            "<=", file_size, max_alloc, name=f"{ok_name}_check"
        )
        ok_block = self.function.append_basic_block(ok_name)
        fail_block = self.function.append_basic_block(fail_name)
        self.builder.cbranch(size_ok, ok_block, fail_block)
        self.builder.position_at_end(fail_block)
        err_msg = self.codegen.create_string_constant(
            f"Error: {builtin_name}: file size exceeds per-allocation cap (1 GB)\n"
        )
        fmt = self.codegen.create_string_constant("%s")
        self.builder.call(self.codegen.get_printf(), [fmt, err_msg])
        self.builder.call(
            self.codegen.get_exit_func(), [ir.Constant(ir.IntType(32), 1)]
        )
        self.builder.unreachable()
        self.builder.position_at_end(ok_block)
        return ok_block

    def _builtin_input(self, args):
        # Requires libc (fgets, printf)
        CompilationContext.require_feature("input", "input()")

        if self.codegen.input_noninteractive:
            return self.codegen.create_string_constant(self.codegen.input_default)

        buffer_size = 1024
        char_type = ir.IntType(8)
        char_ptr = char_type.as_pointer()

        buffer = self.builder.alloca(
            ir.ArrayType(char_type, buffer_size), name="input_buffer"
        )
        buffer_ptr = self.builder.bitcast(buffer, char_ptr, name="buffer_ptr")

        # Initialize buffer[0] = '\0' so callers see an empty string on
        # EOF (when fgets returns NULL and never writes to buffer). Without
        # this, EOF would return uninitialized stack contents and callers
        # couldn't reliably detect "no more input."
        self.builder.store(ir.Constant(char_type, 0), buffer_ptr)

        if args:
            if len(args) != 1:
                raise ExprGenError("input() accepts at most one prompt argument")
            prompt = self.generate_expr(args[ARG_FIRST])
            self.builder.call(self.codegen.get_printf(), [prompt])

        # get_stdin() now returns the FILE* directly (handles POSIX vs
        # Windows UCRT __acrt_iob_func(0) internally).
        stdin_ptr = self.codegen.get_stdin()
        fgets_ret = self.builder.call(
            self.codegen.get_fgets(),
            [buffer_ptr, ir.Constant(ir.IntType(32), buffer_size), stdin_ptr],
            name="fgets_call",
        )

        # If fgets returned null (EOF), return empty string
        null_ptr = ir.Constant(char_ptr, None)
        got_input = self.builder.icmp_unsigned(
            "!=", fgets_ret, null_ptr, name="fgets_ok"
        )
        merge_block = self.function.append_basic_block("input_merge")
        strip_block = self.function.append_basic_block("strip_newline")
        self.builder.cbranch(got_input, strip_block, merge_block)

        # Strip trailing newline if present
        self.builder.position_at_end(strip_block)
        strlen = self.builder.call(
            self.codegen.get_strlen(), [buffer_ptr], name="input_len"
        )
        zero = ir.Constant(ir.IntType(64), 0)
        one = ir.Constant(ir.IntType(64), 1)
        newline = ir.Constant(char_type, 10)
        null_char = ir.Constant(char_type, 0)

        len_gt_zero = self.builder.icmp_signed(">", strlen, zero, name="len_gt_zero")
        then_block = self.function.append_basic_block("strip_if_newline")
        self.builder.cbranch(len_gt_zero, then_block, merge_block)

        self.builder.position_at_end(then_block)
        last_index = self.builder.sub(strlen, one, name="last_index")
        last_char_ptr = self.builder.gep(buffer_ptr, [last_index], name="last_char_ptr")
        last_char = self.builder.load(last_char_ptr, name="last_char")
        is_newline = self.builder.icmp_signed(
            "==", last_char, newline, name="is_newline"
        )

        remove_block = self.function.append_basic_block("remove_newline")
        self.builder.cbranch(is_newline, remove_block, merge_block)

        self.builder.position_at_end(remove_block)
        self.builder.store(null_char, last_char_ptr)
        self.builder.branch(merge_block)

        # Merge
        self.builder.position_at_end(merge_block)
        return buffer_ptr

    def _builtin_read_stdin(self, args):
        """read_stdin() -> heap-owned string containing all stdin bytes."""
        CompilationContext.require_feature("input", "read_stdin()")

        if args:
            raise ExprGenError("read_stdin() expects no arguments")

        if self.codegen.input_noninteractive:
            return self.codegen.create_string_constant(self.codegen.input_default)

        int64 = ir.IntType(64)
        int32 = ir.IntType(32)
        char_type = ir.IntType(8)
        char_ptr = char_type.as_pointer()
        eof = ir.Constant(int32, -1)
        zero64 = ir.Constant(int64, 0)
        one64 = ir.Constant(int64, 1)
        two64 = ir.Constant(int64, 2)
        initial_cap = ir.Constant(int64, 4096)
        max_half = ir.Constant(int64, self.READ_ALLOC_CAP_BYTES // 2)

        cap_slot = self.builder.alloca(int64, name="stdin_cap")
        len_slot = self.builder.alloca(int64, name="stdin_len")
        buf_slot = self.builder.alloca(char_ptr, name="stdin_buf_slot")
        self.builder.store(initial_cap, cap_slot)
        self.builder.store(zero64, len_slot)
        initial_buf = self.codegen.string_alloc(initial_cap, "stdin_buf")
        self.builder.store(initial_buf, buf_slot)

        stdin_ptr = self.codegen.get_stdin()
        loop_block = self.function.append_basic_block("read_stdin_loop")
        have_char_block = self.function.append_basic_block("read_stdin_char")
        grow_check_block = self.function.append_basic_block("read_stdin_grow_check")
        grow_block = self.function.append_basic_block("read_stdin_grow")
        write_block = self.function.append_basic_block("read_stdin_write")
        done_block = self.function.append_basic_block("read_stdin_done")
        cap_fail_block = self.function.append_basic_block("read_stdin_cap_fail")

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)
        ch = self.builder.call(self.codegen.get_fgetc(), [stdin_ptr], name="stdin_ch")
        is_eof = self.builder.icmp_signed("==", ch, eof, name="stdin_eof")
        self.builder.cbranch(is_eof, done_block, have_char_block)

        self.builder.position_at_end(have_char_block)
        cur_len = self.builder.load(len_slot, name="stdin_len_now")
        cur_cap = self.builder.load(cap_slot, name="stdin_cap_now")
        needed = self.builder.add(cur_len, one64, name="stdin_needed")
        needs_growth = self.builder.icmp_unsigned(
            ">=", needed, cur_cap, name="stdin_needs_growth"
        )
        self.builder.cbranch(needs_growth, grow_check_block, write_block)

        self.builder.position_at_end(grow_check_block)
        cur_cap_check = self.builder.load(cap_slot, name="stdin_cap_check")
        cap_too_large = self.builder.icmp_unsigned(
            ">", cur_cap_check, max_half, name="stdin_cap_too_large"
        )
        self.builder.cbranch(cap_too_large, cap_fail_block, grow_block)

        self.builder.position_at_end(cap_fail_block)
        err_msg = self.codegen.create_string_constant(
            "Error: read_stdin() input exceeds per-allocation cap (1 GB)\n"
        )
        fmt = self.codegen.create_string_constant("%s")
        self.builder.call(self.codegen.get_printf(), [fmt, err_msg])
        self.builder.call(
            self.codegen.get_exit_func(), [ir.Constant(ir.IntType(32), 1)]
        )
        self.builder.unreachable()

        self.builder.position_at_end(grow_block)
        old_buf = self.builder.load(buf_slot, name="stdin_old_buf")
        old_cap = self.builder.load(cap_slot, name="stdin_old_cap")
        new_cap = self.builder.mul(old_cap, two64, name="stdin_new_cap")
        new_buf = self.builder.call(
            self.codegen.get_realloc(), [old_buf, new_cap], name="stdin_new_buf"
        )
        self.builder.store(new_buf, buf_slot)
        self.builder.store(new_cap, cap_slot)
        self.builder.branch(write_block)

        self.builder.position_at_end(write_block)
        buf_now = self.builder.load(buf_slot, name="stdin_buf_now")
        len_now = self.builder.load(len_slot, name="stdin_len_write")
        out_ptr = self.builder.gep(buf_now, [len_now], name="stdin_out_ptr")
        out_ch = self.builder.trunc(ch, char_type, name="stdin_byte")
        self.builder.store(out_ch, out_ptr)
        next_len = self.builder.add(len_now, one64, name="stdin_next_len")
        self.builder.store(next_len, len_slot)
        self.builder.branch(loop_block)

        self.builder.position_at_end(done_block)
        final_buf = self.builder.load(buf_slot, name="stdin_final_buf")
        final_len = self.builder.load(len_slot, name="stdin_final_len")
        null_ptr = self.builder.gep(final_buf, [final_len], name="stdin_null_ptr")
        self.builder.store(ir.Constant(char_type, 0), null_ptr)
        return final_buf

    def _builtin_current_dir(self, args):
        """current_dir() -> heap-owned current working directory string."""
        CompilationContext.require_feature("file_io", "current_dir()")

        if args:
            raise ExprGenError("current_dir() expects no arguments")

        char_type = ir.IntType(8)
        char_ptr = char_type.as_pointer()
        buffer_size = 4096
        buffer = self.builder.alloca(
            ir.ArrayType(char_type, buffer_size), name="current_dir_buffer"
        )
        buffer_ptr = self.builder.bitcast(buffer, char_ptr, name="current_dir_ptr")

        is_windows = (
            "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
        )
        fn_name = "_getcwd" if is_windows else "getcwd"
        fn_ty = ir.FunctionType(
            char_ptr,
            [char_ptr, ir.IntType(32) if is_windows else ir.IntType(64)],
        )
        getcwd_fn = self.codegen._declare_external(fn_name, fn_ty)
        size_arg = ir.Constant(
            ir.IntType(32) if is_windows else ir.IntType(64), buffer_size
        )
        got = self.builder.call(getcwd_fn, [buffer_ptr, size_arg], name="getcwd_call")
        null_ptr = ir.Constant(char_ptr, None)
        ok = self.builder.icmp_unsigned("!=", got, null_ptr, name="getcwd_ok")

        success_block = self.function.append_basic_block("current_dir_success")
        error_block = self.function.append_basic_block("current_dir_error")
        merge_block = self.function.append_basic_block("current_dir_merge")
        self.builder.cbranch(ok, success_block, error_block)

        self.builder.position_at_end(success_block)
        success_copy = self.builder.call(
            self.codegen.get_strdup(), [buffer_ptr], name="current_dir_copy"
        )
        self.builder.branch(merge_block)
        success_end = self.builder.block

        self.builder.position_at_end(error_block)
        error_copy = self.builder.call(
            self.codegen.get_strdup(),
            [self.codegen.create_string_constant("")],
            name="current_dir_empty",
        )
        self.builder.branch(merge_block)
        error_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(char_ptr, name="current_dir_result")
        phi.add_incoming(success_copy, success_end)
        phi.add_incoming(error_copy, error_end)
        return phi

    def _builtin_change_dir(self, args):
        """change_dir(path) -> 0 on success, non-zero on failure."""
        CompilationContext.require_feature("file_io", "change_dir()")

        if len(args) != 1:
            raise ExprGenError("change_dir() expects path as the only argument")
        char_ptr = ir.IntType(8).as_pointer()
        path = self.generate_expr(args[ARG_FIRST])

        is_windows = (
            "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
        )
        fn_name = "_chdir" if is_windows else "chdir"
        fn_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
        chdir_fn = self.codegen._declare_external(fn_name, fn_ty)
        rc = self.builder.call(chdir_fn, [path], name="chdir_rc")
        return self.builder.sext(rc, ir.IntType(64), name="chdir_rc_64")

    def _builtin_read_file(self, args):
        # Requires file I/O (fopen, fread, etc.)
        CompilationContext.require_feature("file_io", "read_file()")

        if len(args) != 1:
            raise ExprGenError("read_file() expects filename as the only argument")
        filename = self.generate_expr(args[ARG_FIRST])
        file_ptr, success_block, error_block, merge_block = self._open_binary_read(
            filename, "read"
        )

        self.builder.position_at_end(error_block)
        empty = self.codegen.create_string_constant("")
        self.builder.branch(merge_block)
        error_end = self.builder.block

        self.builder.position_at_end(success_block)
        # 64-bit-safe seek/tell. Plain ftell()'s long return is 32-bit
        # on Windows (LLP64) - get_fseek/get_ftell are now backed by
        # _fseeki64/_ftelli64 (Win) or fseeko/ftello (POSIX).
        file_size = self._read_file_size(file_ptr)
        self._guard_read_allocation_size(
            file_size, "read_file", "size_ok", "size_too_large"
        )
        one64 = ir.Constant(ir.IntType(64), 1)
        buffer_size = self.builder.add(file_size, one64, name="buffer_size")
        buffer = self.codegen.string_alloc(buffer_size, "file_buffer")
        bytes_read = self.builder.call(
            self.codegen.get_fread(),
            [buffer, one64, file_size, file_ptr],
            name="fread",
        )
        null_char_ptr = self.builder.gep(buffer, [bytes_read], name="null_char_ptr")
        self.builder.store(ir.Constant(ir.IntType(8), 0), null_char_ptr)
        self.builder.call(self.codegen.get_fclose(), [file_ptr])
        self.builder.branch(merge_block)
        success_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(buffer.type, name="file_contents")
        phi.add_incoming(empty, error_end)
        phi.add_incoming(buffer, success_end)
        len_phi = self.builder.phi(ir.IntType(64), name="file_contents_len")
        len_phi.add_incoming(ir.Constant(ir.IntType(64), 0), error_end)
        len_phi.add_incoming(bytes_read, success_end)
        register_value_strlen_fact(self.codegen, phi, len_phi)
        return phi

    def _builtin_write_file(self, args):
        """Write content to file with auto-optimized streaming in loops.

        When called inside a loop with a literal path, uses streaming mode
        (keeps file open, appends). Otherwise uses safe single-write mode.
        """
        CompilationContext.require_feature("file_io", "write_file()")

        if len(args) != 2:
            raise ExprGenError("write_file() expects filename and content")

        # Check if we should use streaming mode:
        # 1. Must be inside a loop (loop_depth > 0)
        # 2. Path must be a string literal (not a variable)
        use_streaming = self.codegen.loop_depth > 0 and isinstance(
            args[ARG_FIRST], StringLit
        )

        filename = self.generate_expr(args[ARG_FIRST])
        content = self.generate_expr(args[ARG_SECOND])

        if use_streaming:
            # Use streaming write - keeps file open for subsequent writes
            result = self.builder.call(
                self.codegen.get_stream_write_func(),
                [filename, content],
                name="stream_write_result",
            )
            return result

        # Standard single-write mode (safe, always truncates)
        file_ptr = self.builder.call(
            self.codegen.get_fopen(),
            [filename, self.codegen.create_string_constant("wb")],
            name="fopen",
        )

        null_ptr = ir.Constant(file_ptr.type, None)
        file_ok = self.builder.icmp_unsigned("!=", file_ptr, null_ptr, name="file_ok")
        success_block = self.function.append_basic_block("write_success")
        error_block = self.function.append_basic_block("write_error")
        merge_block = self.function.append_basic_block("write_merge")
        self.builder.cbranch(file_ok, success_block, error_block)

        self.builder.position_at_end(error_block)
        error_result = ir.Constant(ir.IntType(64), 0)
        self.builder.branch(merge_block)
        error_end = self.builder.block

        self.builder.position_at_end(success_block)
        length = (
            lookup_strlen_fact(self.codegen, args[ARG_SECOND])
            or try_emit_known_string_length(self.codegen, args[ARG_SECOND])
            or self.builder.call(
                self.codegen.get_strlen(), [content], name="content_len"
            )
        )
        one64 = ir.Constant(ir.IntType(64), 1)
        self.builder.call(self.codegen.get_fwrite(), [content, one64, length, file_ptr])
        self.builder.call(self.codegen.get_fclose(), [file_ptr])
        success_result = ir.Constant(ir.IntType(64), 1)
        self.builder.branch(merge_block)
        success_end = self.builder.block

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(ir.IntType(64), name="write_result")
        phi.add_incoming(error_result, error_end)
        phi.add_incoming(success_result, success_end)
        return phi

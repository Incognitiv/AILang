"""
RuntimeHelpers - service for LLVM runtime helper function builders.

Phase A6 slice: stream and stdin helpers extracted from ``CodeGen``.
"""

from __future__ import annotations

import sys
from typing import Any

from llvmlite import ir


class RuntimeHelpers:
    """Runtime helper builders for the LLVM backend."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def get_stream_file_global(self) -> ir.GlobalVariable:
        """Get or create global variable for cached file handle (FILE*)."""
        if self._cg._stream_file_global is None:
            char_ptr = ir.IntType(8).as_pointer()
            self._cg._stream_file_global = ir.GlobalVariable(
                self._cg.module, char_ptr, "_ailang_stream_file"
            )
            self._cg._stream_file_global.initializer = ir.Constant(char_ptr, None)
            self._cg._stream_file_global.linkage = "private"
        return self._cg._stream_file_global

    def get_stream_path_global(self) -> ir.GlobalVariable:
        """Get or create global variable for cached file path (4096 bytes)."""
        if self._cg._stream_path_global is None:
            path_array_ty = ir.ArrayType(ir.IntType(8), 4096)
            self._cg._stream_path_global = ir.GlobalVariable(
                self._cg.module, path_array_ty, "_ailang_stream_path"
            )
            self._cg._stream_path_global.initializer = ir.Constant(
                path_array_ty, bytearray(4096)
            )
            self._cg._stream_path_global.linkage = "private"
        return self._cg._stream_path_global

    def get_stream_write_func(self) -> ir.Function:
        """Get or create the streaming write function."""
        if self._cg._stream_write_func is not None:
            return self._cg._stream_write_func

        char_ptr = ir.IntType(8).as_pointer()
        int64 = ir.IntType(64)
        int32 = ir.IntType(32)

        func_ty = ir.FunctionType(int64, [char_ptr, char_ptr])
        func = ir.Function(self._cg.module, func_ty, "_ailang_stream_write")
        func.linkage = "private"
        self._cg._stream_write_func = func

        path_arg, data_arg = func.args
        path_arg.name = "path"
        data_arg.name = "data"

        entry = func.append_basic_block("entry")
        check_cached = func.append_basic_block("check_cached")
        use_cached = func.append_basic_block("use_cached")
        close_and_reopen = func.append_basic_block("close_and_reopen")
        open_new = func.append_basic_block("open_new")
        open_ok = func.append_basic_block("open_ok")
        do_write = func.append_basic_block("do_write")
        error_exit = func.append_basic_block("error_exit")

        builder = ir.IRBuilder(entry)

        stream_file = self.get_stream_file_global()
        stream_path = self.get_stream_path_global()
        cached_handle = builder.load(stream_file, name="cached_handle")

        null_ptr = ir.Constant(char_ptr, None)
        has_cached = builder.icmp_unsigned(
            "!=", cached_handle, null_ptr, name="has_cached"
        )
        builder.cbranch(has_cached, check_cached, open_new)

        builder.position_at_end(check_cached)
        path_ptr = builder.gep(
            stream_path, [ir.Constant(int64, 0), ir.Constant(int64, 0)], name="path_ptr"
        )
        cmp_result = builder.call(
            self._cg.get_strcmp(), [path_ptr, path_arg], name="cmp"
        )
        paths_match = builder.icmp_signed(
            "==", cmp_result, ir.Constant(int32, 0), name="match"
        )
        builder.cbranch(paths_match, use_cached, close_and_reopen)

        builder.position_at_end(use_cached)
        builder.branch(do_write)

        builder.position_at_end(close_and_reopen)
        builder.call(self._cg.get_fclose(), [cached_handle])
        builder.branch(open_new)

        builder.position_at_end(open_new)
        mode_str = self._cg.create_string_constant_gep("wb", builder)
        new_handle = builder.call(
            self._cg.get_fopen(), [path_arg, mode_str], name="new_handle"
        )
        is_valid = builder.icmp_unsigned("!=", new_handle, null_ptr, name="is_valid")
        builder.cbranch(is_valid, open_ok, error_exit)

        builder.position_at_end(open_ok)
        path_ptr2 = builder.gep(
            stream_path,
            [ir.Constant(int64, 0), ir.Constant(int64, 0)],
            name="path_ptr2",
        )
        iofbf = ir.Constant(int32, 0)
        buf_size = ir.Constant(int64, 65536)
        builder.call(self._cg.get_setvbuf(), [new_handle, null_ptr, iofbf, buf_size])
        builder.store(new_handle, stream_file)
        builder.call(
            self._cg.get_strncpy(),
            [path_ptr2, path_arg, ir.Constant(int64, 4095)],
        )
        builder.branch(do_write)

        builder.position_at_end(do_write)
        handle_phi = builder.phi(char_ptr, name="handle")
        handle_phi.add_incoming(cached_handle, use_cached)
        handle_phi.add_incoming(new_handle, open_ok)

        data_len = builder.call(self._cg.get_strlen(), [data_arg], name="data_len")
        one64 = ir.Constant(int64, 1)
        builder.call(self._cg.get_fwrite(), [data_arg, one64, data_len, handle_phi])
        builder.ret(ir.Constant(int64, 1))

        builder.position_at_end(error_exit)
        builder.store(null_ptr, stream_file)
        builder.ret(ir.Constant(int64, 0))

        return func

    def get_stream_close_func(self) -> ir.Function:
        """Get or create function to close cached stream."""
        if self._cg._stream_close_func is not None:
            return self._cg._stream_close_func

        char_ptr = ir.IntType(8).as_pointer()
        int64 = ir.IntType(64)

        func_ty = ir.FunctionType(ir.VoidType(), [])
        func = ir.Function(self._cg.module, func_ty, "_ailang_close_streams")
        func.linkage = "private"
        self._cg._stream_close_func = func

        entry = func.append_basic_block("entry")
        do_close = func.append_basic_block("do_close")
        done = func.append_basic_block("done")

        builder = ir.IRBuilder(entry)
        stream_file = self.get_stream_file_global()
        cached_handle = builder.load(stream_file, name="cached_handle")
        null_ptr = ir.Constant(char_ptr, None)
        has_cached = builder.icmp_unsigned(
            "!=", cached_handle, null_ptr, name="has_cached"
        )
        builder.cbranch(has_cached, do_close, done)

        builder.position_at_end(do_close)
        builder.call(self._cg.get_fclose(), [cached_handle])
        builder.store(null_ptr, stream_file)
        stream_path = self.get_stream_path_global()
        path_ptr = builder.gep(
            stream_path, [ir.Constant(int64, 0), ir.Constant(int64, 0)], name="path_ptr"
        )
        builder.store(ir.Constant(ir.IntType(8), 0), path_ptr)
        builder.branch(done)

        builder.position_at_end(done)
        builder.ret_void()

        return func

    def get_stdin(self) -> ir.Value:
        """Get the FILE* for stdin. Platform-aware."""
        char_ptr = ir.IntType(8).as_pointer()
        is_windows = (
            "windows" in self._cg.module.triple.lower() or sys.platform == "win32"
        )
        if is_windows:
            existing_cache = self._cg.module.globals.get("_ailang_stdin_cache")
            if existing_cache is None:
                cache = ir.GlobalVariable(
                    self._cg.module, char_ptr, "_ailang_stdin_cache"
                )
                cache.linkage = "internal"
                cache.initializer = ir.Constant(char_ptr, None)
            else:
                cache = existing_cache

            cached_val = self._cg.current_builder.load(cache, name="cached_stdin")
            cached_int = self._cg.current_builder.ptrtoint(
                cached_val, ir.IntType(64), name="cached_stdin_int"
            )
            is_null = self._cg.current_builder.icmp_unsigned(
                "==",
                cached_int,
                ir.Constant(ir.IntType(64), 0),
                name="stdin_cache_null",
            )
            init_block = self._cg.current_function.append_basic_block("stdin_init")
            use_block = self._cg.current_function.append_basic_block("stdin_use")
            self._cg.current_builder.cbranch(is_null, init_block, use_block)

            self._cg.current_builder.position_at_end(init_block)
            existing_fn = self._cg.module.globals.get("_fdopen")
            if isinstance(existing_fn, ir.Function):
                fdopen_fn = existing_fn
            else:
                ty = ir.FunctionType(char_ptr, [ir.IntType(32), char_ptr])
                fdopen_fn = ir.Function(self._cg.module, ty, "_fdopen")
            mode_str = self._cg.create_string_constant("r")
            new_file = self._cg.current_builder.call(
                fdopen_fn,
                [ir.Constant(ir.IntType(32), 0), mode_str],
                name="stdin_init",
            )
            self._cg.current_builder.store(new_file, cache)
            self._cg.current_builder.branch(use_block)

            self._cg.current_builder.position_at_end(use_block)
            return self._cg.current_builder.load(cache, name="stdin_ptr")
        if self._cg.stdin_var is None:
            self._cg.stdin_var = ir.GlobalVariable(self._cg.module, char_ptr, "stdin")
            self._cg.stdin_var.linkage = "external"
        return self._cg.current_builder.load(self._cg.stdin_var, name="stdin_ptr")

"""File I/O runtime declarations for LLVM codegen."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class RuntimeDeclsFileIOMixin:
    _cg: Any

    def get_fopen(self) -> ir.Function:
        """Lazy declaration of fopen"""
        if self._cg.fopen_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            fopen_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr])
            self._cg.fopen_func = ir.Function(self._cg.module, fopen_ty, "fopen")
        return self._cg.fopen_func

    def get_fclose(self) -> ir.Function:
        """Lazy declaration of fclose"""
        if self._cg.fclose_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            fclose_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.fclose_func = ir.Function(self._cg.module, fclose_ty, "fclose")
        return self._cg.fclose_func

    def get_fwrite(self) -> ir.Function:
        """Lazy declaration of fwrite"""
        if self._cg.fwrite_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            fwrite_ty = ir.FunctionType(
                ir.IntType(64),
                [char_ptr, ir.IntType(64), ir.IntType(64), char_ptr],
            )
            self._cg.fwrite_func = ir.Function(self._cg.module, fwrite_ty, "fwrite")
        return self._cg.fwrite_func

    def get_setvbuf(self) -> ir.Function:
        """Lazy declaration of setvbuf for buffered I/O."""
        if self._cg.setvbuf_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            setvbuf_ty = ir.FunctionType(
                ir.IntType(32),
                [char_ptr, char_ptr, ir.IntType(32), ir.IntType(64)],
            )
            self._cg.setvbuf_func = ir.Function(self._cg.module, setvbuf_ty, "setvbuf")
        return self._cg.setvbuf_func

    def get_fread(self) -> ir.Function:
        """Lazy declaration of fread"""
        if self._cg.fread_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            fread_ty = ir.FunctionType(
                ir.IntType(64),
                [char_ptr, ir.IntType(64), ir.IntType(64), char_ptr],
            )
            self._cg.fread_func = ir.Function(self._cg.module, fread_ty, "fread")
        return self._cg.fread_func

    def get_fseek(self) -> ir.Function:
        """Lazy declaration of 64-bit-safe fseek.

        Plain ``fseek`` takes a ``long`` offset which is 32-bit on
        Windows (LLP64). Files >2 GB overflow. Pick the
        platform-appropriate 64-bit symbol so seeks past 2 GB work."""
        if self._cg.fseek_func is None:
            import sys as _sys

            char_ptr = ir.IntType(8).as_pointer()
            fseek_ty = ir.FunctionType(
                ir.IntType(32),
                [char_ptr, ir.IntType(64), ir.IntType(32)],
            )
            symbol = "_fseeki64" if _sys.platform.startswith("win") else "fseeko"
            self._cg.fseek_func = ir.Function(self._cg.module, fseek_ty, symbol)
        return self._cg.fseek_func

    def get_ftell(self) -> ir.Function:
        """Lazy declaration of 64-bit-safe ftell.

        Plain ``ftell`` returns ``long`` (32-bit on Windows LLP64);
        i64-typed ``ftell`` would receive only the low 32 bits with
        garbage in the high half.

        On Windows JIT builds, ``_ftelli64`` has proven brittle in practice
        (resolved symbol can still crash). Use ``ftell`` as a stable fallback.
        The read_file cap is 1GB, which fits inside 32-bit signed ``long``.
        """
        if self._cg.ftell_func is None:
            import sys as _sys

            char_ptr = ir.IntType(8).as_pointer()
            ftell_ty = ir.FunctionType(ir.IntType(64), [char_ptr])
            if _sys.platform.startswith("win"):
                symbol = "ftell"
            else:
                symbol = "ftello"
            self._cg.ftell_func = ir.Function(self._cg.module, ftell_ty, symbol)
        return self._cg.ftell_func

    def get_fgets(self) -> ir.Function:
        """Lazy declaration of fgets"""
        if self._cg.fgets_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            fgets_ty = ir.FunctionType(char_ptr, [char_ptr, ir.IntType(32), char_ptr])
            self._cg.fgets_func = ir.Function(self._cg.module, fgets_ty, "fgets")
        return self._cg.fgets_func

    def get_fgetc(self) -> ir.Function:
        """Lazy declaration of fgetc"""
        if self._cg.fgetc_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            fgetc_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.fgetc_func = ir.Function(self._cg.module, fgetc_ty, "fgetc")
        return self._cg.fgetc_func

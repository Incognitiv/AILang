"""String and memory runtime declarations for LLVM codegen."""

from __future__ import annotations

from typing import Any

from ast_access import arg_at
from llvmlite import ir


class RuntimeDeclsStringMemMixin:
    _cg: Any
    _declare_external: Any

    def get_printf(self) -> ir.Function:
        """Lazy declaration of printf"""
        if self._cg.printf_func is None:
            voidptr_ty = ir.IntType(8).as_pointer()
            printf_ty = ir.FunctionType(ir.IntType(32), [voidptr_ty], var_arg=True)
            self._cg.printf_func = self._declare_external("printf", printf_ty)
        return self._cg.printf_func

    def get_puts(self) -> ir.Function:
        """Lazy declaration of puts for already-formatted constant lines."""
        if self._cg.puts_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            puts_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
            self._cg.puts_func = self._declare_external("puts", puts_ty)
        return self._cg.puts_func

    def get_strlen(self) -> ir.Function:
        """Lazy declaration of strlen"""
        if self._cg.strlen_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strlen_ty = ir.FunctionType(ir.IntType(64), [char_ptr])
            self._cg.strlen_func = self._declare_external("strlen", strlen_ty)
        return self._cg.strlen_func

    def get_strcmp(self) -> ir.Function:
        """Lazy declaration of strcmp"""
        if self._cg.strcmp_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strcmp_ty = ir.FunctionType(ir.IntType(32), [char_ptr, char_ptr])
            self._cg.strcmp_func = self._declare_external("strcmp", strcmp_ty)
        return self._cg.strcmp_func

    def get_sprintf(self) -> ir.Function:
        """Lazy declaration of sprintf (for string interpolation)"""
        if self._cg.sprintf_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            sprintf_ty = ir.FunctionType(
                ir.IntType(32), [char_ptr, char_ptr], var_arg=True
            )
            self._cg.sprintf_func = self._declare_external("sprintf", sprintf_ty)
        return self._cg.sprintf_func

    def get_snprintf(self) -> ir.Function:
        """Lazy declaration of snprintf (safe string interpolation).

        On Windows, ``snprintf`` is an inline wrapper in UCRT headers;
        the actual exported symbol is ``_snprintf`` in msvcrt.dll.
        """
        if self._cg.snprintf_func is None:
            import platform

            char_ptr = ir.IntType(8).as_pointer()
            is_windows = platform.system() == "Windows"
            func_name = "_snprintf" if is_windows else "snprintf"
            snprintf_ty = ir.FunctionType(
                ir.IntType(32),
                [char_ptr, ir.IntType(64), char_ptr],
                var_arg=True,
            )
            self._cg.snprintf_func = self._declare_external(func_name, snprintf_ty)
        return self._cg.snprintf_func

    def get_malloc(self) -> ir.Function:
        """Lazy declaration of malloc"""
        if self._cg.malloc_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            malloc_ty = ir.FunctionType(char_ptr, [ir.IntType(64)])
            self._cg.malloc_func = self._declare_external("malloc", malloc_ty)
            self._cg.functions["malloc"] = self._cg.malloc_func
        return self._cg.malloc_func

    def get_strcpy(self) -> ir.Function:
        """Lazy declaration of strcpy"""
        if self._cg.strcpy_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strcpy_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr])
            self._cg.strcpy_func = self._declare_external("strcpy", strcpy_ty)
        return self._cg.strcpy_func

    def get_strcat(self) -> ir.Function:
        """Lazy declaration of strcat"""
        if self._cg.strcat_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strcat_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr])
            self._cg.strcat_func = self._declare_external("strcat", strcat_ty)
        return self._cg.strcat_func

    def get_strdup(self) -> ir.Function:
        """Get or create a portable strdup implementation.

        Uses malloc + strcpy because strdup isn't available on all
        platforms. Unlike the other ``get_*`` accessors here, this
        emits a real function body, not just a forward declaration.
        """
        func_name = "_ailang_strdup"
        if func_name in self._cg.module.globals:
            return self._cg.module.globals[func_name]

        char_ptr = ir.IntType(8).as_pointer()
        func_ty = ir.FunctionType(char_ptr, [char_ptr])
        func = ir.Function(self._cg.module, func_ty, func_name)

        entry = func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        src = arg_at(func, 0)
        src.name = "src"

        strlen_fn = self.get_strlen()
        malloc_fn = self.get_malloc()
        strcpy_fn = self.get_strcpy()

        length = builder.call(strlen_fn, [src], name="len")
        size = builder.add(length, ir.Constant(ir.IntType(64), 1), name="size")
        dst = builder.call(malloc_fn, [size], name="dst")
        builder.call(strcpy_fn, [dst, src])
        builder.ret(dst)

        return func

    def get_strncpy(self) -> ir.Function:
        """Lazy declaration of strncpy. The cache field is intentionally
        not pre-initialized in ``CodeGen.__init__`` (older code path);
        check ``hasattr`` to preserve byte-identity with the legacy
        version."""
        if not hasattr(self._cg, "strncpy_func") or self._cg.strncpy_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strncpy_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr, ir.IntType(64)])
            self._cg.strncpy_func = ir.Function(self._cg.module, strncpy_ty, "strncpy")
        return self._cg.strncpy_func

    def get_memcpy(self) -> ir.Function:
        """Lazy declaration of memcpy"""
        if self._cg.memcpy_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            memcpy_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr, ir.IntType(64)])
            self._cg.memcpy_func = self._declare_external("memcpy", memcpy_ty)
        return self._cg.memcpy_func

    def get_realloc(self) -> ir.Function:
        """Lazy declaration of realloc"""
        if self._cg.realloc_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            realloc_ty = ir.FunctionType(char_ptr, [char_ptr, ir.IntType(64)])
            self._cg.realloc_func = self._declare_external("realloc", realloc_ty)
        return self._cg.realloc_func

    def get_strstr(self) -> ir.Function:
        """Lazy declaration of strstr"""
        if self._cg.strstr_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strstr_ty = ir.FunctionType(char_ptr, [char_ptr, char_ptr])
            self._cg.strstr_func = self._declare_external("strstr", strstr_ty)
        return self._cg.strstr_func

    def get_strncmp(self) -> ir.Function:
        """Lazy declaration of strncmp -- compare n bytes of two
        strings. The legacy version skipped ``_declare_external`` and
        called ``ir.Function`` directly; preserved here for
        byte-identity."""
        if self._cg.strncmp_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            strncmp_ty = ir.FunctionType(
                ir.IntType(32), [char_ptr, char_ptr, ir.IntType(64)]
            )
            self._cg.strncmp_func = ir.Function(self._cg.module, strncmp_ty, "strncmp")
        return self._cg.strncmp_func

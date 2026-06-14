"""
DebugInfoEmitter — service that emits DWARF debug-info metadata for
the LLVM backend.

Phase A2 of the LLVM-side architectural pivot. Lifts the seven DI
methods off ``CodeGen``: DIFile / DICompileUnit / DISubroutineType
caching, DISubprogram emission for each function, DILocation
construction + per-line memoization. Backends that consume LLVM IR
(``ailang foo.ail -o foo``) flow that into ``gdb foo`` and
``objdump --dwarf foo`` without further plumbing.

The service holds a back-reference to ``CodeGen`` and proxies
attribute access through it, matching the pattern established by
``RuntimeDecls`` in Phase A1. Caches that used to live as
``self._di_files`` / ``self._di_cus`` / ``self._di_loc_cache`` /
``self._di_subroutine_type`` / ``self._di_module_flags_emitted`` /
``self._current_di_sp`` stay on the codegen via the proxy -- no
state moves, only methods.
"""

from __future__ import annotations

from typing import Any, Optional

from llvmlite import ir


class DebugInfoEmitter:
    """DWARF metadata emitter for the LLVM backend. Constructed once
    per compile from ``CodeGen.__init__``."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        # Fall through for cache fields that still live on CodeGen --
        # ``_di_files``, ``_di_cus``, etc.
        return getattr(self._cg, name)

    def _ensure_dwarf_module_flags(self) -> None:
        """Emit module-level DWARF version flags exactly once."""
        if self._cg._di_module_flags_emitted:
            return
        self._cg._di_module_flags_emitted = True
        i32 = ir.IntType(32)
        dwarf_ver = self._cg.module.add_metadata([i32(2), "Dwarf Version", i32(4)])
        debug_ver = self._cg.module.add_metadata([i32(2), "Debug Info Version", i32(3)])
        self._cg.module.add_named_metadata("llvm.module.flags", dwarf_ver)
        self._cg.module.add_named_metadata("llvm.module.flags", debug_ver)

    def _get_di_file(self, source_path: str) -> ir.DIValue:
        """Memoized DIFile per absolute source path."""
        key = source_path or "<unknown>"
        if key in self._cg._di_files:
            return self._cg._di_files[key]
        # Split into directory + basename; DIFile stores them separately
        # so debuggers can do their own path search.
        if source_path:
            sep_pos = max(source_path.rfind("/"), source_path.rfind("\\"))
            if sep_pos >= 0:
                directory = source_path[:sep_pos]
                filename = source_path[sep_pos + 1 :]
            else:
                directory = ""
                filename = source_path
        else:
            directory = ""
            filename = "<unknown>"
        di_file = self._cg.module.add_debug_info(
            "DIFile", {"filename": filename, "directory": directory}
        )
        self._cg._di_files[key] = di_file
        return di_file

    def _get_di_compile_unit(self, di_file: ir.DIValue, source_path: str) -> ir.DIValue:
        """Memoized DICompileUnit per source file (one CU per .ail).

        ``DW_LANG_C99`` is the closest stock value LLVM understands;
        there's no ``DW_LANG_AILANG``. Using C99 means external tools
        that don't know our language degrade gracefully into "this
        is C-ish"."""
        if source_path in self._cg._di_cus:
            return self._cg._di_cus[source_path]
        di_cu = self._cg.module.add_debug_info(
            "DICompileUnit",
            {
                "language": ir.DIToken("DW_LANG_C99"),
                "file": di_file,
                "producer": "ailang",
                "emissionKind": ir.DIToken("FullDebug"),
                "isOptimized": False,
                "runtimeVersion": 0,
            },
            is_distinct=True,
        )
        self._cg.module.add_named_metadata("llvm.dbg.cu", di_cu)
        self._cg._di_cus[source_path] = di_cu
        return di_cu

    def _get_di_subroutine_type(self) -> ir.DIValue:
        """Generic ``!DISubroutineType`` used for every function -- we
        don't emit per-parameter DI types in v1, so all functions
        share one opaque signature placeholder."""
        if self._cg._di_subroutine_type is None:
            self._cg._di_subroutine_type = self._cg.module.add_debug_info(
                "DISubroutineType",
                {"types": self._cg.module.add_metadata([])},
            )
        return self._cg._di_subroutine_type

    def emit_dwarf_subprogram(
        self,
        func: ir.Function,
        func_name: str,
        source_path: str,
        line: int,
    ) -> Optional[ir.DIValue]:
        """Attach a ``!DISubprogram`` to ``func``, creating CU / file
        lazily. Returns the DISubprogram (or None when DI is off /
        source location is unknown).

        No-op when ``debug_info_enabled`` is off or when source location
        is unknown -- emitting incomplete DI metadata is worse than
        emitting none, since external tools complain on malformed
        nodes."""
        if not (self._cg.debug_info_enabled or self._cg.profile_enabled):
            return None
        if not source_path:
            return None
        self._ensure_dwarf_module_flags()
        di_file = self._get_di_file(source_path)
        di_cu = self._get_di_compile_unit(di_file, source_path)
        sp_line = line if line > 0 else 1
        di_sp = self._cg.module.add_debug_info(
            "DISubprogram",
            {
                "name": func_name,
                "linkageName": func.name,
                "scope": di_file,
                "file": di_file,
                "line": sp_line,
                "type": self._get_di_subroutine_type(),
                "scopeLine": sp_line,
                "unit": di_cu,
                "spFlags": ir.DIToken("DISPFlagDefinition"),
            },
            is_distinct=True,
        )
        func.set_metadata("dbg", di_sp)
        return di_sp

    def _make_di_location(self, di_sp: ir.DIValue, line: int) -> ir.DIValue:
        """Build a ``!DILocation`` pointing into the given subprogram
        scope.

        LLVM's DI verifier insists every instruction in a function
        with DI carry a ``!dbg`` location once the function is
        inlinable into a DI'd caller. The IRBuilder's default location
        keeps the verifier happy; per-statement updates via
        ``di_location_for_line`` let stmt_generator attach real source
        lines so gdb/perf show line-by-line attribution."""
        return self._cg.module.add_debug_info(
            "DILocation",
            {
                "line": line if line > 0 else 1,
                "column": 0,
                "scope": di_sp,
            },
        )

    def di_location_for_line(self, line: int) -> Optional[ir.DIValue]:
        """Return a memoized ``!DILocation`` for the current function
        at ``line``. None when DI is off or no subprogram is in scope.

        Cache key is ``(function_name, line)`` so each statement-line
        in a function emits exactly one DILocation node, no matter
        how many AST instances reference that line."""
        if self._cg._current_di_sp is None:
            return None
        if line <= 0:
            return None
        func_name = (
            self._cg.current_function.name
            if self._cg.current_function is not None
            else ""
        )
        key = (func_name, line)
        cached = self._cg._di_loc_cache.get(key)
        if cached is not None:
            return cached
        loc = self._make_di_location(self._cg._current_di_sp, line)
        self._cg._di_loc_cache[key] = loc
        return loc

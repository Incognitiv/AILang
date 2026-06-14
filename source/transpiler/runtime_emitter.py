"""
RuntimeEmitter -- service that emits the C runtime-helper code.

Replaces ``_RuntimeEmitMixin``. Constructed once per compile with the
populated ``RuntimeNeeds`` and the function-signature table; ``run()``
appends every needed runtime helper's C source to the caller's output
buffer.

Phase 6 of the New Path roadmap. Pure emit pass: input
RuntimeNeeds + TypeInfo, output a list of C source lines. No analysis
state, no per-function context -- this is the single fattest "stringly
emit" pass in the C backend.

The methods here mirror the legacy ``_emit_runtime_*`` mixin methods
one-to-one with two mechanical changes: ``self.emit_raw(...)`` becomes
``self._output.append(...)``, and the ``_needs_*`` flag accesses route
through ``self._needs`` (the dataclass) instead of separate
attributes. Behavior is byte-identical to the mixin -- the corpus
diff verifies this.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Set, Tuple

from transpiler.runtime_emit_atomics import emit_runtime_atomics
from transpiler.runtime_emit_channels import emit_runtime_channels
from transpiler.runtime_emit_collections import (
    emit_runtime_dict,
    emit_runtime_dynamic_array,
    emit_runtime_str_array,
)
from transpiler.runtime_emit_fd import emit_runtime_fd
from transpiler.runtime_emit_io import emit_runtime_fileops, emit_runtime_sqlite
from transpiler.runtime_emit_math import emit_runtime_math
from transpiler.runtime_emit_process import emit_runtime_process
from transpiler.runtime_emit_safety import emit_safety_helpers
from transpiler.runtime_emit_simd import (
    emit_runtime_simd,
    emit_simd_advanced_ops,
    emit_simd_basic_ops,
    emit_simd_header,
)
from transpiler.runtime_emit_sockets import emit_runtime_sockets
from transpiler.runtime_emit_status import emit_runtime_status
from transpiler.runtime_emit_string import (
    emit_base_conversion_helpers,
    emit_parse_int_helper,
    emit_runtime_string,
    emit_split_helper,
    emit_split_ints_helper,
)
from transpiler.runtime_emit_string_aux import (
    emit_arena_helper,
    emit_dynamic_array_helpers,
    emit_file_io_helpers,
    emit_input_helper,
)
from transpiler.runtime_emit_sync import emit_runtime_sync
from transpiler.runtime_emit_syscall import emit_runtime_syscall
from transpiler.runtime_emit_system import emit_runtime_system
from transpiler.runtime_emit_threading import (
    _spawn_box_name,
    _spawn_caller_name,
    _spawn_thunk_name,
    emit_runtime_threading,
)
from transpiler.runtime_emit_threading_utils import emit_runtime_threading_utils
from transpiler.runtime_emit_time import emit_runtime_time
from transpiler.runtime_emit_win32 import emit_runtime_win32
from transpiler.runtime_needs import RuntimeNeeds


class RuntimeEmitter:
    """Emits the C source of every runtime-helper category required by
    the program. Called once per compile from the prologue-emit phase.
    """

    def __init__(
        self,
        runtime_needs: RuntimeNeeds,
        functions: Dict[str, Tuple[List[str], str]],
        user_defined_funcs: Set[str],
        ailang_type_to_c: Callable[[str], str],
        output: List[str],
    ) -> None:
        self._needs = runtime_needs
        self._functions = functions
        self._user_defined_funcs = user_defined_funcs
        self._ailang_type_to_c = ailang_type_to_c
        self._output = output

    def run(self) -> None:
        """Emit every runtime-helper category whose flag / helper key is
        set in ``self._needs``. Order mirrors the legacy ``_emit_runtime``
        in ``_EmitPrologueMixin`` for byte-identity with the
        pre-refactor output: header + safety helpers (always emitted)
        followed by per-category emitters, each gated internally on
        its own ``self._needs.helpers`` membership /
        ``self._needs.X`` flag.

        ``_emit_runtime_string`` internally dispatches to the
        ``_emit_split_*`` / ``_emit_parse_int_helper`` /
        ``_emit_file_io_helpers`` / ``_emit_arena_helper`` /
        ``_emit_input_helper`` / ``_emit_base_conversion_helpers`` /
        ``_emit_dynamic_array_helpers`` sub-helpers; they're not in
        the run-list because they're not directly gated on a top-level
        helper key."""
        self._output.append("/* Runtime helpers */")
        self._output.append("")
        self._emit_safety_helpers()
        helpers = set(self._needs.helpers)
        if not helpers:
            return
        if helpers.intersection({"time_ns", "time_ms", "rdtsc"}):
            self._emit_runtime_time()
        if helpers.intersection(
            {
                "strlen",
                "i64_decimal_len",
                "char_at",
                "unsafe_char_at",
                "int_to_str",
                "chr",
                "substr",
                "concat",
                "strcat",
                "strcat_n",
                "split",
                "split_ints",
                "str_array",
                "parse_int",
                "base_conv",
                "input",
                "file_io",
                "cmdline",
                "arena",
                "memory",
                "index_of",
                "startswith",
                "endswith",
                "str_replace",
                "print",
            }
        ):
            self._emit_runtime_string()
        if helpers.intersection({"math", "abs", "safe_div", "safe_shift"}):
            self._emit_runtime_math()
        if "simd" in helpers:
            self._emit_runtime_simd()
        if self._needs.dicts or "dict" in helpers:
            self._emit_runtime_dict()
        if self._needs.dynamic_arrays or "dynamic_array" in helpers:
            self._emit_runtime_dynamic_array()
        if helpers.intersection({"str_array", "split", "split_ints"}):
            self._emit_runtime_str_array()
        if "sqlite" in helpers:
            self._emit_runtime_sqlite()
        if "fileops" in helpers:
            self._emit_runtime_fileops()
        if "fd" in helpers:
            self._emit_runtime_fd()
        if self._needs.threading or bool(self._needs.spawn_targets):
            self._emit_runtime_threading()
        if self._needs.threading or "threading_utils" in helpers:
            self._emit_runtime_threading_utils()
        if self._needs.atomics:
            self._emit_runtime_atomics()
        if self._needs.channels:
            self._emit_runtime_channels()
        if self._needs.sync:
            self._emit_runtime_sync()
        if "system" in helpers:
            self._emit_runtime_system()
        if "syscall" in helpers:
            self._emit_runtime_syscall()
        if "process" in helpers:
            self._emit_runtime_process()
        if "status" in helpers:
            self._emit_runtime_status()
        if "sockets" in helpers:
            self._emit_runtime_sockets()
        if "win32" in helpers:
            self._emit_runtime_win32()

    def _emit_runtime_time(self) -> None:
        """Emit time-related runtime helpers."""
        emit_runtime_time(self)

    def _emit_runtime_string(self) -> None:
        emit_runtime_string(self)

    def _emit_split_ints_helper(self) -> None:
        emit_split_ints_helper(self)

    def _emit_split_helper(self) -> None:
        emit_split_helper(self)

    def _emit_parse_int_helper(self) -> None:
        emit_parse_int_helper(self)

    def _emit_file_io_helpers(self) -> None:
        emit_file_io_helpers(self)

    def _emit_arena_helper(self) -> None:
        emit_arena_helper(self)

    def _emit_input_helper(self) -> None:
        emit_input_helper(self)

    def _emit_base_conversion_helpers(self) -> None:
        emit_base_conversion_helpers(self)

    def _emit_dynamic_array_helpers(self) -> None:
        emit_dynamic_array_helpers(self)

    def _emit_runtime_math(self) -> None:
        emit_runtime_math(self)

    def _emit_safety_helpers(self) -> None:
        emit_safety_helpers(self)

    def _emit_runtime_simd(self) -> None:
        emit_runtime_simd(self)

    def _emit_simd_header(self) -> None:
        emit_simd_header(self)

    def _emit_simd_basic_ops(self) -> None:
        emit_simd_basic_ops(self)

    def _emit_simd_advanced_ops(self) -> None:
        emit_simd_advanced_ops(self)

    def _emit_runtime_dict(self) -> None:
        emit_runtime_dict(self)

    def _emit_runtime_dynamic_array(self) -> None:
        emit_runtime_dynamic_array(self)

    def _emit_runtime_str_array(self) -> None:
        emit_runtime_str_array(self)

    def _emit_runtime_sqlite(self) -> None:
        emit_runtime_sqlite(self)

    def _emit_runtime_fileops(self) -> None:
        emit_runtime_fileops(self)

    def _emit_runtime_fd(self) -> None:
        emit_runtime_fd(self)

    def _emit_runtime_threading(self) -> None:
        emit_runtime_threading(self)

    def _spawn_box_name(self, func_name: str) -> str:
        return _spawn_box_name(self, func_name)

    def _spawn_thunk_name(self, func_name: str) -> str:
        return _spawn_thunk_name(self, func_name)

    def _spawn_caller_name(self, func_name: str) -> str:
        return _spawn_caller_name(self, func_name)

    def _emit_spawn_thunks(self) -> None:
        """Emit one box+thunk+caller triple per spawn target with arguments."""
        for func_name in sorted(self._needs.spawn_targets):
            param_types = self._needs.spawn_targets[func_name]
            c_param_types = [self._ailang_type_to_c(t) for t in param_types]
            nargs = len(c_param_types)

            # Capture the user function's return so `join` gets a real
            # value back. Void-returning targets just return 0 from the
            # thunk; integer/pointer returns get cast to int64_t.
            _params, ret_type = self._functions.get(func_name, ([], "void"))
            ret_is_void = ret_type in ("void", "")

            box = self._spawn_box_name(func_name)
            thunk = self._spawn_thunk_name(func_name)
            caller = self._spawn_caller_name(func_name)

            # Box struct: holds the captured arguments by value.
            self._output.append("typedef struct {")
            for i, ct in enumerate(c_param_types):
                self._output.append(f"    {ct} a{i};")
            self._output.append(f"}} {box};")
            self._output.append("")

            # Thunk: thread entry. Unbox, call user function, free box.
            unpacked = ", ".join(f"box->a{i}" for i in range(nargs))
            self._output.append(f"static int64_t {thunk}(void *arg) {{")
            self._output.append(f"    {box} *box = ({box} *)arg;")
            if ret_is_void:
                self._output.append(f"    {func_name}({unpacked});")
                self._output.append("    free(box);")
                self._output.append("    return 0;")
            else:
                self._output.append(
                    f"    int64_t __ret = (int64_t){func_name}({unpacked});"
                )
                self._output.append("    free(box);")
                self._output.append("    return __ret;")
            self._output.append("}")
            self._output.append("")

            # Caller helper: allocate + fill box + spawn. One C expression
            # at the spawn site reduces to a clean call to this function.
            params_decl = ", ".join(f"{ct} a{i}" for i, ct in enumerate(c_param_types))
            self._output.append(f"static ailang_thread_t *{caller}({params_decl}) {{")
            self._output.append(f"    {box} *box = ({box} *)malloc(sizeof({box}));")
            self._output.append("    if (!box) return NULL;")
            for i in range(nargs):
                self._output.append(f"    box->a{i} = a{i};")
            self._output.append(
                f"    return ailang_spawn((ailang_thread_func_t){thunk}, box);"
            )
            self._output.append("}")
            self._output.append("")

    def _emit_runtime_threading_utils(self) -> None:
        """Emit threading utility builtins (thread_id, num_cpus, yield_thread, sleep_ms)."""
        emit_runtime_threading_utils(self)

    def _emit_runtime_atomics(self) -> None:
        """Emit atomic operations runtime helpers."""
        emit_runtime_atomics(self)

    def _emit_runtime_channels(self) -> None:
        emit_runtime_channels(self)

    def _emit_runtime_sync(self) -> None:
        """Emit synchronization helpers: mutex, condvar, rwlock (Ada-inspired)."""
        emit_runtime_sync(self)

    def _emit_runtime_system(self) -> None:
        """Emit system() wrapper."""
        emit_runtime_system(self)

    def _emit_runtime_syscall(self) -> None:
        """Emit syscall() wrapper."""
        emit_runtime_syscall(self)

    def _emit_runtime_process(self) -> None:
        """Emit process helper wrappers."""
        emit_runtime_process(self)

    def _emit_runtime_status(self) -> None:
        """Emit hosted status helper wrappers."""
        emit_runtime_status(self)

    def _emit_runtime_sockets(self) -> None:
        """Emit socket runtime helpers."""
        emit_runtime_sockets(self)

    def _emit_runtime_win32(self) -> None:
        """Emit Win32 dynamic-library helpers."""
        emit_runtime_win32(self)

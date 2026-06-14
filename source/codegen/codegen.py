"""
AILang Code Generator - AST to LLVM IR
Converts Abstract Syntax Tree into executable LLVM Intermediate Representation
"""

import ctypes.util
import os
from parser.ast import (
    Function,
)
from typing import Any, Optional

from llvmlite import binding, ir
from runtime.arena import ArenaGenerator
from transpiler.emit_expressions import ExprGenerator
from transpiler.emit_statements import StmtGenerator
from transpiler.range_facts import RangeFacts

from .codegen_bigint_format_mixin import _CodeGenBigIntFormatMixin
from .codegen_class_record_mixin import _CodeGenClassRecordMixin
from .codegen_errors import CodeGenError
from .codegen_function_analysis_mixin import _CodeGenFunctionAnalysisMixin
from .codegen_function_mixin import _CodeGenFunctionMixin
from .codegen_module_mixin import _CodeGenModuleMixin
from .codegen_optimizer_report_mixin import _CodeGenOptimizerReportMixin
from .codegen_support_mixin import _CodeGenSupportMixin
from .monomorphize import Monomorphizer

__all__ = ["CodeGen", "CodeGenError"]


class CodeGen(
    _CodeGenSupportMixin,
    _CodeGenModuleMixin,
    _CodeGenFunctionMixin,
    _CodeGenClassRecordMixin,
    _CodeGenBigIntFormatMixin,
    _CodeGenFunctionAnalysisMixin,
    _CodeGenOptimizerReportMixin,
):
    """
    LLVM IR Code Generator
    Converts AILang AST into LLVM IR that can be JIT-compiled or AOT-compiled
    """

    module: ir.Module
    builder: Optional[ir.IRBuilder]
    func: Optional[ir.Function]
    locals: dict[str, ir.Value]
    functions: dict[str, ir.Function]
    loop_stack: list[tuple[ir.Block, ir.Block]]
    array_metadata: dict[str, tuple[int, Any]]
    record_types: dict[str, Any]  # ir.LiteralStructType not in stubs yet
    record_fields: dict[str, list[tuple[str, Any]]]
    enum_values: dict[str, int]
    in_try_block: bool
    current_landingpad: Optional[Any]
    personality_func: Optional[ir.Function]
    monomorphizer: Monomorphizer

    def __init__(self) -> None:
        self.module = ir.Module(name="ailang")
        # Set target triple to current platform (not "unknown-unknown-unknown")
        binding.initialize_native_target()
        self.module.triple = binding.get_default_triple()  # Use proper triple string
        self.builder = None
        self.func = None
        self.locals: dict[str, Any] = {}  # Variable name -> LLVM Value
        self.local_decl_types: dict[str, Any] = {}
        self.var_signedness: dict[str, bool] = {}  # True=signed, False=unsigned
        self.value_signedness: dict[str, bool] = {}  # SSA value name -> is_signed
        self.functions: dict[str, Any] = {}
        self.function_defaults: dict[str, list[tuple[int, Any]]] = (
            {}
        )  # func -> [(idx, default), ...]
        self._function_nodes: dict[str, Function] = {}
        # Union/record field metadata, populated by _process_union_def.
        self.union_field_types: dict = {}
        self.opaque_record_names: set[str] = set()
        self.extern_record_c_names: dict[str, str] = {}
        self.extern_record_layouts: dict[str, dict[str, Any]] = {}
        # DWARF subprogram metadata for the function currently being generated;
        # picked up by the IRBuilder after entry-block creation.
        self._pending_di_sp = None
        self._pending_di_sp_line: int = 0
        # Default to interactive input; opt out with AILANG_NO_INPUT_BLOCK=1
        self.input_noninteractive = os.getenv("AILANG_NO_INPUT_BLOCK", "0") == "1"
        self.input_default = os.getenv("AILANG_INPUT_DEFAULT", "")
        # Windows ships sqlite under several names depending on toolchain
        # (sqlite3.dll, libsqlite3.dll, MSYS2's libsqlite3-0.dll). Probe the
        # common aliases so JIT mode doesn't silently fall back to fakes.
        self.sqlite_available = any(
            ctypes.util.find_library(n) is not None
            for n in ("sqlite3", "libsqlite3", "libsqlite3-0")
        )
        # Loop context stack for break/continue
        self.loop_stack = []  # Stack of (continue_block, break_block) tuples
        # Loop depth tracking for I/O optimization
        self.loop_depth: int = 0
        # Array metadata: varname -> (length, element_type)
        self.array_metadata: dict[str, tuple[int, Any]] = {}
        # Global variables registry
        self.globals: dict[str, ir.GlobalVariable] = {}  # name -> ir.GlobalVariable
        # Record type registry
        self.record_types = {}  # name -> ir.LiteralStructType
        self.record_type_ids: dict[int, str] = (
            {}
        )  # id(struct) -> name for reverse lookup
        self.record_fields = {}  # name -> [(field_name, field_type), ...]
        # Class type registry (classes are like records with methods)
        self.class_types: dict[str, Any] = {}  # name -> ir.LiteralStructType
        self.class_fields: dict[str, list[tuple[str, str, Any, Any]]] = (
            {}
        )  # name -> [(visibility, field_name, field_type, init_value), ...]
        self.class_methods: dict[str, list[Function]] = {}  # name -> [Function, ...]
        self.current_class: Optional[str] = None  # Current class being compiled
        self.current_this: Optional[ir.Value] = None  # 'this' pointer in methods
        # Parameter class types for type annotations (Option 2: explicit types)
        # Maps (function_name, param_name) -> class_type_name
        self.param_class_types: dict[tuple[str, str], str] = {}
        # Destructor tracking for RAII-style cleanup
        # Maps class name to destructor function (if defined)
        self.class_destructors: dict[str, ir.Function] = {}
        self._virtual_string_length_only_fields: set[tuple[str, str]] = set()
        self._virtual_string_elidable_params: set[tuple[str, str, int]] = set()
        self._stack_array_field_values: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._inline_this_stack_var: Optional[str] = None
        self._optimizer_decisions: list[dict[str, object]] = []
        self._optimizer_summary: dict[str, int] = {}
        # Stack of objects that need cleanup when scope exits
        # Each entry: (var_name, class_name, obj_ptr)
        self.scope_cleanup_stack: list[list[tuple[str, str, Any]]] = [[]]
        # Enum registry
        self.enum_values = {}  # "EnumName.ValueName" -> int_value
        # Data-carrying enum registry
        # Maps enum_name -> { variant_name -> [(field_name, field_type), ...] }
        self.data_enums: dict[str, dict[str, list[tuple[str, str]]]] = {}
        # Maps enum_name -> LLVM tagged union struct type
        self.data_enum_types: dict[str, ir.Type] = {}
        # Maps enum_name -> { variant_name -> tag_value }
        self.data_enum_tags: dict[str, dict[str, int]] = {}
        # Type alias registry (for Ada-style range types)
        self.type_aliases: dict[str, Any] = {}  # type_name -> RangeType or other type
        # Range-constrained variables registry
        # Maps var_name -> (low_val, high_val, exclusive)
        self.range_vars: dict[str, tuple[Any, Any, bool]] = {}
        # Monomorphizer for generic type instantiation
        self.monomorphizer = Monomorphizer()
        # Exception handling state (setjmp/longjmp-based)
        self.in_try_block = False
        self.current_landingpad = None
        self.personality_func = None
        self._exc_msg_global: Optional[ir.GlobalVariable] = None
        self._exc_type_global: Optional[ir.GlobalVariable] = None
        self._exc_handler_stack: list[ir.Block] = []
        # @synchronized mutex state (per-function, set during codegen)
        self._synchronized_mutex_ptr: Optional[Any] = None
        # Lazy-loaded external functions (only declare when needed)
        self.printf_func: Optional[ir.Function] = None
        self.strlen_func: Optional[ir.Function] = None
        self.strcat_func: Optional[ir.Function] = None
        self.strcmp_func: Optional[ir.Function] = None
        self.sprintf_func: Optional[ir.Function] = None
        self.snprintf_func: Optional[ir.Function] = None
        self.malloc_func: Optional[ir.Function] = None
        self.strcpy_func: Optional[ir.Function] = None
        self.fopen_func: Optional[ir.Function] = None
        self.fclose_func: Optional[ir.Function] = None
        self.fwrite_func: Optional[ir.Function] = None
        self.fread_func: Optional[ir.Function] = None
        self.fseek_func: Optional[ir.Function] = None
        self.ftell_func: Optional[ir.Function] = None
        self.fgets_func: Optional[ir.Function] = None
        self.fgetc_func: Optional[ir.Function] = None
        self.setvbuf_func: Optional[ir.Function] = None
        self.strncpy_func: Optional[ir.Function] = None
        self.strncmp_func: Optional[ir.Function] = None
        self.stdin_var: Optional[ir.GlobalVariable] = None
        self.memcpy_func: Optional[ir.Function] = None
        self.strstr_func: Optional[ir.Function] = None
        self.realloc_func: Optional[ir.Function] = None
        self.sqlite3_open_func: Optional[ir.Function] = None
        self.sqlite3_open_v2_func: Optional[ir.Function] = None
        self.sqlite3_close_func: Optional[ir.Function] = None
        self.sqlite3_exec_func: Optional[ir.Function] = None
        self.sqlite3_errmsg_func: Optional[ir.Function] = None
        self.sqlite3_prepare_v2_func: Optional[ir.Function] = None
        self.sqlite3_step_func: Optional[ir.Function] = None
        self.sqlite3_bind_int64_func: Optional[ir.Function] = None
        self.sqlite3_bind_text_func: Optional[ir.Function] = None
        self.sqlite3_bind_null_func: Optional[ir.Function] = None
        self.sqlite3_clear_bindings_func: Optional[ir.Function] = None
        self.sqlite3_reset_func: Optional[ir.Function] = None
        self.sqlite3_column_int64_func: Optional[ir.Function] = None
        self.sqlite3_column_text_func: Optional[ir.Function] = None
        self.sqlite3_finalize_func: Optional[ir.Function] = None
        # Dict runtime functions (generated inline)
        self.dict_create_func: Optional[ir.Function] = None
        self.dict_set_func: Optional[ir.Function] = None
        self.dict_get_func: Optional[ir.Function] = None
        self.dict_get_type_func: Optional[ir.Function] = None
        self.dict_type: Optional[ir.LiteralStructType] = None
        # Threading runtime functions (Windows: CreateThread, etc.)
        self.create_thread_func: Optional[ir.Function] = None
        self.wait_for_single_object_func: Optional[ir.Function] = None
        self.close_handle_func: Optional[ir.Function] = None
        self.get_exit_code_thread_func: Optional[ir.Function] = None
        self.exit_func: Optional[ir.Function] = None  # C exit() function
        # Threading runtime functions (POSIX: pthread_create, etc.)
        self.pthread_create_func: Optional[ir.Function] = None
        self.pthread_join_func: Optional[ir.Function] = None
        # Synchronization primitives (mutex, condvar, rwlock)
        self._mutex_funcs: dict[str, Optional[ir.Function]] = {}
        self._cond_funcs: dict[str, Optional[ir.Function]] = {}
        self._rwlock_funcs: dict[str, Optional[ir.Function]] = {}
        # Clock/timing functions (Windows: QueryPerformanceCounter, POSIX: clock_gettime)
        self.qpc_func: Optional[ir.Function] = None  # QueryPerformanceCounter
        self.qpf_func: Optional[ir.Function] = None  # QueryPerformanceFrequency
        self.clock_gettime_func: Optional[ir.Function] = None  # POSIX clock_gettime
        # Math functions (libm)
        self.exp_func: Optional[ir.Function] = None
        self.log_func: Optional[ir.Function] = None
        self.sqrt_func: Optional[ir.Function] = None
        self.sin_func: Optional[ir.Function] = None
        self.cos_func: Optional[ir.Function] = None
        self.tan_func: Optional[ir.Function] = None
        self.tanh_func: Optional[ir.Function] = None
        self.pow_func: Optional[ir.Function] = None
        self.floor_func: Optional[ir.Function] = None
        self.ceil_func: Optional[ir.Function] = None
        self.fabs_func: Optional[ir.Function] = None
        # Channel type: struct { capacity, head, tail, closed, lock, buffer_ptr }
        # This is a bounded SPMC (single-producer multiple-consumer) channel
        self.channel_type: Optional[ir.LiteralStructType] = None
        # Recursion depth tracking for stack overflow protection
        self.recursion_depth_global: Optional[ir.GlobalVariable] = None
        self.max_recursion_depth = 10000  # Configurable limit
        # Profile instrumentation (--profile). When True, generate_function
        # injects calls to __ailang_prof_enter/__ailang_prof_exit at entry
        # and at every return path. The callbacks are Python thunks
        # registered with the JIT by ailang/profiler.py via add_symbol.
        # Implies debug_info_enabled (so the profile can show file:line
        # and so LLVM doesn't reject inlined-call+DI mixing).
        self.profile_enabled: bool = False
        # DWARF emission (separate from instrumentation). AOT compiles set
        # this without setting profile_enabled - they want gdb/perf to see
        # AILang names but cannot link the Python thunks the JIT path uses.
        self.debug_info_enabled: bool = False
        self._prof_enter_func: Optional[ir.Function] = None
        self._prof_exit_func: Optional[ir.Function] = None
        # Cache one global string constant per function name so we don't
        # re-emit the same NUL-terminated literal for every return path.
        self._prof_name_consts: dict[str, ir.Value] = {}
        # AILang debug-info side-table: function name -> (source_file, line).
        # Populated as generate_function runs (whether or not --profile is on,
        # since AOT debug tooling can consume the same map; size is in the
        # noise). Profile/crash output uses this to render
        # `spread_one_step @ memory/spread.ail:120` instead of a bare name.
        self.source_map: dict[str, tuple[str, int]] = {}
        # Compile-unit source file (set by generate(); used as the fallback
        # source path for top-level functions that don't carry _source_path).
        self._compile_source_file: str = ""
        # DWARF emission state - populated lazily when --profile is on. We
        # emit DICompileUnit + per-file DIFile + per-function DISubprogram
        # so external tools (perf, gdb, llvm-symbolizer) can resolve PC
        # ranges back to AILang function names, and per-statement
        # DILocation so they resolve back to source lines too.
        self._di_module_flags_emitted: bool = False
        self._di_files: dict[str, ir.DIValue] = {}
        self._di_cus: dict[str, ir.DIValue] = {}
        self._di_subroutine_type: Optional[ir.DIValue] = None
        # Currently-being-generated function's DISubprogram (set in
        # generate_function entry, restored on exit). stmt_generator reads
        # this to attach per-statement !DILocation to the IRBuilder.
        self._current_di_sp: Optional[ir.DIValue] = None
        # Memoized DILocation per (function_name, line) so we don't emit
        # one node per statement-instance - typical AILang programs have
        # at most a few hundred unique lines per function.
        self._di_loc_cache: dict[tuple[str, int], ir.DIValue] = {}
        # Command-line argument globals (argc/argv)
        self.argc_global: Optional[ir.GlobalVariable] = None
        self.argv_global: Optional[ir.GlobalVariable] = None
        # Unchecked mode: when True, skip overflow/bounds checking for max speed
        self._unchecked_mode: bool = False
        # Fastmath mode: when True, allow less precise but faster float ops
        self._fastmath_mode: bool = False
        # Function scope marker used by proof-based arithmetic elision.
        self._current_function_name: Optional[str] = None
        self._stack_class_cleanup_plans: dict[str, Any] = {}
        self._loop_stack_class_cleanup: list[list[str]] = []
        self._recursive_functions: set[str] = set()
        self._recursion_analyzed_functions: set[str] = set()
        # Range/interval analysis facts (built once per module generate()).
        self.range_facts: RangeFacts = RangeFacts()
        # File streaming optimization - global state for cached file handle
        self._stream_file_global: Optional[ir.GlobalVariable] = None
        self._stream_path_global: Optional[ir.GlobalVariable] = None
        self._stream_write_func: Optional[ir.Function] = None
        self._stream_close_func: Optional[ir.Function] = None
        # Refactored generators
        self.expr_generator = ExprGenerator(self)
        self.stmt_generator = StmtGenerator(self)
        # Bigint (unbounded) type support
        self._bigint_type: Optional[ir.LiteralStructType] = None
        self._bigint_new_func: Optional[ir.Function] = None
        self._bigint_from_int_func: Optional[ir.Function] = None
        self._bigint_add_func: Optional[ir.Function] = None
        self._bigint_sub_func: Optional[ir.Function] = None
        self._bigint_mul_func: Optional[ir.Function] = None
        self._bigint_div_func: Optional[ir.Function] = None
        self._bigint_pow_func: Optional[ir.Function] = None
        self._bigint_cmp_func: Optional[ir.Function] = None
        self._bigint_print_func: Optional[ir.Function] = None
        self._bigint_digits_func: Optional[ir.Function] = None
        self._bigint_free_func: Optional[ir.Function] = None
        # Memory management functions
        self.free_func: Optional[ir.Function] = None
        # Template system - compiled foreign code IRs
        self.template_irs: list[str] = []
        # Arena-based string memory management.
        # Strings allocated via string_alloc() are auto-freed when the arena is
        # destroyed (i.e., when main() exits). The arena now grows on demand
        # via chunked allocation (see ailang/arena.py): when the active chunk
        # fills, arena_alloc transparently allocates a new chunk (geometric
        # sizing - each chunk roughly double the previous, with a 16 MB
        # floor) and chains it for cleanup at destroy time. There is no
        # language-imposed upper bound; total arena memory grows until the
        # program terminates or the OS denies a malloc.
        #
        # Initial chunk size is intentionally modest. Short batch programs
        # never overflow it; long-running programs (HTTP servers, REPLs,
        # soak harnesses) get extra chunks as needed. The previous design
        # picked a fixed 16 MB cap, then 1 GB after the immediate fix, both
        # of which contradicted AILang's elsewhere-stated stance against
        # artificial limits (unbound integers, arbitrary precision). The
        # chunked version honors that stance: the language doesn't decide
        # how big "big enough" is.
        #
        # User code that wants a hard cap should apply it as policy in .ail
        # See core/memory.ail for helpers that check arena_used /
        # system_ram_total and abort gracefully before exhaustion.
        self._arena_gen = ArenaGenerator(self)
        self._string_arena: Optional[ir.Value] = None  # Active string arena (i8*)
        self._module_uses_string_arena = True
        # Per-function request-arena routing slot. arena_use(handle) stores
        # the currently selected arena pointer here; string_alloc() loads it
        # on each allocation so control-flow updates remain sound.
        self._request_arena_slot: Optional[ir.Value] = None
        self._string_arena_size = 16 * 1024 * 1024  # 16 MB initial chunk
        # Track heap-allocated temporary strings for cleanup (legacy, used without arena)
        self.temp_strings: set[str] = set()
        # LLVM-side service objects. Each holds a back-reference to
        # this CodeGen and proxies attribute access through it; the
        # ``__getattr__`` below falls through to whichever service
        # defines a method that legacy callers expect to find on
        # ``self``. Methods migrated by the LLVM-pivot phases:
        # - RuntimeDecls: lazy ``get_*`` extern declarations (Phase A1)
        # - RuntimeHelpers: stream/stdin helper builders (Phase A6 slice)
        # - CollectionHelpers: channel/dict helper builders (Phase A6 slice)
        # - BuiltinMiscEmitter: fn-ptr/print/len builtins (Phase A8)
        # - BuiltinStringEmitter: scalar string builtins (Phase A8)
        # - DebugInfoEmitter: DWARF metadata (Phase A2)
        # - ProfilingEmitter: profile hooks/constants (Phase A3)
        # - BigIntRuntime: bigint type/runtime decls (Phase A4)
        # - MemoryEmitter: checked/string alloc helpers (Phase A5)
        # - SafetyEmitter: arithmetic/overflow/range/bounds checks (Phase A7)
        from codegen.bigint_runtime import BigIntRuntime
        from codegen.builtin_arrays import BuiltinArrayEmitter
        from codegen.builtin_misc import BuiltinMiscEmitter
        from codegen.builtin_string import BuiltinStringEmitter
        from codegen.collection_helpers import CollectionHelpers
        from codegen.context_emitter import ContextEmitter
        from codegen.control_flow_emitter import ControlFlowEmitter
        from codegen.debug_info import DebugInfoEmitter
        from codegen.exception_emitter import ExceptionEmitter
        from codegen.memory_emitter import MemoryEmitter
        from codegen.profiling_emitter import ProfilingEmitter
        from codegen.runtime_decls import RuntimeDecls
        from codegen.runtime_helpers import RuntimeHelpers
        from codegen.safety_emitter import SafetyEmitter
        from codegen.type_lowering import TypeLowering

        self.runtime_decls: RuntimeDecls = RuntimeDecls(self)
        self.runtime_helpers: RuntimeHelpers = RuntimeHelpers(self)
        self.collection_helpers: CollectionHelpers = CollectionHelpers(self)
        self.builtin_misc: BuiltinMiscEmitter = BuiltinMiscEmitter(self)
        self.builtin_arrays: BuiltinArrayEmitter = BuiltinArrayEmitter(self)
        self.builtin_string: BuiltinStringEmitter = BuiltinStringEmitter(self)
        self.debug_info: DebugInfoEmitter = DebugInfoEmitter(self)
        self.profiling: ProfilingEmitter = ProfilingEmitter(self)
        self.control_flow: ControlFlowEmitter = ControlFlowEmitter(self)
        self.exception_emitter: ExceptionEmitter = ExceptionEmitter(self)
        self.bigint_runtime: BigIntRuntime = BigIntRuntime(self)
        self.memory_emitter: MemoryEmitter = MemoryEmitter(self)
        self.safety: SafetyEmitter = SafetyEmitter(self)
        self.type_lowering: TypeLowering = TypeLowering(self)
        self.context_emitter: ContextEmitter = ContextEmitter(self)

    def __getattr__(self, name: str) -> Any:
        """Fall through to the LLVM-side services so legacy
        ``self.get_X()`` / ``self._get_di_X()`` / ``self.emit_dwarf_X()``
        callers keep working after methods migrate out. Only
        triggered when the attribute isn't on this instance directly.
        ``vars(type(svc))`` check prevents infinite recursion through
        the service's own ``__getattr__``."""
        services = (
            self.__dict__.get("runtime_decls"),
            self.__dict__.get("runtime_helpers"),
            self.__dict__.get("collection_helpers"),
            self.__dict__.get("builtin_misc"),
            self.__dict__.get("builtin_arrays"),
            self.__dict__.get("builtin_string"),
            self.__dict__.get("debug_info"),
            self.__dict__.get("context_emitter"),
            self.__dict__.get("control_flow"),
            self.__dict__.get("exception_emitter"),
            self.__dict__.get("profiling"),
            self.__dict__.get("bigint_runtime"),
            self.__dict__.get("memory_emitter"),
            self.__dict__.get("safety"),
            self.__dict__.get("type_lowering"),
        )
        for svc in services:
            if svc is None:
                continue
            if any(name in cls.__dict__ for cls in type(svc).mro()):
                return getattr(svc, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    # ``get_printf`` / ``_declare_external`` / ``get_strlen`` /
    # ``get_strcmp`` / ``get_sprintf`` / ``get_snprintf`` /
    # ``get_malloc`` moved to ``codegen.runtime_decls.RuntimeDecls``.
    # Legacy ``self.get_X()`` callers keep working via the
    # ``__getattr__`` fallback above.
    # ``checked_malloc`` / ``_checked_malloc_with_builder`` /
    # ``string_alloc`` moved to ``codegen.memory_emitter.MemoryEmitter``.
    # ``get_strcpy`` / ``get_strcat`` / ``get_strdup`` moved to
    # ``codegen.runtime_decls.RuntimeDecls``.
    # ``_get_prof_enter_func`` / ``_get_prof_exit_func`` /
    # ``_get_prof_name_const`` / ``_is_profile_skipped`` /
    # ``emit_profile_enter`` / ``emit_profile_exit`` moved to
    # ``codegen.profiling_emitter.ProfilingEmitter``.
    # ``_ensure_dwarf_module_flags`` / ``_get_di_file`` /
    # ``_get_di_compile_unit`` / ``_get_di_subroutine_type`` /
    # ``emit_dwarf_subprogram`` / ``_make_di_location`` /
    # ``di_location_for_line`` moved to ``codegen.debug_info.DebugInfoEmitter``.
    # ``get_fopen`` / ``get_fclose`` / ``get_fwrite`` / ``get_setvbuf``
    # moved to ``codegen.runtime_decls.RuntimeDecls``.
    # ``get_strncpy`` moved to ``codegen.runtime_decls.RuntimeDecls``.
    # ``get_stream_file_global`` / ``get_stream_path_global`` /
    # ``get_stream_write_func`` / ``get_stream_close_func`` moved to
    # ``codegen.runtime_helpers.RuntimeHelpers``.
    # ``get_fread`` / ``get_fseek`` / ``get_ftell`` / ``get_fgets``
    # moved to ``codegen.runtime_decls.RuntimeDecls``.
    # ``get_memcpy`` / ``get_realloc`` / ``get_strstr`` / ``get_strncmp``
    # moved to ``codegen.runtime_decls.RuntimeDecls``.
    # ------------------------------------------------------------------
    # Math functions (libm) - hardware-accelerated
    # ------------------------------------------------------------------
    # Math runtime declarations (get_exp / get_log / get_sqrt /
    # get_sin / get_cos / get_tan / get_tanh / get_pow / get_floor /
    # get_ceil / get_fabs) moved to ``codegen.runtime_decls.RuntimeDecls``.
    # Legacy ``self.get_exp()`` etc. callers keep working via the
    # ``__getattr__`` fallback wired up in ``__init__``.
    # ``_get_bigint_type`` / ``_get_bigint_new`` / ``_get_bigint_from_int`` /
    # ``_get_bigint_add`` / ``_get_bigint_sub`` / ``_get_bigint_mul`` /
    # ``_get_bigint_div`` / ``_get_bigint_pow`` / ``_get_bigint_cmp`` /
    # ``_get_bigint_print`` / ``_get_bigint_digits`` / ``_get_bigint_free`` /
    # ``is_bigint_type`` moved to ``codegen.bigint_runtime.BigIntRuntime``.
    # ------------------------------------------------------------------
    # Threading runtime functions (cross-platform)
    # ------------------------------------------------------------------
    # ``get_pthread_create`` / ``get_pthread_join`` /
    # ``get_create_thread`` / ``get_wait_for_single_object`` /
    # ``get_close_handle`` / ``get_exit_code_thread`` moved to
    # ``codegen.runtime_decls.RuntimeDecls``.
    # ``_get_or_declare_exit`` moved to ``codegen.runtime_decls.RuntimeDecls``.
    # ``_emit_safety_trap`` / ``_ensure_exc_globals`` moved to
    # ``codegen.exception_emitter.ExceptionEmitter``.
    # ------------------------------------------------------------------
    # Synchronization primitives (Ada/SPARK-inspired)
    # ------------------------------------------------------------------
    # ``get_mutex_func`` moved to ``codegen.runtime_decls.RuntimeDecls``.
    # Recursion + synchronized methods moved to `codegen.control_flow_emitter.ControlFlowEmitter`.
    # ------------------------------------------------------------------
    # Channel type and operations
    # ------------------------------------------------------------------
    # ``get_channel_type`` / ``get_dict_type`` / ``get_dict_create_func`` /
    # ``get_dict_set_func`` / ``get_dict_get_type_func`` /
    # ``get_dict_get_func`` / ``get_dict_has_key_func`` /
    # ``get_dict_size_func`` / ``get_dict_key_at_func`` /
    # ``get_dict_value_at_func`` / ``get_dict_remove_func`` moved to
    # ``codegen.collection_helpers.CollectionHelpers``.
    # Type tag constants for dict values (kept on CodeGen for compatibility).
    DICT_TYPE_INT = 0
    DICT_TYPE_FLOAT = 1
    DICT_TYPE_STRING = 2
    DICT_TYPE_BOOL = 3
    DICT_TYPE_POINTER = 4

    # ``get_stdin`` moved to ``codegen.runtime_helpers.RuntimeHelpers``.
    # All ``get_sqlite3_*`` methods (open/close/exec/errmsg/prepare_v2/
    # step/column_int64/column_text/finalize) moved to
    # ``codegen.runtime_decls.RuntimeDecls``.
    # ``current_builder`` / ``current_function`` / ``alloca_in_entry_block`` /
    # ``push_scope`` / ``pop_scope`` / ``register_for_cleanup`` /
    # ``cleanup_all_scopes`` moved to ``codegen.context_emitter.ContextEmitter``.

    # ========================================================================
    # Top-level generation
    # ========================================================================

    # ========================================================================
    # Statement and Expression Generation (Delegation)
    # ========================================================================

    # Value helpers moved to ``codegen.safety_emitter``.
    # --------------------------------------------------------------------
    # String built-ins
    # --------------------------------------------------------------------
    # ``builtin_char_at`` / ``builtin_unsafe_char_at`` / ``builtin_index_of`` /
    # ``builtin_substr`` / ``builtin_concat`` / ``builtin_ord`` /
    # ``builtin_chr`` / ``builtin_strlen`` / ``builtin_str`` /
    # ``builtin_startswith`` / ``builtin_endswith`` / ``builtin_str_replace``
    # moved to ``codegen.builtin_string.BuiltinStringEmitter``.

    # ``_array_header_ptr`` / ``builtin_array_*`` / ``_str_array_header_ptr`` /
    # ``builtin_str_array_*`` moved to ``codegen.builtin_arrays.BuiltinArrayEmitter``.
    # Legacy ``self.builtin_array_*`` and ``self.builtin_str_array_*`` callers keep working
    # via the ``__getattr__`` fallback above.
    # ``builtin_fn_ptr`` / ``builtin_fn_call`` / ``builtin_fn_call_str`` /
    # ``builtin_putc`` / ``builtin_print`` / ``_format_print_value`` /
    # ``_format_int_for_print`` / ``_is_string_pointer`` / ``builtin_len``
    # moved to ``codegen.builtin_misc.BuiltinMiscEmitter``.
    # Exception helpers moved to `codegen.exception_emitter.ExceptionEmitter`.

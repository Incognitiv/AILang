"""
AILang to C Transpiler
Transpiles .ail files to pure C code that can be compiled with GCC/Clang.
This produces standalone C programs with no Python runtime dependency.
The generated C code:
- Targets C23 (ISO/IEC 9899:2024) standard
- Compiles with -Wall -Wextra -Werror -pedantic -std=c23
- Uses C23 features: nullptr for null pointer constants
- Safe array operations with bounds checking (ailang_safe_array_get/set)
- Safe integer operations with overflow detection (ailang_safe_add/mul)
- Cross-platform (Windows/Linux/macOS/BSD/Freestanding)
Usage:
    python -m transpiler.core input.ail -o output.c
    gcc -O3 -Wall -Wextra -std=c23 output.c -o output
"""

import sys
from parser.parser import Parser
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from lexer.scan import tokenize
from transpiler.expr_gen import CExprEmitter
from transpiler.stmt_visit import CStmtEmitter

from .core_cleanup_reports import _CTranspilerCleanupReportMixin
from .core_emit_driver import _CTranspilerEmitDriverMixin
from .core_optimizer_reports import _CTranspilerOptimizerReportMixin
from .core_state_aliases import _CTranspilerStateAliasMixin
from .core_type_lowering import _CTranspilerTypeLoweringMixin
from .core_type_state import _CTranspilerTypeStateMixin

# Type aliases for clarity
ClassField = Tuple[str, str, str]  # (visibility, field_name, field_type)
RecordField = Tuple[str, str]  # (field_name, field_type)


class CTranspiler(
    _CTranspilerStateAliasMixin,
    _CTranspilerTypeStateMixin,
    _CTranspilerCleanupReportMixin,
    _CTranspilerOptimizerReportMixin,
    _CTranspilerTypeLoweringMixin,
    _CTranspilerEmitDriverMixin,
):
    """Transpiles AILang AST to C code.
    Pure orchestrator now -- no more mixin inheritance. Holds the
    per-compile state (output buffer, type_info, runtime_needs,
    ownership analyzer, per-function emit state) and delegates the
    actual visit / expr walks to ``CStmtEmitter`` and ``CExprEmitter``
    instances. The two emitters proxy attribute access back to this
    class via ``__getattr__`` / ``__setattr__`` so legacy
    ``self.<state>`` access patterns in their methods keep working
    unchanged.
    """

    # C keywords and standard library names that conflict with generated C.
    # AILang keeps these usable as identifiers where the grammar allows it;
    # the C backend gives them safe storage names instead.
    C_RESERVED_NAMES: ClassVar[Set[str]] = {
        "alignas",
        "alignof",
        "auto",
        "bool",
        "break",
        "case",
        "char",
        "const",
        "constexpr",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extern",
        "false",
        "float",
        "for",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "nullptr",
        "register",
        "restrict",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "struct",
        "switch",
        "thread_local",
        "true",
        "typedef",
        "typeof",
        "typeof_unqual",
        "union",
        "unsigned",
        "void",
        "volatile",
        "while",
        "abs",
        "labs",
        "llabs",
        "div",
        "ldiv",
        "lldiv",
        "max",
        "min",
        "malloc",
        "free",
        "realloc",
        "calloc",
        "exit",
        "abort",
        "atexit",
        "atoi",
        "atol",
        "atoll",
        "rand",
        "srand",
        "time",
        "clock",
        "strlen",
        "strcpy",
        "strcat",
        "strcmp",
        "memcpy",
        "memset",
        "memmove",
        "printf",
        "scanf",
        "sprintf",
        "sscanf",
        "fprintf",
        "puts",
        "gets",
        "getchar",
        "putchar",
        "fopen",
        "fclose",
        "sqrt",
        "pow",
        "sin",
        "cos",
        "tan",
        "log",
        "exp",
        "floor",
        "ceil",
        "round",
        "fabs",
    }

    def __init__(self) -> None:
        self.output: List[str] = []
        self.indent: int = 0
        self.declared_vars: Set[str] = set()
        self._function_c_symbols: Dict[str, str] = {}
        # --profile: when set, the C backend instruments every user
        # function with rdtsc-based entry/exit hooks and emits an
        # atexit report ranking functions by accumulated cycles.
        # Off by default — adds a few ns/call which is negligible for
        # finding the hot path but unwanted in production.
        self.profile_enabled: bool = False
        # Per-function index assigned at function emission. Used by
        # the profile hooks to pick which counter slot to bump.
        self._profile_func_index: Dict[str, int] = {}
        # ``type_info`` is the new explicit container for static type
        # information (records, unions, enums, classes, function
        # signatures, plus per-function variable typing). Legacy
        # attributes (``self.records``, ``self.classes``, ``self._var_types``,
        # ``self._string_vars``, etc.) are property aliases routed
        # through it. Reassigning ``self.type_info`` swaps every alias
        # at once, the same trick that worked for ``runtime_needs``.
        from transpiler.ownership_analyzer import OwnershipAnalyzer
        from transpiler.range_facts import RangeFacts
        from transpiler.type_info import TypeInfo

        self.type_info: TypeInfo = TypeInfo()
        self.range_facts: RangeFacts = RangeFacts()
        # OwnershipAnalyzer is constructed once per compile, queried
        # per-function during emit. Carries the type tables + dispatch
        # sets it consults; pure-functional otherwise.
        self.ownership: OwnershipAnalyzer = OwnershipAnalyzer(
            self.type_info,
            self._STRING_OWNING_CALLS,
            self._NON_CAPTURING_CALLS,
        )
        self.current_function: Optional[str] = None
        self.user_defined_funcs: Set[str] = set()  # Track user-defined functions
        self._source_file: str = ""  # Source file for import resolution
        # Track which runtime helpers are actually used. ``runtime_needs``
        # is the new explicit data container; ``used_helpers``,
        # ``_spawn_targets``, and the ``_needs_*`` properties below all
        # route through it so legacy reads / writes work unchanged during
        # the gradual mixin -> service migration. After scan we may
        # reassign ``self.runtime_needs`` (e.g. to swap in the
        # HelperScanner's output) without breaking those legacy names --
        # the properties always re-read the current container.
        from transpiler.runtime_needs import RuntimeNeeds

        self.runtime_needs: RuntimeNeeds = RuntimeNeeds()
        # Per-function dict-typed locals that need cleanup at scope exit;
        # populated by visit_Function for each function it processes.
        self._dict_locals_for_cleanup: List[str] = []
        # Variable typing fields (``_dict_vars`` etc.) and ``classes`` now
        # live in ``self.type_info``; properties at class scope route
        # legacy attribute access through it.
        self._current_class: Optional[str] = None  # Track current class for 'this'
        # Recursion-depth guard: True only when the current function is in
        # a call cycle. ``_recursive_funcs`` lives in self.type_info now;
        # ``_guard_active`` is per-function emit state.
        self._guard_active: bool = False
        # Class auto-cleanup: per-function list of (var_name, class_name)
        # pairs that should be freed at scope exit. Set per-function in
        # visit_Function / _generate_class_method, consumed by visit_Return
        # and the implicit-return paths.
        self._class_locals_for_cleanup: List[Tuple[str, str]] = []
        self._string_locals_for_cleanup: List[str] = []
        # StringArray and IntArray locals from split() / split_ints().
        # Same liveness discipline: track non-escaping ones, free at exit.
        self._str_array_locals_for_cleanup: List[str] = []
        self._int_array_locals_for_cleanup: List[str] = []
        # ailang_dyn_array (from array_new) and ailang_str_array
        # (from str_array_new) — track for scope-exit cleanup of the
        # backing heap buffer.
        self._dyn_array_locals_for_cleanup: List[str] = []
        self._lc_str_array_locals_for_cleanup: List[str] = []
        # Tracked owned locals — used for FREE-BEFORE-REASSIGN. A var is
        # tracked when EVERY assignment to it is an owned allocation
        # (`new ClassName(...)` for classes, an owned-string-alloc for
        # strings). On reassignment we free the previous value to avoid
        # the loop-overwrite leak. Non-escaping ones additionally get
        # final-free at scope exit (separate cleanup_for lists above).
        self._tracked_owned_string_locals: Set[str] = set()
        self._tracked_owned_class_locals: Dict[str, str] = {}
        self._stack_owned_class_locals: Dict[str, str] = {}
        self._stack_array_field_values: Dict[Tuple[str, str], Tuple[str, ...]] = {}
        self._inline_this_expr: Optional[str] = None
        self._inline_this_stack_var: Optional[str] = None
        self._owned_value_local_kinds: Dict[str, Tuple[str, Any]] = {}
        # Owned-string locals that are read EXACTLY ONCE in the function
        # body. These are eligible to be CONSUMED by the next operation:
        # in `d = c + str(42)`, if c is single-use and owned, we can pass
        # it to consuming-strcat with free_a=1 instead of leaking it.
        # Without this the value is marked escaping (the read in d's RHS)
        # and never freed. Populated per-function in visit_Function.
        # _single_use_owned_strings now lives in self.type_info (set
        # per-function during VarTypingScanner / ownership analysis).
        # Mixed-ownership string locals: assigned BOTH owned-allocs and
        # non-owning values across different statements/branches. Each
        # gets a runtime `__var_owned` flag — set/cleared per assign,
        # conditional free at scope exit and before reassignment. Lets
        # us track patterns like `s = ""; if cond: s = "x" + str(y)`
        # without leaking the owned alloc on the cond-true path.
        self._mixed_ownership_string_locals: Set[str] = set()
        self._owned_string_param_flags: Dict[str, str] = {}
        self._owned_param_flags: Dict[str, Tuple[str, str, Any]] = {}
        self._virtual_string_length_only_fields: Set[Tuple[str, str]] = set()
        self._virtual_string_elidable_params: Set[Tuple[str, str, int]] = set()
        # Order-preserving list for the cleanup emitter so the per-
        # function `__var_owned = 0;` initializers can be issued at
        # function scope alongside the variable's NULL init.
        self._mixed_ownership_cleanup: List[str] = []
        # Loop depth tracking for I/O optimization
        self._loop_depth: int = 0
        self._bound_counter: int = 0  # Unique counter for bounded loop variables
        self._current_bound_var: str = ""  # Track current bound var for do-while
        # Unchecked mode: skip overflow/bounds checks for max performance
        self._unchecked_mode: bool = False
        self._scanning_unchecked: bool = False  # Track during helper scanning
        # @synchronized decorator state (per-function mutex name)
        self._synchronized_mutex_name: Optional[str] = None
        # Track current function's C return type for visit_Return
        self._current_ret_type: str = "int64_t"
        # Range type tracking for Ada-style range constraints
        self._range_vars: Dict[str, tuple] = {}
        self._type_aliases: Dict[str, Any] = {}  # A.RangeType
        # Generics support
        from codegen.monomorphize import Monomorphizer

        self._monomorphizer = Monomorphizer()
        self._generic_funcs_emitted: Set[str] = set()  # Track emitted specializations
        # Global name tracking (populated in transpile())
        self._globally_used_names: set[str] = set()
        self._const_global_names: set[str] = set()
        self._static_global_names: set[str] = set()
        # Check-elision audit trail (populated during expression emission).
        self._check_decisions: list[dict[str, object]] = []
        self._check_summary: dict[str, int] = {}
        # Formatting-specialization audit trail (P11): records per-site
        # direct writer/specialized vs generic formatter fallback decisions.
        self._format_decisions: list[dict[str, object]] = []
        self._format_summary: dict[str, int] = {}
        # Optimizer audit trail: records materialization, scalarization, and
        # inlining decisions made before C emission.
        self._optimizer_decisions: list[dict[str, object]] = []
        self._optimizer_summary: dict[str, int] = {}
        # Compile-time array length hints for bounds-proof hooks.
        self._array_len_hints: dict[tuple[Optional[str], str], int] = {}
        # Lazy-initialized collections (populated by collection passes)
        self.extern_vars: Dict[str, str] = {}
        # ``unions`` and ``_type_decorators`` are property aliases of
        # ``self.type_info.unions`` / ``self.type_info.type_decorators``.
        # Statement + expression emit services. Each is a proxy class
        # that holds a back-reference to ``self`` so its visit_* /
        # _expr_* methods can read and write per-function emit state on
        # this orchestrator. CTranspiler delegates ``visit()`` and
        # ``expr()`` to them; cross-emitter calls (e.g. stmt code calling
        # ``self._expr_method_call``) resolve via the ``__getattr__``
        # fallback below.
        self.stmt_emitter: CStmtEmitter = CStmtEmitter(self)
        self.expr_emitter: CExprEmitter = CExprEmitter(self)

    def __getattr__(self, name: str):
        # Called only when the attribute isn't on this instance. Fall
        # through to whichever emitter defines it (matters for the
        # stmt/expr cross-calls left over from the mixin refactor:
        # stmt code calling ``self._expr_X``, expr code calling
        # ``self.visit_Function``, etc.). ``vars(type(emitter))``
        # checks the method is actually defined on the emitter class
        # rather than triggering its ``__getattr__`` proxy back to
        # this object -- which would infinite-loop.
        emitters = (
            self.__dict__.get("stmt_emitter"),
            self.__dict__.get("expr_emitter"),
        )
        for emitter in emitters:
            if emitter is not None and name in vars(type(emitter)):
                return getattr(emitter, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    # ==================== owned-call dispatch tables ====================
    #
    # Static metadata about which builtins allocate / capture / return
    # strings. Owned-call analysis (the C-backend cleanup story) reads
    # these from CTranspiler today; in Phase 5 they will move into
    # ``OwnershipAnalyzer`` and be removed from this class.
    # Builtins whose AILang return type is a string. Locals taking their
    # result need ``const char *`` declarations rather than int64_t.
    _STR_RETURNING_BUILTINS: ClassVar["frozenset[str]"] = frozenset(
        {
            "str_array_get",
            "split_str_get",
            "dict_key_at",
            "dict_get_string",
            "str_array_pop",
            "str_array_join",
            "fn_call_str",
            "tcp_recv",
            "win32_full_path",
            "current_dir",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "argv",
            "target_os",
            "target_backend",
            "read_stdin",
        }
    )
    # Builtins that always allocate a fresh heap string. Assigning their
    # result to a local makes that var an "owned string" — eligible for
    # auto-free at scope exit if it doesn't escape.
    _STRING_OWNING_CALLS: ClassVar["frozenset[str]"] = frozenset(
        {
            "str",
            "chr",
            "substr",
            "concat",
            "str_replace",
            "hex",
            "bin",
            "oct",
            "tcp_recv",
            "win32_full_path",
            "input",
            "read_stdin",
            "read_file",
            "current_dir",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "ailang_strcat",
            "fn_call_str",
        }
    )
    # Builtins that READ a string argument transiently and don't
    # capture it. Passing an owned string into one of these does NOT
    # mark it as escaped, which lets the cleanup pass auto-free
    # strings that are only read (strlen, comparisons, output writes...).
    _NON_CAPTURING_CALLS: ClassVar["frozenset[str]"] = frozenset(
        {
            "strlen",
            "len",
            "ord",
            "char_at",
            "unsafe_char_at",
            "index_of",
            "index_of_from",
            "startswith",
            "endswith",
            "parse_int",
            "parse_float",
            "print",
            "println",
            "tcp_send",
            "write_file",
            "write_bytes",
            "win32_full_path",
            "file_exists",
            "file_can_execute",
            "file_is_regular",
            "file_is_symlink",
            "file_is_block",
            "file_is_char",
            "file_is_fifo",
            "file_is_socket",
            "file_is_setuid",
            "file_is_setgid",
            "file_mtime",
            "file_same",
            "fd_is_tty",
            "access",
            "make_dir",
            "delete_file",
            "move_file",
            "fd_open",
            "fd_read",
            "fd_write",
            "fd_close",
            "fd_dup",
            "fd_dup2",
            "fd_tell",
            "fd_seek",
            "fd_flush",
            "system",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "list_dir",
            "split_len",
            "split_get",
            "split_str_get",
            "split_set",
            "str_array_len",
            "str_array_get",
            "str_array_join",
            "str_array_pop",
            "array_len",
            "array_cap",
            "array_get",
            "array_pop",
            "dict_size",
            "dict_key_at",
            "dict_value_at",
            "dict_get_string",
            "dict_get",
            "dict_has",
            "dict_has_key",
            "dict_get_type",
            "dict_remove",
            "sql_exec",
            "sql_prepare",
            "sql_step",
            "sql_bind_int",
            "sql_bind_text",
            "sql_bind_text_i64",
            "sql_bind_text_i64_parts",
            "sql_bind_null",
            "sql_clear_bindings",
            "sql_finalize",
            "sql_reset",
            "sql_column_int",
            "sql_column_text",
            "sql_close",
            "substr",
            "str_replace",
            "concat",
            "split",
            "split_ints",
        }
    )


def transpile_file(
    input_path: str,
    output_path: Optional[str] = None,
    profile_enabled: bool = False,
) -> str:
    """Transpile an AILang file to C.
    `profile_enabled` (driven by the CLI's `--profile` flag) emits
    rdtsc-based per-function entry/exit timing and an atexit report.
    """
    from runtime.phases import Phase

    with Phase("c_backend.read_source"):
        with open(input_path, "r", encoding="utf-8") as f:
            source = f.read()
    with Phase("c_backend.lex"):
        tokens = tokenize(source)
    with Phase("c_backend.parse"):
        p = Parser(tokens)
        ast = p.parse_program()
    with Phase("c_backend.transpile"):
        transpiler = CTranspiler()
        transpiler.profile_enabled = profile_enabled
        c_code = transpiler.transpile(ast, input_path)
    if output_path:
        with Phase("c_backend.write_output"):
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(c_code)
    return c_code


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m transpiler.core input.ail [-o output.c]")
        sys.exit(1)
    input_path = sys.argv[1]
    output_path = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            output_path = sys.argv[idx + 1]
    if not output_path:
        output_path = str(Path(input_path).with_suffix(".c"))
    c_code = transpile_file(input_path, output_path)
    print(f"Transpiled {input_path} -> {output_path}")
    lines = c_code.split("\n")
    if len(lines) > 50:
        print("\n--- Preview (first 50 lines) ---")
        print("\n".join(lines[:50]))
        print(f"... ({len(lines) - 50} more lines)")
    else:
        print("\n--- Generated C code ---")
        print(c_code)


if __name__ == "__main__":
    main()

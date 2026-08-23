"""
AILang Static Analysis - Null Flow Analysis & Data Race Detection

Compile-time warnings for:
1. Potential null dereferences (variable may be null when accessed)
2. Data races: unsynchronized access to shared variables across concurrent contexts
3. Write-write conflicts in parallel code
4. Read-write conflicts without atomic operations

These are WARNINGS, not errors - they don't block compilation.
"""

from parser import ast as A
from typing import ClassVar, Optional

from ast_access import arg_at
from diagnostics.static_analysis_class_cleanup import (
    _check_class_cleanup_contract as _m_check_class_cleanup_contract,
)
from diagnostics.static_analysis_class_cleanup import (
    _extract_class_field_spec as _m_extract_class_field_spec,
)
from diagnostics.static_analysis_class_cleanup import (
    _field_type_to_text as _m_field_type_to_text,
)
from diagnostics.static_analysis_class_cleanup import (
    _looks_owned_resource_type as _m_looks_owned_resource_type,
)
from diagnostics.static_analysis_models import (
    AnalysisWarning,
    FunctionContext,
    NullState,
    VariableAccess,
)
from diagnostics.static_analysis_perf import check_string_concat_loops


class StaticAnalyzer:
    """
    Performs compile-time static analysis on AILang AST.

    Detects:
    1. Potential null dereferences
    2. Data races in concurrent code (spawn, parallel_map, async)
    3. Write-write conflicts
    4. Read-write conflicts without synchronization
    """

    # Atomic operations that are safe for concurrent access
    ATOMIC_OPS: ClassVar[set[str]] = {
        "atomic_load",
        "atomic_store",
        "atomic_add",
        "atomic_sub",
        "atomic_cas",
        "atomic_exchange",
        "atomic_fetch_add",
        "atomic_fetch_sub",
        "atomic_fetch_and",
        "atomic_fetch_or",
        "atomic_fetch_xor",
    }

    # Channel operations that safely transfer ownership
    CHANNEL_OPS: ClassVar[set[str]] = {
        "channel_send",
        "channel_recv",
        "chan_send",
        "chan_recv",
        "send",
        "recv",
    }

    def __init__(self) -> None:
        self.warnings: list[AnalysisWarning] = []
        # Global variable names (declared at file scope)
        self.global_vars: set[str] = set()
        # Global variables that are written to
        self.global_writes: set[str] = set()
        # Variables explicitly marked as shared (future: 'shared' keyword)
        self.shared_vars: set[str] = set()
        # Functions and their contexts
        self.functions: dict[str, FunctionContext] = {}
        # Current function being analyzed
        self.current_func: Optional[FunctionContext] = None
        # Functions called via spawn
        self.spawned_functions: set[str] = set()
        # Functions called via parallel_map
        self.parallel_functions: set[str] = set()
        # All variable accesses for race detection
        self.all_accesses: list[VariableAccess] = []
        # Type-level names (enums, records, classes) — never null
        self.type_names: set[str] = set()

    def analyze(self, ast_nodes: list[A.ASTNode]) -> list[AnalysisWarning]:
        """Analyze the full AST and return warnings."""
        self.warnings = []

        # First pass: collect global variables, functions, and spawn calls
        for node in ast_nodes:
            self._collect_globals_and_functions(node)

        # Class lifecycle contract check: class auto-cleanup is shallow unless
        # user defines `~ClassName`. Warn on likely-owned fields with no dtor.
        self._check_class_cleanup_contract(ast_nodes)

        # Second pass: analyze each function
        for node in ast_nodes:
            self._analyze_node(node)

        # Third pass: check for shared variable issues
        self._check_shared_variables()

        # Fourth pass: flag O(n^2) string concatenation in loops.
        # `s = s + "x"` inside a `while`/`for`/`foreach`/`loop`/`repeat`
        # body is the most common AILang performance footgun -- each
        # iteration mallocs a new string and copies the entire previous
        # one. See perf_jit_driver/microbench/string_concat_loop.* for
        # the empirical 1500ms-vs-28ms demo. Suggest str_array_join.
        for node in ast_nodes:
            check_string_concat_loops(self, node, in_loop=False)

        return self.warnings

    def _check_class_cleanup_contract(self, ast_nodes: list[A.ASTNode]) -> None:
        _m_check_class_cleanup_contract(self, ast_nodes)

    def _extract_class_field_spec(self, field: object) -> tuple[str, str]:
        return _m_extract_class_field_spec(self, field)

    def _field_type_to_text(self, raw_type: object) -> str:
        return _m_field_type_to_text(self, raw_type)

    def _looks_owned_resource_type(self, field_type: str) -> bool:
        return _m_looks_owned_resource_type(self, field_type)

    def _collect_globals_and_functions(self, node: A.ASTNode) -> None:
        """First pass: collect global variables, functions, and spawn calls."""
        if node is None:
            return

        # Collect global variable declarations
        if isinstance(node, A.VarDecl):
            self.global_vars.add(node.var_name)
            return

        # Collect type definitions (enums, records, classes) — these are
        # type-level names, never null, used for static field access
        if isinstance(node, A.EnumDef):
            self.type_names.add(node.name)
            return
        if isinstance(node, A.RecordDef):
            self.type_names.add(node.name)
            return

        # Collect functions
        if isinstance(node, A.Function):
            ctx = FunctionContext(name=node.name)
            self.functions[node.name] = ctx
            # Scan body for spawn calls
            for stmt in node.body:
                self._find_spawn_calls(stmt)

        elif isinstance(node, A.ClassDef):
            self.type_names.add(node.name)
            for method in node.methods:
                self._collect_globals_and_functions(method)

    def _collect_functions(self, node: A.ASTNode) -> None:
        """First pass: collect function definitions and find spawn calls."""
        if node is None:
            return

        if isinstance(node, A.Function):
            ctx = FunctionContext(name=node.name)
            self.functions[node.name] = ctx
            # Scan body for spawn calls
            for stmt in node.body:
                self._find_spawn_calls(stmt)

        elif isinstance(node, A.ClassDef):
            for method in node.methods:
                self._collect_functions(method)

    def _find_spawn_calls(self, node: A.ASTNode) -> None:
        """Find spawn/parallel_map/async calls to mark functions as concurrent."""
        if node is None:
            return

        # spawn func() - mark func as spawned
        if isinstance(node, A.Spawn) and isinstance(node.func_call, A.Call):
            self.spawned_functions.add(node.func_call.name)
            if node.func_call.name in self.functions:
                self.functions[node.func_call.name].is_spawned = True

        # Check for parallel_map(items, func) calls
        if isinstance(node, A.Call) and (
            node.name == "parallel_map" and len(node.args) >= 2
        ):
            func_arg = arg_at(node, 1)
            if isinstance(func_arg, A.Variable):
                func_name = func_arg.name
                self.parallel_functions.add(func_name)
                if func_name in self.functions:
                    self.functions[func_name].is_parallel = True

        # Recurse into children
        self._recurse_find_spawn(node)

    def _recurse_find_spawn(self, node: A.ASTNode) -> None:
        """Recursively search for spawn calls."""
        if node is None:
            return

        if isinstance(node, A.If):
            self._find_spawn_calls(node.cond)
            for stmt in node.then_body:
                self._find_spawn_calls(stmt)
            if node.else_body:
                for stmt in node.else_body:
                    self._find_spawn_calls(stmt)
        elif isinstance(node, A.While):
            self._find_spawn_calls(node.cond)
            for stmt in node.body:
                self._find_spawn_calls(stmt)
        elif isinstance(node, (A.For, A.Foreach)):
            for stmt in node.body:
                self._find_spawn_calls(stmt)
        elif isinstance(node, A.VarDecl):
            if node.init_value:
                self._find_spawn_calls(node.init_value)
        elif isinstance(node, A.Assign):
            self._find_spawn_calls(node.value)
        elif isinstance(node, A.Return):
            if node.value:
                self._find_spawn_calls(node.value)
        elif isinstance(node, A.Call):
            for arg in node.args:
                self._find_spawn_calls(arg)
        elif isinstance(node, A.BinaryOp):
            self._find_spawn_calls(node.left)
            self._find_spawn_calls(node.right)

    def _analyze_node(self, node: A.ASTNode) -> None:
        """Analyze a node for null and shared variable issues."""
        if node is None:
            return

        if isinstance(node, A.Function):
            self._analyze_function(node)
        elif isinstance(node, A.ClassDef):
            for method in node.methods:
                self._analyze_node(method)

    def _analyze_function(self, func: A.Function) -> None:
        """Analyze a function for null flow and variable access."""
        ctx = self.functions.get(func.name)
        if ctx is None:
            ctx = FunctionContext(name=func.name)
            self.functions[func.name] = ctx

        self.current_func = ctx

        # Initialize parameters as NOT_NULL and mark them as local
        # Params are tuples: (name, type, default_value)
        for param in func.params:
            if isinstance(param, tuple):
                param_name = param[0]  # First element is name
            elif isinstance(param, str):
                param_name = param
            else:
                param_name = str(param)
            ctx.null_states[param_name] = NullState.NOT_NULL
            ctx.params.add(param_name)  # Mark as parameter (local)

        # Analyze body
        for stmt in func.body:
            self._analyze_statement(stmt)

        self.current_func = None

    def _analyze_statement(self, stmt: A.ASTNode) -> None:
        """Analyze a statement for null flow."""
        if stmt is None or self.current_func is None:
            return

        ctx = self.current_func

        if isinstance(stmt, A.VarDecl):
            # Track null state based on initializer - VarDecl uses var_name
            # VarDecl declares a LOCAL variable
            var_name = stmt.var_name
            ctx.locals.add(var_name)  # Mark as local variable
            if stmt.init_value is None or isinstance(stmt.init_value, A.Null):
                ctx.null_states[var_name] = NullState.NULL
            else:
                # Check if the init expression could be null
                self._check_null_access(stmt.init_value)
                ctx.null_states[var_name] = self._expr_null_state(stmt.init_value)
            # Don't track writes for local variable declarations (already in ctx.locals)

        elif isinstance(stmt, A.Assign):
            # Update null state - Assign uses var_name not target
            var_name = stmt.var_name

            # Check if this is a GLOBAL variable (declared at file scope)
            # or a new LOCAL variable (first assignment in function)
            if var_name in self.global_vars:
                # Writing to a known global - track for race detection
                pass  # Will track as write below
            elif var_name not in ctx.locals and var_name not in ctx.params:
                # First assignment to undeclared var - this creates a LOCAL
                ctx.locals.add(var_name)

            if isinstance(stmt.value, A.Null):
                ctx.null_states[var_name] = NullState.NULL
            else:
                self._check_null_access(stmt.value)
                ctx.null_states[var_name] = self._expr_null_state(stmt.value)

            # Only track writes to GLOBAL variables for race detection
            if var_name in self.global_vars:
                ctx.writes.add(var_name)
                if var_name not in ctx.write_lines:
                    ctx.write_lines[var_name] = getattr(stmt, "line", 0)
            self._check_null_access(stmt.value)

        elif isinstance(stmt, A.If):
            self._analyze_if(stmt)

        elif isinstance(stmt, A.While):
            self._check_null_access(stmt.cond)
            # After condition, variables checked for null are known non-null
            self._refine_from_condition(stmt.cond, inside_true_branch=True)
            for s in stmt.body:
                self._analyze_statement(s)

        elif isinstance(stmt, A.For):
            if stmt.init:
                self._analyze_statement(stmt.init)
            if stmt.cond:
                self._check_null_access(stmt.cond)
            if stmt.step:
                self._analyze_statement(stmt.step)
            for s in stmt.body:
                self._analyze_statement(s)

        elif isinstance(stmt, A.Foreach):
            self._check_null_access(stmt.iterable)
            # Loop variable is NOT_NULL during iteration
            ctx.null_states[stmt.var_name] = NullState.NOT_NULL
            for s in stmt.body:
                self._analyze_statement(s)

        elif isinstance(stmt, A.Return):
            if stmt.value:
                self._check_null_access(stmt.value)

        elif isinstance(stmt, A.Call):
            self._check_null_access(stmt)
            for arg in stmt.args:
                self._check_null_access(arg)

        elif isinstance(stmt, A.MethodCall):
            self._check_null_access(stmt.object_expr)
            for arg in stmt.args:
                self._check_null_access(arg)

        elif isinstance(stmt, A.FieldAccess):
            self._check_null_access(stmt.object_expr)

        elif isinstance(stmt, A.FieldAssign):
            self._check_null_access(stmt.object_expr)
            self._check_null_access(stmt.value)

    def _analyze_if(self, node: A.If) -> None:
        """Analyze if statement with branch-aware null tracking."""
        if self.current_func is None:
            return

        ctx = self.current_func
        self._check_null_access(node.cond)

        # Save state before branches
        state_before = ctx.null_states.copy()

        # Analyze then branch with refined state
        self._refine_from_condition(node.cond, inside_true_branch=True)
        for stmt in node.then_body:
            self._analyze_statement(stmt)
        state_after_then = ctx.null_states.copy()

        # Restore and analyze else branch
        ctx.null_states = state_before.copy()
        if node.else_body:
            self._refine_from_condition(node.cond, inside_true_branch=False)
            for stmt in node.else_body:
                self._analyze_statement(stmt)
        state_after_else = ctx.null_states.copy()

        # Merge states: if either branch could be null, result is MAYBE_NULL
        merged: dict[str, NullState] = {}
        all_vars = set(state_after_then.keys()) | set(state_after_else.keys())
        for var in all_vars:
            then_state = state_after_then.get(var, NullState.MAYBE_NULL)
            else_state = state_after_else.get(var, NullState.MAYBE_NULL)
            if then_state == else_state:
                merged[var] = then_state
            else:
                merged[var] = NullState.MAYBE_NULL
        ctx.null_states = merged

    def _refine_from_condition(self, cond: A.ASTNode, inside_true_branch: bool) -> None:
        """Refine null states based on condition.

        If condition is 'x != null' and we're in true branch, x is NOT_NULL.
        If condition is 'x == null' and we're in false branch, x is NOT_NULL.
        """
        if self.current_func is None:
            return

        ctx = self.current_func

        if isinstance(cond, A.BinaryOp):
            # x != null
            if cond.op in ("!=", "is_not") and isinstance(cond.right, A.Null):
                if isinstance(cond.left, A.Variable):
                    if inside_true_branch:
                        ctx.null_states[cond.left.name] = NullState.NOT_NULL
                    else:
                        ctx.null_states[cond.left.name] = NullState.NULL
            # x == null
            elif cond.op in ("==", "is") and isinstance(cond.right, A.Null):
                if isinstance(cond.left, A.Variable):
                    if inside_true_branch:
                        ctx.null_states[cond.left.name] = NullState.NULL
                    else:
                        ctx.null_states[cond.left.name] = NullState.NOT_NULL
            # null != x
            elif (
                cond.op in ("!=", "is_not") and isinstance(cond.left, A.Null)
            ) and isinstance(cond.right, A.Variable):
                if inside_true_branch:
                    ctx.null_states[cond.right.name] = NullState.NOT_NULL
                else:
                    ctx.null_states[cond.right.name] = NullState.NULL

    def _check_null_access(self, expr: A.ASTNode) -> None:
        """Check if expression accesses a potentially null variable unsafely."""
        if expr is None or self.current_func is None:
            return

        ctx = self.current_func

        # Field access on potentially null object
        if isinstance(expr, A.FieldAccess):
            if isinstance(expr.object_expr, A.Variable):
                var_name = expr.object_expr.name
                # Skip type-level accesses (Enum.Variant, Record.field, etc.)
                if var_name in self.type_names:
                    return
                state = ctx.null_states.get(var_name, NullState.MAYBE_NULL)
                if state in (NullState.NULL, NullState.MAYBE_NULL):
                    self.warnings.append(
                        AnalysisWarning(
                            line=0,  # Would need line info in AST
                            column=0,
                            category="null",
                            message=f"'{var_name}' may be null when accessing '.{expr.field_name}'",
                            suggestion=f"Add 'if {var_name} != null then' check before access",
                            severity="error",
                        )
                    )
            self._check_null_access(expr.object_expr)

        # Method call on potentially null object
        elif isinstance(expr, A.MethodCall):
            if isinstance(expr.object_expr, A.Variable):
                var_name = expr.object_expr.name
                # Skip type-level accesses
                if var_name in self.type_names:
                    return
                state = ctx.null_states.get(var_name, NullState.MAYBE_NULL)
                if state in (NullState.NULL, NullState.MAYBE_NULL):
                    self.warnings.append(
                        AnalysisWarning(
                            line=0,
                            column=0,
                            category="null",
                            message=f"'{var_name}' may be null when calling '.{expr.method_name}()'",
                            suggestion=f"Add 'if {var_name} != null then' check before call",
                            severity="error",
                        )
                    )
            self._check_null_access(expr.object_expr)
            for arg in expr.args:
                self._check_null_access(arg)

        # Recurse into sub-expressions
        elif isinstance(expr, A.BinaryOp):
            self._check_null_access(expr.left)
            self._check_null_access(expr.right)
        elif isinstance(expr, A.UnaryOp):
            self._check_null_access(expr.operand)
        elif isinstance(expr, A.Call):
            # Check for atomic operations - these are safe for concurrency
            self._track_atomic_call(expr)
            for arg in expr.args:
                self._check_null_access(arg)
        elif isinstance(expr, A.ArrayAccess):
            self._check_null_access(expr.array)
            self._check_null_access(expr.index)

        # Track variable reads for race detection
        # Only track GLOBAL variables (potential race candidates)
        if isinstance(expr, A.Variable):
            var_name = expr.name
            if var_name in self.global_vars:
                ctx.reads.add(var_name)
                if var_name not in ctx.read_lines:
                    ctx.read_lines[var_name] = getattr(expr, "line", 0)

    def _track_atomic_call(self, call: A.Call) -> None:
        """Track atomic and channel operations for safe concurrent access."""
        if self.current_func is None:
            return

        ctx = self.current_func
        func_name = call.name

        # Atomic load operations - mark variable as safely read
        if func_name in ("atomic_load",) and (
            call.args and isinstance(arg_at(call, 0), A.Variable)
        ):
            var_name = arg_at(call, 0).name
            ctx.atomic_reads.add(var_name)
            # Remove from unsafe reads since it's atomic
            ctx.reads.discard(var_name)

        # Atomic read-modify-write operations - both read and write atomically
        if (
            func_name
            in (
                "atomic_add",
                "atomic_sub",
                "atomic_fetch_add",
                "atomic_fetch_sub",
                "atomic_fetch_and",
                "atomic_fetch_or",
                "atomic_fetch_xor",
            )
            and call.args
            and isinstance(arg_at(call, 0), A.Variable)
        ):
            var_name = arg_at(call, 0).name
            ctx.atomic_reads.add(var_name)
            ctx.atomic_writes.add(var_name)
            # Remove from unsafe accesses since it's atomic
            ctx.reads.discard(var_name)
            ctx.writes.discard(var_name)

        # Atomic store operations - mark variable as safely written
        if func_name in ("atomic_store", "atomic_cas", "atomic_exchange") and (
            call.args and isinstance(arg_at(call, 0), A.Variable)
        ):
            var_name = arg_at(call, 0).name
            ctx.atomic_writes.add(var_name)
            # Remove from unsafe writes since it's atomic
            ctx.writes.discard(var_name)

        # Channel operations - variable is safely transferred
        if func_name in self.CHANNEL_OPS:
            for arg in call.args:
                if isinstance(arg, A.Variable):
                    ctx.channel_vars.add(arg.name)

    def _expr_null_state(self, expr: A.ASTNode) -> NullState:
        """Determine if an expression could be null."""
        if expr is None:
            return NullState.NULL
        if isinstance(expr, A.Null):
            return NullState.NULL
        if isinstance(expr, (A.Number, A.Bool, A.StringLit, A.ArrayLit)):
            return NullState.NOT_NULL
        if isinstance(expr, A.Variable) and self.current_func:
            return self.current_func.null_states.get(expr.name, NullState.MAYBE_NULL)
        if isinstance(expr, A.NewExpr):
            return NullState.NOT_NULL  # new always returns non-null
        if isinstance(expr, A.Call):
            # AILang has no nullable return types — builtins and user
            # functions always return concrete values or void.  Treating
            # call results as MAYBE_NULL produces overwhelming false
            # positives on field access (e.g. split().length, new().field).
            return NullState.NOT_NULL
        if isinstance(
            expr,
            (
                A.BinaryOp,
                A.UnaryOp,
                A.TernaryOp,
                A.Cast,
                A.InterpolatedString,
                A.DictLit,
                A.Range,
                A.ArrayAccess,
                A.FieldAccess,
                A.MethodCall,
            ),
        ):
            return NullState.NOT_NULL
        return NullState.MAYBE_NULL

    def _best_line_for_var(
        self, var_name: str, func_names: list[str], is_write: bool
    ) -> int:
        """Get the best line number for a variable access across functions."""
        for fname in func_names:
            ctx = self.functions.get(fname)
            if ctx is None:
                continue
            lines_dict = ctx.write_lines if is_write else ctx.read_lines
            line_num = lines_dict.get(var_name, 0)
            if line_num > 0:
                return line_num
        return 0

    def _check_shared_variables(self) -> None:
        """Check for data races in concurrent code."""
        # Collect accesses from all functions
        concurrent_writes: dict[str, list[str]] = {}  # var -> [func names]
        concurrent_reads: dict[str, list[str]] = {}  # var -> [func names]
        main_writes: set[str] = set()
        main_reads: set[str] = set()

        for name, ctx in self.functions.items():
            if ctx.is_concurrent():
                # Track concurrent writes (excluding atomic)
                unsafe_writes = ctx.writes - ctx.atomic_writes - ctx.channel_vars
                for var in unsafe_writes:
                    if var not in concurrent_writes:
                        concurrent_writes[var] = []
                    concurrent_writes[var].append(name)

                # Track concurrent reads (excluding atomic)
                unsafe_reads = ctx.reads - ctx.atomic_reads - ctx.channel_vars
                for var in unsafe_reads:
                    if var not in concurrent_reads:
                        concurrent_reads[var] = []
                    concurrent_reads[var].append(name)
            else:
                # Main thread / non-concurrent
                main_writes.update(ctx.writes - ctx.atomic_writes)
                main_reads.update(ctx.reads - ctx.atomic_reads)

        # Check for WRITE-WRITE races (multiple concurrent writers)
        for var, writers in concurrent_writes.items():
            race_line = self._best_line_for_var(var, writers, is_write=True)
            if len(writers) > 1:
                self.warnings.append(
                    AnalysisWarning(
                        line=race_line,
                        column=0,
                        category="write-write",
                        message=f"DATA RACE: '{var}' written by multiple concurrent functions: {', '.join(writers)}",
                        suggestion=f"Use atomic_store({var}, value) or protect with mutex",
                        severity="error",
                    )
                )
            # Also check write in concurrent + write in main
            if var in main_writes:
                self.warnings.append(
                    AnalysisWarning(
                        line=race_line,
                        column=0,
                        category="write-write",
                        message=f"DATA RACE: '{var}' written in main thread AND concurrent function(s): {', '.join(writers)}",
                        suggestion=f"Use atomic_store({var}, value) or synchronize access",
                        severity="error",
                    )
                )

        # Check for READ-WRITE races (concurrent read + concurrent/main write)
        for var, readers in concurrent_reads.items():
            race_line = self._best_line_for_var(var, readers, is_write=False)
            # Concurrent read + concurrent write
            if var in concurrent_writes:
                writers = concurrent_writes[var]
                # Only warn if different functions (same function is sequential)
                conflicting = set(writers) - set(readers)
                if conflicting or len(writers) > 0:
                    self.warnings.append(
                        AnalysisWarning(
                            line=race_line,
                            column=0,
                            category="read-write",
                            message=f"DATA RACE: '{var}' read in {readers} while written in {writers}",
                            suggestion=f"Use atomic_load({var}) for reads, atomic_store() for writes",
                            severity="error",
                        )
                    )

            # Concurrent read + main write
            if var in main_writes:
                self.warnings.append(
                    AnalysisWarning(
                        line=race_line,
                        column=0,
                        category="read-write",
                        message=f"DATA RACE: '{var}' read in concurrent function(s) {readers} but written in main thread",
                        suggestion=f"Use atomic_load({var}) or pass value through channel",
                        severity="warning",
                    )
                )

        # Check for main reads of concurrently written variables
        for var in main_reads:
            if var in concurrent_writes:
                writers = concurrent_writes[var]
                race_line = self._best_line_for_var(var, writers, is_write=True)
                self.warnings.append(
                    AnalysisWarning(
                        line=race_line,
                        column=0,
                        category="read-write",
                        message=f"DATA RACE: '{var}' read in main thread but written in concurrent function(s): {', '.join(writers)}",
                        suggestion=f"Use atomic_load({var}) or wait for completion before reading",
                        severity="warning",
                    )
                )


def analyze_ast(ast_nodes: list[A.ASTNode]) -> list[AnalysisWarning]:
    """Convenience function to analyze an AST."""
    analyzer = StaticAnalyzer()
    return analyzer.analyze(ast_nodes)

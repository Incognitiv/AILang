"""Core threading and atomic builtins for ExprBuiltinThreadingEmitter."""

from __future__ import annotations

import sys
from parser.ast import AtomicOp, Await, Call, Join, Spawn, Variable

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ARG_THIRD, ExprGenError


def visit_Spawn(self, node: Spawn) -> ir.Value:
    """Generate code for spawning a new thread.

    spawn func_call

    Returns a thread handle (opaque pointer) that can be joined later.

    Implementation uses platform-specific threading:
    - Windows: CreateThread
    - POSIX: pthread_create

    For simplicity, we create a wrapper function that:
    1. Calls the user's function
    2. Stores the result in a shared location
    3. Returns the thread exit code
    """

    # Call is already imported at module level
    if not isinstance(node.func_call, Call):
        raise ExprGenError("spawn requires a function call")

    call_node = node.func_call
    func_name = call_node.name

    # Get the function being called
    target_func = self.codegen.functions.get(func_name)
    if not target_func:
        raise ExprGenError(f"spawn: undefined function '{func_name}'")

    # For now, only support nullary functions (no args) for simplicity
    if call_node.args:
        raise ExprGenError("spawn currently only supports functions with no arguments")

    void_ptr = ir.IntType(8).as_pointer()

    # Detect platform from target triple
    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        return self._spawn_windows(func_name, target_func, void_ptr)
    return self._spawn_pthread(func_name, target_func, void_ptr)


def _spawn_windows(
    self, func_name: str, target_func: ir.Function, void_ptr: ir.Type
) -> ir.Value:
    """Spawn thread using Windows CreateThread API"""

    # Create thread-entry wrapper function if not exists
    # Windows thread func signature: DWORD WINAPI ThreadFunc(LPVOID lpParam)
    wrapper_name = f"__thread_wrapper_{func_name}"
    if wrapper_name not in self.codegen.functions:
        # Save current state
        saved_func = self.codegen.func
        saved_builder = self.codegen.builder

        # Create wrapper: DWORD (*)(LPVOID) -> i32 (*)(i8*)
        wrapper_ty = ir.FunctionType(ir.IntType(32), [void_ptr])
        wrapper_func = ir.Function(self.codegen.module, wrapper_ty, wrapper_name)
        self.codegen.functions[wrapper_name] = wrapper_func

        entry = wrapper_func.append_basic_block("entry")
        wrapper_builder = ir.IRBuilder(entry)

        # Call the target function
        ret_val = wrapper_builder.call(target_func, [], name="call_result")

        # Convert result to DWORD (i32)
        if ret_val.type == ir.IntType(64):
            ret_dword = wrapper_builder.trunc(ret_val, ir.IntType(32), name="ret_dword")
        elif ret_val.type == ir.IntType(32):
            ret_dword = ret_val
        else:
            ret_dword = ir.Constant(ir.IntType(32), 0)

        wrapper_builder.ret(ret_dword)

        # Restore state
        self.codegen.func = saved_func
        self.codegen.builder = saved_builder

    wrapper_func = self.codegen.functions[wrapper_name]

    # Get CreateThread
    create_thread = self.codegen.get_create_thread()

    # Allocate thread ID storage
    thread_id_ptr = self.builder.alloca(ir.IntType(32), name="thread_id")

    # Call CreateThread(NULL, 0, wrapper, NULL, 0, &thread_id)
    null_ptr = ir.Constant(void_ptr, None)
    zero = ir.Constant(ir.IntType(64), 0)
    zero32 = ir.Constant(ir.IntType(32), 0)

    handle = self.builder.call(
        create_thread,
        [null_ptr, zero, wrapper_func, null_ptr, zero32, thread_id_ptr],
        name="thread_handle",
    )

    # Return the handle as i64 (pointer to int)
    handle_int = self.builder.ptrtoint(handle, ir.IntType(64), name="handle_int")
    return handle_int


def _spawn_pthread(
    self, func_name: str, target_func: ir.Function, void_ptr: ir.Type
) -> ir.Value:
    """Spawn thread using POSIX pthread API"""

    # Allocate thread ID storage
    thread_id_ptr = self.builder.alloca(ir.IntType(64), name="thread_id")

    # Get pthread_create
    pthread_create = self.codegen.get_pthread_create()

    # Create thread-entry wrapper function if not exists
    wrapper_name = f"__thread_wrapper_{func_name}"
    if wrapper_name not in self.codegen.functions:
        # Save current state (use .func not .current_function property)
        saved_func = self.codegen.func
        saved_builder = self.codegen.builder

        # Create wrapper: void* wrapper(void* arg)
        wrapper_ty = ir.FunctionType(void_ptr, [void_ptr])
        wrapper_func = ir.Function(self.codegen.module, wrapper_ty, wrapper_name)
        self.codegen.functions[wrapper_name] = wrapper_func

        entry = wrapper_func.append_basic_block("entry")
        wrapper_builder = ir.IRBuilder(entry)

        # Call the target function
        ret_val = wrapper_builder.call(target_func, [], name="call_result")

        # Convert result to void* (inttoptr)
        if ret_val.type == ir.IntType(64):
            ret_ptr = wrapper_builder.inttoptr(ret_val, void_ptr, name="ret_ptr")
        else:
            ret_ptr = ir.Constant(void_ptr, None)

        wrapper_builder.ret(ret_ptr)

        # Restore state
        self.codegen.func = saved_func
        self.codegen.builder = saved_builder

    wrapper_func = self.codegen.functions[wrapper_name]

    # Call pthread_create(thread_id_ptr, NULL, wrapper, NULL)
    null_ptr = ir.Constant(void_ptr, None)
    self.builder.call(
        pthread_create,
        [thread_id_ptr, null_ptr, wrapper_func, null_ptr],
        name="pthread_create_result",
    )

    # Return the thread ID as handle
    thread_id = self.builder.load(thread_id_ptr, name="thread_handle")
    return thread_id


def visit_Join(self, node: Join) -> ir.Value:
    """Generate code for joining (waiting on) a thread.

    join(handle)

    Waits for the thread to complete and returns its result.
    """

    # Get the thread handle
    handle = self.generate_expr(node.handle)

    void_ptr = ir.IntType(8).as_pointer()

    # Detect platform from target triple
    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        return self._join_windows(handle, void_ptr)
    return self._join_pthread(handle, void_ptr)


def _join_windows(self, handle: ir.Value, void_ptr: ir.Type) -> ir.Value:
    """Wait for thread using Windows WaitForSingleObject API"""

    # Convert handle from i64 back to pointer
    if handle.type == ir.IntType(64):
        handle_ptr = self.builder.inttoptr(handle, void_ptr, name="handle_ptr")
    else:
        handle_ptr = handle

    # WaitForSingleObject(handle, INFINITE)
    wait_func = self.codegen.get_wait_for_single_object()
    infinite = ir.Constant(ir.IntType(32), 0xFFFFFFFF)  # INFINITE
    self.builder.call(wait_func, [handle_ptr, infinite], name="wait_result")

    # Get the thread's exit code
    get_exit_code = self.codegen.get_exit_code_thread()
    exit_code_ptr = self.builder.alloca(ir.IntType(32), name="exit_code")
    self.builder.call(
        get_exit_code, [handle_ptr, exit_code_ptr], name="get_exit_result"
    )

    # Close the handle
    close_handle = self.codegen.get_close_handle()
    self.builder.call(close_handle, [handle_ptr], name="close_result")

    # Return the exit code as i64
    exit_code = self.builder.load(exit_code_ptr, name="exit_code_val")
    result = self.builder.zext(exit_code, ir.IntType(64), name="result")
    return result


def _join_pthread(self, handle: ir.Value, void_ptr: ir.Type) -> ir.Value:
    """Wait for thread using POSIX pthread_join API"""
    # Allocate storage for return value pointer
    retval_ptr = self.builder.alloca(void_ptr, name="retval_ptr")

    # Get pthread_join
    pthread_join = self.codegen.get_pthread_join()

    # Ensure handle is i64
    if handle.type != ir.IntType(64):
        handle = self.builder.zext(handle, ir.IntType(64), name="handle_ext")

    # Call pthread_join(handle, &retval)
    self.builder.call(pthread_join, [handle, retval_ptr], name="join_result")

    # Load and convert return value
    retval = self.builder.load(retval_ptr, name="thread_retval")
    result = self.builder.ptrtoint(retval, ir.IntType(64), name="thread_result")

    return result


def visit_Await(self, node: Await) -> ir.Value:
    """Generate code for await expression.

    await async_call()

    In AILang's simple async model, await currently works like a synchronous
    call - it evaluates the expression and returns its value. This provides
    syntactic support for async/await patterns while the underlying runtime
    can be extended later to support true cooperative scheduling.

    For RTOS use, this can be extended to:
    - Yield to scheduler
    - Resume when operation completes
    - Integrate with event loops
    """
    # For now, await just evaluates the expression synchronously
    # This allows async/await syntax to be used, and the runtime
    # can be extended later for true cooperative multitasking
    result = self.generate_expr(node.expr)
    return result


def visit_AtomicOp(self, node: AtomicOp) -> ir.Value:
    """Generate code for atomic operations.

    atomic_load(ptr)
    atomic_store(ptr, value)
    atomic_add(ptr, value)
    atomic_exchange(ptr, value)
    atomic_compare_exchange(ptr, expected, desired)
    """
    ptr = self.generate_expr(node.ptr)

    # Ensure ptr is a pointer type
    if not isinstance(ptr.type, ir.PointerType):
        raise ExprGenError(f"atomic operations require pointer, got {ptr.type}")

    op = node.op.lower()

    if op == "load":
        # Atomic load with sequential consistency
        # llvmlite doesn't support atomic load directly, use atomic_rmw("or", ptr, 0)
        # to get the value atomically without changing it
        elem_type = ptr.type.pointee
        zero_val = ir.Constant(elem_type, 0)
        return self.builder.atomic_rmw(
            "or", ptr, zero_val, "seq_cst", name="atomic_load"
        )

    if op == "store":
        if node.value is None:
            raise ExprGenError("atomic_store requires a value")
        value = self.generate_expr(node.value)
        # Use atomic_rmw("xchg") for atomic store since plain store isn't atomic
        self.builder.atomic_rmw("xchg", ptr, value, "seq_cst", name="atomic_store")
        return ir.Constant(ir.IntType(64), 0)  # void return

    if op == "add":
        if node.value is None:
            raise ExprGenError("atomic_add requires a value")
        value = self.generate_expr(node.value)
        return self.builder.atomic_rmw("add", ptr, value, "seq_cst", name="atomic_add")

    if op == "sub":
        if node.value is None:
            raise ExprGenError("atomic_sub requires a value")
        value = self.generate_expr(node.value)
        return self.builder.atomic_rmw("sub", ptr, value, "seq_cst", name="atomic_sub")

    if op in {"exchange", "xchg"}:
        if node.value is None:
            raise ExprGenError("atomic_exchange requires a value")
        value = self.generate_expr(node.value)
        return self.builder.atomic_rmw(
            "xchg", ptr, value, "seq_cst", name="atomic_xchg"
        )

    if op in {"cmpxchg", "compare_exchange"}:
        if node.expected is None or node.value is None:
            raise ExprGenError(
                "atomic_compare_exchange requires expected and desired values"
            )
        expected = self.generate_expr(node.expected)
        desired = self.generate_expr(node.value)
        result = self.builder.cmpxchg(
            ptr, expected, desired, "seq_cst", "seq_cst", name="atomic_cmpxchg"
        )
        # cmpxchg returns {T, i1}, extract the success flag
        return self.builder.extract_value(result, 1, name="cmpxchg_success")

    raise ExprGenError(f"Unknown atomic operation: {op}")


# Atomic operations as function calls
def _get_variable_ptr(self, arg) -> ir.Value:
    """Get pointer to a variable for atomic operations.

    Atomics need the address of the variable, not its value.
    This handles local and global variables.
    """
    # If it's a Variable node, get its pointer directly
    if isinstance(arg, Variable):
        var_name = arg.name
        # Check locals first
        if var_name in self.codegen.locals:
            ptr = self.codegen.locals[var_name]
            if isinstance(ptr.type, ir.PointerType):
                return ptr
        # Check globals
        if var_name in self.codegen.globals:
            return self.codegen.globals[var_name]
        raise ExprGenError(f"Cannot get address of unknown variable: {var_name}")

    # For other expressions, generate and check if it's already a pointer
    val = self.generate_expr(arg)
    if isinstance(val.type, ir.PointerType):
        return val
    raise ExprGenError(f"atomic operation requires variable or pointer, got {val.type}")


def _builtin_atomic_load(self, args) -> ir.Value:
    """atomic_load(var) -> int"""
    if len(args) != 1:
        raise ExprGenError("atomic_load() expects 1 argument")
    ptr = self._get_variable_ptr(args[ARG_FIRST])
    result = self.builder.load(ptr, name="atomic_load")
    return result


def _builtin_atomic_store(self, args) -> ir.Value:
    """atomic_store(var, value) -> void"""
    if len(args) != 2:
        raise ExprGenError("atomic_store() expects 2 arguments")
    ptr = self._get_variable_ptr(args[ARG_FIRST])
    value = self.generate_expr(args[ARG_SECOND])
    self.builder.store(value, ptr)
    return ir.Constant(ir.IntType(64), 0)


def _builtin_atomic_add(self, args) -> ir.Value:
    """atomic_add(var, value) -> old_value"""
    if len(args) != 2:
        raise ExprGenError("atomic_add() expects 2 arguments")
    ptr = self._get_variable_ptr(args[ARG_FIRST])
    value = self.generate_expr(args[ARG_SECOND])
    return self.builder.atomic_rmw("add", ptr, value, "seq_cst", name="atomic_add")


def _builtin_atomic_sub(self, args) -> ir.Value:
    """atomic_sub(var, value) -> old_value"""
    if len(args) != 2:
        raise ExprGenError("atomic_sub() expects 2 arguments")
    ptr = self._get_variable_ptr(args[ARG_FIRST])
    value = self.generate_expr(args[ARG_SECOND])
    return self.builder.atomic_rmw("sub", ptr, value, "seq_cst", name="atomic_sub")


def _builtin_atomic_exchange(self, args) -> ir.Value:
    """atomic_exchange(var, value) -> old_value"""
    if len(args) != 2:
        raise ExprGenError("atomic_exchange() expects 2 arguments")
    ptr = self._get_variable_ptr(args[ARG_FIRST])
    value = self.generate_expr(args[ARG_SECOND])
    return self.builder.atomic_rmw("xchg", ptr, value, "seq_cst", name="atomic_xchg")


def _builtin_atomic_cmpxchg(self, args) -> ir.Value:
    """atomic_compare_exchange(var, expected, desired) -> success (bool)"""
    if len(args) != 3:
        raise ExprGenError("atomic_compare_exchange() expects 3 arguments")
    ptr = self._get_variable_ptr(args[ARG_FIRST])
    expected = self.generate_expr(args[ARG_SECOND])
    desired = self.generate_expr(args[ARG_THIRD])
    result = self.builder.cmpxchg(
        ptr, expected, desired, "seq_cst", "seq_cst", name="atomic_cmpxchg"
    )
    return self.builder.extract_value(result, ARG_SECOND, name="cmpxchg_success")


def _builtin_thread_id(self, args) -> ir.Value:
    """Get current thread ID (platform-specific)"""

    if args:
        raise ExprGenError("thread_id() takes no arguments")

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        # Use GetCurrentThreadId() on Windows
        get_tid_ty = ir.FunctionType(ir.IntType(32), [])
        get_tid = ir.Function(self.codegen.module, get_tid_ty, "GetCurrentThreadId")
        tid32 = self.builder.call(get_tid, [], name="thread_id32")
        return self.builder.zext(tid32, ir.IntType(64), name="thread_id")
    # Use pthread_self() on POSIX
    pthread_self_ty = ir.FunctionType(ir.IntType(64), [])
    pthread_self = ir.Function(self.codegen.module, pthread_self_ty, "pthread_self")
    return self.builder.call(pthread_self, [], name="thread_id")


def _builtin_num_cpus(self, args) -> ir.Value:
    """Get number of available CPUs (platform-specific implementation)"""

    if args:
        raise ExprGenError("num_cpus() takes no arguments")

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        # Windows: Use GetActiveProcessorCount(ALL_PROCESSOR_GROUPS)
        get_proc_count_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(16)])
        get_proc_count = ir.Function(
            self.codegen.module, get_proc_count_ty, "GetActiveProcessorCount"
        )
        # ALL_PROCESSOR_GROUPS = 0xFFFF
        all_groups = ir.Constant(ir.IntType(16), 0xFFFF)
        count32 = self.builder.call(get_proc_count, [all_groups], name="cpu_count32")
        return self.builder.zext(count32, ir.IntType(64), name="cpu_count")
    # POSIX: Use sysconf(_SC_NPROCESSORS_ONLN) - value 84 on Linux
    sysconf_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(32)])
    sysconf = ir.Function(self.codegen.module, sysconf_ty, "sysconf")
    sc_nprocessors = ir.Constant(ir.IntType(32), 84)
    result = self.builder.call(sysconf, [sc_nprocessors], name="cpu_count")
    # sysconf returns -1 on error, clamp to minimum of 1
    one = ir.Constant(ir.IntType(64), 1)
    is_valid = self.builder.icmp_signed(">", result, ir.Constant(ir.IntType(64), 0))
    return self.builder.select(is_valid, result, one, name="cpu_count_safe")


def _builtin_yield_thread(self, args) -> ir.Value:
    """Yield the current thread to let others run"""

    if args:
        raise ExprGenError("yield_thread() takes no arguments")

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        # Use SwitchToThread() on Windows
        switch_ty = ir.FunctionType(ir.IntType(32), [])
        switch_func = ir.Function(self.codegen.module, switch_ty, "SwitchToThread")
        self.builder.call(switch_func, [])
    else:
        # Use sched_yield() on POSIX
        sched_yield_ty = ir.FunctionType(ir.IntType(32), [])
        sched_yield = ir.Function(self.codegen.module, sched_yield_ty, "sched_yield")
        self.builder.call(sched_yield, [])

    return ir.Constant(ir.IntType(64), 0)


def _builtin_sleep_ms(self, args) -> ir.Value:
    """Sleep for specified milliseconds"""

    if len(args) != 1:
        raise ExprGenError("sleep_ms() requires 1 argument")

    ms = self.generate_expr(args[ARG_FIRST])

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )

    if is_windows:
        # Use Sleep() on Windows (takes milliseconds)
        sleep_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(32)])
        sleep_func = ir.Function(self.codegen.module, sleep_ty, "Sleep")
        ms32 = self.builder.trunc(ms, ir.IntType(32), name="ms32")
        self.builder.call(sleep_func, [ms32])
    else:
        # Use usleep() on POSIX (microseconds)
        usleep_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(32)])
        usleep_func = ir.Function(self.codegen.module, usleep_ty, "usleep")

        # Convert ms to us
        thousand = ir.Constant(ir.IntType(64), 1000)
        us = self.builder.mul(ms, thousand, name="us")
        us32 = self.builder.trunc(us, ir.IntType(32), name="us32")

        self.builder.call(usleep_func, [us32])

    return ir.Constant(ir.IntType(64), 0)


# ------------------------------------------------------------------
# Synchronization primitives (Ada/SPARK-inspired: mutex, condvar, rwlock)
# ------------------------------------------------------------------
# Sizes: Windows CRITICAL_SECTION=40B, SRWLOCK=8B, CONDITION_VARIABLE=8B
#        POSIX pthread_mutex_t <=64B, pthread_cond_t <=64B, pthread_rwlock_t <=200B
# We allocate conservatively to cover all platforms.

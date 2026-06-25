"""Built-in API listing output for CLI help."""

from __future__ import annotations


def _print_builtins():
    """Print all built-in functions, keywords, and types."""
    print("=" * 70)
    print("AILang Built-in Reference")
    print("=" * 70)
    print("\n=== TYPES ===")
    types = {
        "Integer types": [
            "tiny/byte (8-bit)",
            "small (16-bit)",
            "short (32-bit)",
            "int (64-bit)",
            "long (128-bit)",
            "wide (256-bit)",
            "vast (512-bit)",
            "grand (1024-bit)",
            "giant (2048-bit)",
            "titan (4096-bit)",
            "colos (8192-bit)",
            "unbounded",
        ],
        "Unsigned": [
            "ubyte",
            "usmall",
            "ushort",
            "uint",
            "ulong",
            "uwide",
            "uvast",
            "ugrand",
            "ugiant",
            "utitan",
            "ucolos",
        ],
        "Float types": ["float (32-bit)", "double (64-bit)", "quad (128-bit)"],
        "Other": ["bool", "string", "array", "dict", "void"],
        "SIMD vectors": [
            "vec4i, vec8i, vec16i (int32)",
            "vec4l, vec8l (int64)",
            "vec4f, vec8f (float)",
            "vec2d, vec4d, vec8d (double)",
            "vec16b, vec32b, vec64b (bytes)",
        ],
    }
    for category, items in types.items():

        print(f"\n  {category}:")
        for item in items:

            print(f"    {item}")
    print("\n=== I/O FUNCTIONS ===")
    io_funcs = [
        "print(expr...)      - Print with newline",
        "puts(str)           - Print string with newline",
        "putc(char)          - Print single character",
        "input([prompt])     - Read line from stdin",
        "read_stdin()       - Read all stdin as a string",
        "read_file(path)     - Read file as string",
        "write_file(p, s)    - Write string to file",
        "read_bytes(p, sz)   - Read binary file",
        "write_bytes(p,d,sz) - Write binary data",
        "file_size(path)     - Get file size",
        "file_is_*(path)     - Hosted file type/mode predicates",
        "fd_is_tty(fd)       - Check whether a hosted fd is a terminal",
        "current_dir()       - Current working directory",
        "change_dir(path)    - Change current working directory",
        "list_dir(path)      - Directory entries as newline-separated string",
        "fd_open(p,flags,m)  - Open hosted fd with portable flags",
        "fd_read(fd,p,sz)    - Read bytes into pointer",
        "fd_write(fd,p,sz)   - Write bytes from pointer",
        "fd_close(fd)        - Close hosted fd",
        "fd_dup(fd)          - Duplicate hosted fd",
        "fd_dup2(src,dst)    - Duplicate fd onto a target fd",
        "fd_tell(fd)         - Current hosted fd offset",
        "fd_seek(fd,off)     - Set hosted fd offset",
        "fd_flush()          - Flush hosted stdio streams",
    ]
    for f in io_funcs:

        print(f"  {f}")
    print("\n=== STRING FUNCTIONS ===")
    str_funcs = [
        "len(s)              - String/array length",
        "strlen(s)           - String length (alias)",
        "char_at(s, i)       - Get character at index (safe)",
        "unsafe_char_at(s,i) - Get character (unchecked)",
        "ord(c)              - Character to ASCII code",
        "chr(n)              - ASCII code to character",
        "substr(s, start, n) - Substring",
        "concat(a, b)        - Concatenate strings",
        "index_of(s, sub[, start]) - Find substring from optional start offset",
        "startswith(s, pre)  - Check prefix",
        "endswith(s, suf)    - Check suffix",
        "str_replace(s,o,n)  - Replace in string",
        "str(n)              - Number to string",
        "parse_int(s)        - String to number",
        "hex(n)              - Number to hex string",
        "bin(n)              - Number to binary string",
        "oct(n)              - Number to octal string",
        "split(s, delim)     - Split string -> array of strings",
        "split_ints(s,delim) - Split string -> array of ints",
        "split_len(arr)      - Get split result length",
        "split_get(arr, i)   - Get element from split result",
    ]
    for f in str_funcs:

        print(f"  {f}")
    print("\n=== ARRAY FUNCTIONS ===")
    arr_funcs = [
        "array_new(size)     - Create array",
        "array_len(arr)      - Get length",
        "array_cap(arr)      - Get capacity",
        "array_push(arr, v)  - Append value",
        "array_pop(arr)      - Remove last",
        "arr[i]              - Index access (safe)",
        "arr[i, unsafe]      - Index access (unchecked)",
    ]
    for f in arr_funcs:

        print(f"  {f}")
    print("\n=== MATH FUNCTIONS ===")
    math_funcs = [
        "sqrt(x), pow(x,y)   - Square root, power",
        "sin(x), cos(x), tan(x), tanh(x) - Trigonometry",
        "exp(x), log(x)      - Exponential, natural log",
        "floor(x), ceil(x)   - Rounding",
        "fabs(x)             - Absolute value",
    ]
    for f in math_funcs:

        print(f"  {f}")
    print("\n=== MEMORY FUNCTIONS ===")
    mem_funcs = [
        "alloc(size)         - Allocate bytes (returns ptr)",
        "dealloc(ptr)        - Free memory",
        "peek(addr)          - Read from address",
        "peek64(ptr, off)    - Read 64-bit at offset",
        "poke(addr, val)     - Write to address",
        "poke64(ptr,off,val) - Write 64-bit at offset",
        "ptr_add(ptr, n)     - Pointer arithmetic",
        "ptr_sub(ptr, n)     - Pointer arithmetic",
        "ptr_array(...)      - Null-terminated pointer array",
        "memset(p, val, n)   - Fill memory with byte value",
        "memmove(d, s, n)    - Copy memory (overlap-safe)",
        "realloc(ptr, sz)    - Resize allocation",
        "calloc(cnt, sz)     - Allocate zeroed memory",
        "sizeof(type)        - Size of type in bytes",
        "alignof(type)       - Alignment of type in bytes",
        "offsetof(T, field)  - Field offset in bytes",
        "peek32(ptr, off)    - Read 32-bit at offset",
        "poke32(p,off,val)   - Write 32-bit at offset",
        "peek8(ptr, off)     - Read 8-bit at offset",
        "poke8(p,off,val)    - Write 8-bit at offset",
    ]
    for f in mem_funcs:

        print(f"  {f}")
    print("\n=== THREADING & ATOMICS ===")
    thread_funcs = [
        "spawn func()        - Create new thread",
        "join(handle)        - Wait for thread",
        "thread_id()         - Get current thread ID",
        "yield_thread()      - Yield CPU",
        "sleep_ms(ms)        - Sleep milliseconds",
        "channel(cap)        - Create channel",
        "chan_send(ch, val)  - Send to channel",
        "chan_recv(ch)       - Receive from channel",
        "chan_try_send(c,v)  - Non-blocking send",
        "chan_try_recv(ch)   - Non-blocking receive",
        "chan_close(ch)      - Close channel",
        "atomic_load(ptr)    - Atomic read",
        "atomic_store(p, v)  - Atomic write",
        "atomic_add(p, v)    - Atomic add",
        "atomic_sub(p, v)    - Atomic subtract",
        "atomic_exchange(p,v)- Atomic swap",
        "atomic_compare_exchange(p, exp, des)",
    ]
    for f in thread_funcs:

        print(f"  {f}")
    print("\n=== SYNCHRONIZATION ===")
    sync_funcs = [
        "mutex_create()      - Create mutex",
        "mutex_lock(mtx)     - Lock mutex",
        "mutex_unlock(mtx)   - Unlock mutex",
        "mutex_destroy(mtx)  - Destroy mutex",
        "cond_create()       - Create condition variable",
        "cond_wait(cv, mtx)  - Wait on condition",
        "cond_signal(cv)     - Signal one waiter",
        "cond_broadcast(cv)  - Signal all waiters",
        "cond_destroy(cv)    - Destroy condvar",
        "rwlock_create()     - Create read-write lock",
        "rwlock_read_lock(r) - Acquire shared read",
        "rwlock_write_lock(r)- Acquire exclusive write",
        "rwlock_read_unlock  - Release read lock",
        "rwlock_write_unlock - Release write lock",
        "rwlock_destroy(r)   - Destroy rwlock",
    ]
    for f in sync_funcs:

        print(f"  {f}")
    print("\n=== SIMD VECTOR FUNCTIONS ===")
    simd_funcs = [
        "vec_load(ptr)       - Load vector from memory",
        "vec_store(ptr, v)   - Store vector to memory",
        "vec_broadcast(val)  - Fill vector with value",
        "vec_add/sub/mul(a,b)- Arithmetic",
        "vec_and/or/xor(a,b) - Bitwise operations",
        "vec_nand/nor/xnor   - Logic gates",
        "vec_not(v)          - Bitwise NOT",
        "vec_cmpeq/gt/lt     - Comparisons",
        "vec_min/max/avg     - Reductions",
        "vec_shuffle(v, mask)- Shuffle elements",
        "vec_extract(v, i)   - Extract element",
        "vec_insert(v, i, x) - Insert element",
        "vec_hadd(v)         - Horizontal add",
        "vec_dot(a, b)       - Dot product",
        "vec_fma(a, b, c)    - Fused multiply-add",
        "vec_fms(a, b, c)    - Fused multiply-subtract",
        "vec_movemask(v)     - Extract sign bits",
        "vec_abs(v)          - Absolute value",
        "vec_blend(a,b,mask) - Blend by mask",
        "vec_permute(v, ctl) - Permute elements",
        "vec_gather(base, i) - Gather from memory",
    ]
    for f in simd_funcs:

        print(f"  {f}")
    print("\n=== STRING ARRAY FUNCTIONS ===")
    str_arr_funcs = [
        "str_array_new(cap)  - Create string array",
        "str_array_len(arr)  - Get length",
        "str_array_push(a,s) - Append string",
        "str_array_get(a, i) - Get string at index",
        "str_array_set(a,i,s)- Set string at index",
        "str_array_pop(arr)  - Remove last string",
        "str_array_join(a,s) - Join with separator (O(n); --backend=c only)",
    ]
    for f in str_arr_funcs:

        print(f"  {f}")
    print("\n=== ARENA ALLOCATOR ===")
    arena_funcs = [
        "arena_create(size)  - Create bump allocator",
        "arena_alloc(a, sz)  - Allocate from arena",
        "arena_reset(a)      - Reset (free all at once)",
        "arena_destroy(a)    - Destroy arena",
        "arena_used(a)       - Bytes used",
        "arena_remaining(a)  - Bytes remaining",
        "arena_use(a)        - Set active request arena",
    ]
    for f in arena_funcs:

        print(f"  {f}")
    print("\n=== DICTIONARY FUNCTIONS ===")
    dict_funcs = [
        "dict_new()         - Create dictionary",
        "dict_has_key(d, k)  - Check key exists",
        "dict_size(d)        - Number of entries",
        "dict_remove(d, k)   - Remove entry",
        "dict_key_at(d, i)   - Key at index",
        "dict_value_at(d, i) - Value at index",
        "dict_get_type(d, k) - Type of value",
        "dict_get_string(d,k)- Get string value",
    ]
    for f in dict_funcs:

        print(f"  {f}")
    print("\n=== FUNCTION POINTERS ===")
    fn_funcs = [
        "fn_ptr(name)        - Get function pointer",
        "fn_call(p, args...) - Call (returns int)",
        "fn_call_str(p, ...) - Call (returns string)",
    ]
    for f in fn_funcs:

        print(f"  {f}")
    print("\n=== SQL (SQLITE) ===")
    sql_funcs = [
        "sql_open(path)      - Open database",
        "sql_exec(db, query) - Execute statement",
        "sql_prepare(db, q)  - Prepare statement",
        "sql_bind_int(s,i,v) - Bind integer parameter",
        "sql_bind_text(s,i,t)- Bind text parameter",
        "sql_bind_text_i64(s,i,p,v) - Bind p + int without heap string",
        "sql_bind_text_i64_parts(s,i,p,v,x) - Bind p + int + x",
        "sql_bind_null(s,i)  - Bind NULL parameter",
        "sql_clear_bindings(s) - Clear prepared bindings",
        "sql_step(stmt)      - Step prepared statement",
        "sql_reset(stmt)     - Reset prepared statement",
        "sql_column_int(s,i) - Read integer column",
        "sql_column_text(s,i)- Read text column",
        "sql_finalize(stmt)  - Finalize prepared statement",
        "sql_close(db)       - Close database",
    ]
    for f in sql_funcs:

        print(f"  {f}")
    print("\n=== SYSTEM FUNCTIONS ===")
    sys_funcs = [
        "time_ms()           - Milliseconds since epoch",
        "time_ns()           - Nanoseconds since epoch",
        "clock_ns()          - High-precision clock",
        "rdtsc()             - CPU timestamp counter",
        "num_cpus()          - Number of CPU cores",
        "argc()              - Command line arg count",
        "argv(i)             - Get command line arg",
        "system(cmd)         - Execute shell command",
        "process_capture(cmd)- Execute command and capture stdout string",
        "process_run_argv(args) - Execute argv vector, return exit status",
        "process_run_argv_redirs(args,ops,targets) - Run argv with redirections",
        "process_run_argv_env_redirs(args,env,ops,targets) - Run argv with env and redirections",
        "process_spawn_argv_env_redirs(args,env,ops,targets) - Spawn argv asynchronously",
        "process_spawn_argv_env_redirs_pgrp(args,env,ops,targets,pgid) - Spawn argv in a POSIX process group",
        "process_wait_pid(pid) - Wait for spawned process and return exit status",
        "process_wait_pid_event(pid) - Wait and report stopped children as negative signal events",
        "process_poll_pid(pid) - Poll spawned process; -1 means still running",
        "process_kill_pid(pid,signal) - Signal or terminate a process",
        "process_get_pgrp(pid) - POSIX process group id, negative errno on failure",
        "process_set_pgrp(pid,pgid) - POSIX setpgid wrapper",
        "process_kill_pgrp(pgid,signal) - Signal a POSIX process group",
        "terminal_get_pgrp(fd) - POSIX foreground terminal process group",
        "terminal_set_pgrp(fd,pgid) - POSIX foreground terminal handoff",
        "process_exec_replace_argv_env_redirs(args,env,ops,targets) - Host exec replacement where supported",
        "process_capture_argv_env_redirs(args,env,ops,targets) - Capture argv stdout",
        "process_last_capture_status() - Last argv stdout capture exit status",
        "process_set_last_capture_status(status) - Override last argv capture status",
        "process_last_exec_errno() - Last native exec failure errno",
        "process_errno_enoexec() - Native ENOEXEC value",
        "process_errno_enoent() - Native ENOENT value",
        "process_errno_eacces() - Native EACCES value",
        "process_errno_eperm() - Native EPERM value",
        "process_pipe_argv_redirs(l,lo,lt,r,ro,rt) - Run argv pipe",
        "process_pipeline_argv_redirs(args,counts,ops,targets,rcounts) - Run argv pipeline",
        "process_pipeline_argv_env_redirs(args,counts,env,ops,targets,rcounts) - Run argv pipeline with env",
        "process_spawn_pipeline_argv_env_redirs_pgrp(args,counts,env,ops,targets,rcounts,pgid) - Spawn argv pipeline in a POSIX process group",
        "process_capture_pipeline_argv_redirs(args,counts,ops,targets,rcounts) - Capture argv pipeline stdout",
        "process_capture_pipeline_argv_env_redirs(args,counts,env,ops,targets,rcounts) - Capture argv pipeline stdout with env",
        "signal_install(n)   - Install native pending-signal handler",
        "signal_ignore(n)    - Ignore native signal where supported",
        "signal_default(n)   - Restore native signal default where supported",
        "signal_pending()    - First pending native signal number, non-destructive",
        "signal_clear(n)     - Clear one pending native signal",
        "signal_drain()      - Drain first pending native signal number",
        "signal_raise(n)     - Raise native signal in current process",
        "getpid()            - Current process id",
        "getppid()           - Parent process id (POSIX, -ENOSYS elsewhere)",
        "getuid/geteuid()    - User/effective user id (POSIX)",
        "getgid/getegid()    - Group/effective group id (POSIX)",
        "errno_get()         - Current hosted libc errno",
        "errno_clear()       - Clear hosted libc errno",
        "errno_set(value)    - Set hosted libc errno",
        "syscall(n, ...)     - Native target syscall, returns int",
        "win32_load_library(name) - Load DLL, returns handle",
        "win32_get_proc_address(h,name) - Get FARPROC as int handle",
        "win32_free_library(h) - Release DLL handle",
        "win32_get_last_error() - Get Win32 error code",
        "win32_utf16_from_utf8(s) - Allocate UTF-16 string for Win32 APIs",
        "win32_local_free(p) - Release LocalFree-owned Win32 memory",
        "win32_shell_execute_runas(exe,args) - Relaunch through ShellExecuteW/runas",
        "win32_is_user_admin() - Check elevated/admin token through Shell32",
        "win32_full_path(path) - Return heap-owned absolute Windows path",
        "win32_hcs_*()        - Typed Hyper-V HCS lifecycle helpers",
        "typeof(x)           - Get type as string",
        "target_os()         - Compile target OS name",
        "target_backend()    - Compile backend family",
    ]
    for f in sys_funcs:

        print(f"  {f}")
    print("\n=== BIT MANIPULATION ===")
    bit_funcs = [
        "popcount(x)         - Count set bits",
        "clz(x)              - Count leading zeros",
        "ctz(x)              - Count trailing zeros",
        "band, bor, bxor, bnot - Bitwise operators",
        "shl, shr, ushr      - Shift operators",
        "nand, nor, xnor     - Logic gate operators",
    ]
    for f in bit_funcs:

        print(f"  {f}")
    print("\n=== LOW-LEVEL (FREESTANDING) ===")
    low_funcs = [
        "outb(port, val)     - x86 port output",
        "inb(port)           - x86 port input",
        'asm "..." end      - Inline assembly block',
    ]
    for f in low_funcs:

        print(f"  {f}")
    print("\n=== DECORATORS ===")
    decorators = [
        "@inline             - Force function inlining",
        "@noinline           - Prevent inlining",
        "@pure               - No side effects",
        "@unchecked          - Disable safety checks",
        "@noalias            - No pointer aliasing",
        "@fastmath           - Allow FP approximations",
        "@bounded(N)         - Loop runs at most N times",
        "@export             - External linkage (FFI)",
        '@abi("ret", ...)    - Exact exported ABI signature metadata',
        '@cabi("ret", ...)   - Compatibility alias for @abi',
        '@library("name")   - Mark as library module',
    ]
    for d in decorators:

        print(f"  {d}")
    print("\n=== CONTROL FLOW KEYWORDS ===")
    keywords = [
        "if/then/elsif/else/end",
        "unless (negated if)",
        "while/then/end",
        "until (negated while)",
        "do/while (body-first)",
        "for/in",
        "foreach/in/then",
        "loop/end (infinite)",
        "repeat N times/end",
        "match/case/default/end",
        "break",
        "continue",
        "return",
        "try/catch/finally/end",
        "assert",
    ]
    for k in keywords:

        print(f"  {k}")
    print("\n=== DEFINITIONS ===")
    defs = [
        "def name(args): rettype ... end",
        "type name(args): ... end  - Type-prefix function, e.g. int main():",
        "void main(): ... end      - No-argv/no-return entrypoint",
        "async def ... end  - Async function",
        "test name ... end  - Test function",
        "record Name then fields end",
        "class Name then methods end",
        "enum Name then variants end",
        "union Name then fields end",
        "type Alias = Type  - Type alias",
        "typedef Type Alias  - C-style type alias",
        "extern fn name(p): ret  - FFI declaration",
        "extern var name: type   - External variable",
    ]
    for d in defs:

        print(f"  {d}")
    print("\n=== ABI HEADER BLOCKS ===")
    cabi_header = [
        'abi header "path.h": ... end   - Generated ABI bridge header',
        'cabi header "path.h": ... end  - Compatibility alias',
        "guard NAME                  - Optional include guard",
        "include <system.h>          - Emit system include",
        'include "local.h"           - Emit local include',
        "include_next <system.h>     - Emit compiler include_next",
        "define NAME = VALUE         - Emit #define NAME VALUE",
        "ifdef/ifndef/if ...:        - Emit scoped preprocessor conditionals",
        "typedef Name = Type         - Emit C typedef",
        "struct Name: ... end        - Emit C struct declaration",
        "prototype ret name(args)    - Emit C function prototype; supports ...",
        "static inline ret name(args): c_emit ... end - Emit static inline C helper",
        "macro NAME(args): c_emit ... end - Emit tiny function-like macro",
    ]
    for item in cabi_header:

        print(f"  {item}")
    print(
        "\n  Note: header/guard/define/prototype/macro/c_emit are scoped to "
        "`abi header` blocks, not general AILang keywords."
    )
    print("\n" + "=" * 70)
    print("For full documentation, see: docs/AILANG_COMPREHENSIVE_REFERENCE.md")
    print("=" * 70)

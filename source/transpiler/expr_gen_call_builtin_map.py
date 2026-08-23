"""Builtin call mappings for the C expression emitter."""

from __future__ import annotations

from typing import Any, Callable

from transpiler.expr_gen_call_fd import fd_c_builtin_mappings
from transpiler.expr_gen_call_process import process_c_builtin_mappings
from transpiler.expr_gen_call_win32 import win32_c_builtin_mappings


def _emit_ptr_array(args: list[str]) -> str:
    """Emit a null-terminated pointer array with enclosing-block lifetime."""
    entries = ", ".join(f"(const char *)(uintptr_t)({arg})" for arg in args)
    if entries:
        entries = f"{entries}, "
    return f"((int64_t)(uintptr_t)((const char *[]){{{entries}NULL}}))"


def _emit_index_of(args: list[str]) -> str:
    if len(args) == 2:
        haystack, needle = args
        return f"ailang_index_of({haystack}, {needle})"
    if len(args) == 3:
        haystack, needle, start = args
        return f"ailang_index_of_from({haystack}, {needle}, {start})"
    raise ValueError(
        f"index_of() expects (haystack, needle) or (haystack, needle, start), got {len(args)} args"
    )


def c_builtin_mappings(self: Any) -> dict[str, Callable[[list[str]], str]]:
    return {
        "strlen": lambda a: f"ailang_strlen({a[0]})",
        "len": lambda a: f"ailang_strlen({a[0]})",
        "char_at": lambda a: (
            f"char_at({a[0]}, {a[1]}, {a[2]})"
            if len(a) >= 3
            else f"char_at({a[0]}, {a[1]}, -1LL)"
        ),
        "unsafe_char_at": lambda a: f"unsafe_char_at({a[0]}, {a[1]})",
        "time_ns": lambda a: "time_ns()",
        "clock_ns": lambda a: "clock_ns()",
        "abs": lambda a: f"ailang_rt_abs({a[0]})",
        "min": lambda a: f"(({a[0]}) < ({a[1]}) ? ({a[0]}) : ({a[1]}))",
        "max": lambda a: f"(({a[0]}) > ({a[1]}) ? ({a[0]}) : ({a[1]}))",
        "int": lambda a: f"((int64_t)({a[0]}))",
        "float": lambda a: f"((double)({a[0]}))",
        "str": lambda a: f"ailang_int_to_str({a[0]})",
        "sqrt": lambda a: f"sqrt({a[0]})",
        "pow": lambda a: f"pow((double)({a[0]}), (double)({a[1]}))",
        # String functions
        "ord": lambda a: f"((int64_t)(unsigned char)({a[0]})[0])",
        "chr": lambda a: f"ailang_chr({a[0]})",
        "substr": lambda a: (
            f"ailang_substr({a[0]}, {a[1]}, {a[2]})"
            if len(a) >= 3
            else f"ailang_substr({a[0]}, {a[1]}, -1LL)"
        ),
        # String concatenation (supports 2-5+ args)
        "concat": lambda a: (
            a[0]
            if len(a) == 1
            else (
                f"ailang_concat2({a[0]}, {a[1]})"
                if len(a) == 2
                else (
                    f"ailang_concat3({a[0]}, {a[1]}, {a[2]})"
                    if len(a) == 3
                    else (
                        f"ailang_concat4({a[0]}, {a[1]}, {a[2]}, {a[3]})"
                        if len(a) == 4
                        else (
                            f"ailang_concat2(ailang_concat4({a[0]}, {a[1]}, {a[2]}, {a[3]}), {a[4]})"
                            if len(a) == 5
                            else (
                                f"ailang_concat2(ailang_concat4({a[0]}, {a[1]}, {a[2]}, {a[3]}), "
                                f"ailang_concat{min(len(a)-4, 4)}({', '.join(a[4:8])}))"
                            )
                        )
                    )
                )
            )
        ),
        # String search
        "index_of": _emit_index_of,
        "index_of_from": lambda a: (f"ailang_index_of_from({a[0]}, {a[1]}, {a[2]})"),
        # String prefix/suffix checks
        "startswith": lambda a: f"ailang_startswith({a[0]}, {a[1]})",
        "endswith": lambda a: f"ailang_endswith({a[0]}, {a[1]})",
        # String replace
        "str_replace": lambda a: f"ailang_str_replace({a[0]}, {a[1]}, {a[2]})",
        "streq": lambda a: f"(__ailang_strcmp_raw({a[0]}, {a[1]}) == 0 ? 1 : 0)",
        # Pointer arithmetic
        # ptr_add / ptr_sub: AILang treats pointers as int64. Do
        # the byte arithmetic via char* and cast back so the result
        # plays cleanly with the rest of the int64 ABI.
        "ptr_add": lambda a: (
            f"((int64_t)(uintptr_t)((const char *)(uintptr_t)({a[0]}) + ({a[1]})))"
        ),
        "ptr_sub": lambda a: (
            f"((int64_t)(uintptr_t)((const char *)(uintptr_t)({a[0]}) - ({a[1]})))"
        ),
        "ptr_array": _emit_ptr_array,
        # sizeof / alignof intrinsics
        "sizeof": lambda a: self._emit_sizeof(a[0]),
        "alignof": lambda a: self._emit_alignof(a[0]),
        # Bit operations. clz/ctz on 0 are undefined for the GCC
        # builtins, so guard the input — return 64 for a zero word.
        "popcount": lambda a: f"((int64_t)__builtin_popcountll((uint64_t)({a[0]})))",
        "clz": lambda a: (
            f"((int64_t)((uint64_t)({a[0]}) ?"
            f" __builtin_clzll((uint64_t)({a[0]})) : 64))"
        ),
        "ctz": lambda a: (
            f"((int64_t)((uint64_t)({a[0]}) ?"
            f" __builtin_ctzll((uint64_t)({a[0]})) : 64))"
        ),
        # Time functions
        "time_ms": lambda a: "time_ms()",
        # Dynamic array functions
        "array_new": lambda a: f"array_new({a[0]})" if a else "array_new(8)",
        "array_push": lambda a: f"array_push({a[0]}, {a[1]})",
        "array_pop": lambda a: f"array_pop(&{a[0]})",
        "array_get": lambda a: f"array_get({a[0]}, {a[1]})",
        "array_set": lambda a: f"array_set({a[0]}, {a[1]}, {a[2]})",
        "array_len": lambda a: f"array_len({a[0]})",
        "array_cap": lambda a: f"array_cap({a[0]})",
        # Base conversion (return strings)
        "hex": lambda a: f"ailang_to_hex({a[0]})",
        "bin": lambda a: f"ailang_to_bin({a[0]})",
        "oct": lambda a: f"ailang_to_oct({a[0]})",
        # SQLite FFI
        "sql_open": lambda a: f"sql_open({a[0]})",
        "sql_open_readonly": lambda a: f"sql_open_readonly({a[0]})",
        "sql_last_open_status": lambda a: "sql_last_open_status()",
        "sql_exec": lambda a: f"sql_exec({a[0]}, {a[1]})",
        "sql_close": lambda a: f"sql_close({a[0]})",
        "sql_prepare": lambda a: f"sql_prepare({a[0]}, {a[1]})",
        "sql_step": lambda a: f"sql_step({a[0]})",
        "sql_bind_int": lambda a: f"sql_bind_int({a[0]}, {a[1]}, {a[2]})",
        "sql_bind_text": lambda a: f"sql_bind_text({a[0]}, {a[1]}, {a[2]})",
        "sql_bind_text_i64": lambda a: (
            f"sql_bind_text_i64({a[0]}, {a[1]}, {a[2]}, {a[3]})"
        ),
        "sql_bind_text_i64_parts": lambda a: (
            f"sql_bind_text_i64_parts({a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]})"
        ),
        "sql_bind_null": lambda a: f"sql_bind_null({a[0]}, {a[1]})",
        "sql_clear_bindings": lambda a: f"sql_clear_bindings({a[0]})",
        "sql_column_int": lambda a: f"sql_column_int({a[0]}, {a[1]})",
        "sql_column_text": lambda a: f"sql_column_text({a[0]}, {a[1]})",
        "sql_finalize": lambda a: f"sql_finalize({a[0]})",
        # File operations (POSIX aliases resolve to the same C helpers)
        "file_exists": lambda a: f"file_exists({a[0]})",
        "file_can_execute": lambda a: f"file_can_execute({a[0]})",
        "file_is_regular": lambda a: f"file_is_regular({a[0]})",
        "file_is_symlink": lambda a: f"file_is_symlink({a[0]})",
        "file_is_block": lambda a: f"file_is_block({a[0]})",
        "file_is_char": lambda a: f"file_is_char({a[0]})",
        "file_is_fifo": lambda a: f"file_is_fifo({a[0]})",
        "file_is_socket": lambda a: f"file_is_socket({a[0]})",
        "file_is_setuid": lambda a: f"file_is_setuid({a[0]})",
        "file_is_setgid": lambda a: f"file_is_setgid({a[0]})",
        "file_mtime": lambda a: f"file_mtime({a[0]})",
        "file_same": lambda a: f"file_same({a[0]}, {a[1]})",
        "fd_is_tty": lambda a: f"fd_is_tty({a[0]})",
        "current_dir": lambda a: "current_dir()",
        "change_dir": lambda a: f"change_dir({a[0]})",
        "list_dir": lambda a: f"list_dir({a[0]})",
        "access": lambda a: (
            f"ailang_access({a[0]}, {a[1]})"
            if len(a) > 1
            else f"ailang_access({a[0]}, 0)"
        ),
        "make_dir": lambda a: f"make_dir({a[0]})",
        "mkdir": lambda a: f"make_dir({a[0]})",
        "delete_file": lambda a: f"delete_file({a[0]})",
        "unlink": lambda a: f"delete_file({a[0]})",
        "move_file": lambda a: f"move_file({a[0]}, {a[1]})",
        "rename": lambda a: f"move_file({a[0]}, {a[1]})",
        **fd_c_builtin_mappings(),
        # String split and parse functions
        "split": lambda a: (
            f"split({a[0]}, {a[1]})" if len(a) > 1 else f'split({a[0]}, " ")'
        ),
        "split_ints": lambda a: (
            f"split_ints({a[0]}, {a[1]})" if len(a) > 1 else f'split_ints({a[0]}, " ")'
        ),
        "parse_int": lambda a: f"parse_int({a[0]})",
        # Atomic operations (C11 stdatomic)
        "atomic_load": lambda a: f"ailang_atomic_load(&{a[0]})",
        "atomic_store": lambda a: f"(ailang_atomic_store(&{a[0]}, {a[1]}), 0)",
        "atomic_add": lambda a: f"ailang_atomic_add(&{a[0]}, {a[1]})",
        "atomic_sub": lambda a: f"ailang_atomic_sub(&{a[0]}, {a[1]})",
        "atomic_exchange": lambda a: f"ailang_atomic_exchange(&{a[0]}, {a[1]})",
        "atomic_compare_exchange": lambda a: f"ailang_atomic_cas(&{a[0]}, {a[1]}, {a[2]})",
        # Channel operations
        "channel": lambda a: f"ailang_channel_create({a[1] if len(a) > 1 else '1'})",
        "channel_send": lambda a: f"(ailang_channel_send({a[0]}, {a[1]}), 0)",
        "channel_recv": lambda a: f"ailang_channel_recv({a[0]})",
        "channel_try_send": lambda a: f"ailang_channel_try_send({a[0]}, {a[1]})",
        "channel_close": lambda a: f"(ailang_channel_close({a[0]}), 0)",
        # Synchronization primitives (Ada/SPARK-inspired)
        "mutex_create": lambda a: "ailang_mutex_create()",
        "mutex_lock": lambda a: f"(ailang_mutex_lock({a[0]}), 0)",
        "mutex_unlock": lambda a: f"(ailang_mutex_unlock({a[0]}), 0)",
        "mutex_destroy": lambda a: f"(ailang_mutex_destroy({a[0]}), 0)",
        "cond_create": lambda a: "ailang_cond_create()",
        "cond_wait": lambda a: f"(ailang_cond_wait({a[0]}, {a[1]}), 0)",
        "cond_signal": lambda a: f"(ailang_cond_signal({a[0]}), 0)",
        "cond_broadcast": lambda a: f"(ailang_cond_broadcast({a[0]}), 0)",
        "cond_destroy": lambda a: f"(ailang_cond_destroy({a[0]}), 0)",
        "rwlock_create": lambda a: "ailang_rwlock_create()",
        "rwlock_read_lock": lambda a: f"(ailang_rwlock_read_lock({a[0]}), 0)",
        "rwlock_write_lock": lambda a: f"(ailang_rwlock_write_lock({a[0]}), 0)",
        "rwlock_read_unlock": lambda a: f"(ailang_rwlock_read_unlock({a[0]}), 0)",
        "rwlock_write_unlock": lambda a: f"(ailang_rwlock_write_unlock({a[0]}), 0)",
        "rwlock_destroy": lambda a: f"(ailang_rwlock_destroy({a[0]}), 0)",
        # Command-line arguments. argv returns a borrowed libc
        # `const char *` — leave it as a pointer so `strlen(argv(0))`
        # works. The corresponding return-type inference below
        # exposes it as `char *` to user code, matching how
        # `tcp_recv` / `read_file` / etc. flow through the pipeline.
        "argc": lambda a: "ailang_argc()",
        "argv": lambda a: f"ailang_argv({a[0]})",
        # getenv: borrowed libc pointer. Empty string if unset (so
        # `strlen(getenv("X"))` is safe). Do NOT free — not in
        # _STRING_OWNING_CALLS, so auto-cleanup correctly skips it.
        "getenv": lambda a: f"ailang_getenv({a[0]})",
        # x86 timestamp counter. `__rdtsc()` is the standard
        # intrinsic on both gcc/clang (via <x86intrin.h>) and
        # MSVC. Cast to int64 for AILang's ABI.
        "rdtsc": lambda a: "((int64_t)__rdtsc())",
        # File I/O - new binary functions
        "file_size": lambda a: f"file_size({a[0]})",
        "read_bytes": lambda a: f"read_bytes({a[0]}, (int64_t *)({a[1]}))",
        "write_bytes": lambda a: f"write_bytes({a[0]}, {a[1]}, {a[2]})",
        # Low-level memory functions
        "alloc": lambda a: f"((int64_t)(uintptr_t)ailang_safe_malloc((size_t)({a[0]})))",
        "dealloc": lambda a: (
            f"(({a[0]}) != 0 ? "
            f"(ailang_safe_free((void *)(uintptr_t)({a[0]})), 0) : 0)"
        ),
        "malloc": lambda a: f"((int64_t)(uintptr_t)ailang_safe_malloc((size_t)({a[0]})))",
        "free": lambda a: f"(ailang_safe_free((void *)(uintptr_t)({a[0]})), 0)",
        # Peek/poke for raw memory access (null-guarded)
        "peek64": lambda a: f"ailang_peek64({a[0]}, {a[1]})",
        "poke64": lambda a: f"ailang_poke64({a[0]}, {a[1]}, {a[2]})",
        "peek": lambda a: f"ailang_peek64({a[0]}, 0)",
        "poke": lambda a: f"ailang_poke64({a[0]}, 0, {a[1]}), 0",
        # 32-bit memory access (framebuffers, pixel arrays)
        "peek32": lambda a: f"ailang_peek32({a[0]}, {a[1]})",
        "poke32": lambda a: f"ailang_poke32({a[0]}, {a[1]}, {a[2]})",
        # 8-bit memory access (bytes, characters)
        "peek8": lambda a: f"ailang_peek8({a[0]}, {a[1]})",
        "poke8": lambda a: f"ailang_poke8({a[0]}, {a[1]}, {a[2]})",
        # addressof — get pointer to a variable as int64_t
        "addressof": lambda a: f"((int64_t)(uintptr_t)&({a[0]}))",
        # memcpy — raw memory copy
        "memcpy": lambda a: (
            f"(memcpy((void *)(uintptr_t)({a[0]}),"
            f" (void *)(uintptr_t)({a[1]}),"
            f" (size_t)({a[2]})), 0)"
        ),
        # memset — fill memory with a byte value
        "memset": lambda a: (
            f"(memset((void *)(uintptr_t)({a[0]}),"
            f" (int)({a[1]}),"
            f" (size_t)({a[2]})), 0)"
        ),
        # memmove — copy with overlap support
        "memmove": lambda a: (
            f"(memmove((void *)(uintptr_t)({a[0]}),"
            f" (void *)(uintptr_t)({a[1]}),"
            f" (size_t)({a[2]})), 0)"
        ),
        # realloc — resize allocation. Route through the tracked
        # ailang_safe_realloc so the counter sees both the freed
        # old size and the new alloc; otherwise the matching
        # `dealloc` later subtracts bytes the tracker never added.
        "realloc": lambda a: (
            f"((int64_t)(uintptr_t)ailang_safe_realloc("
            f"(void *)(uintptr_t)({a[0]}), (size_t)({a[1]})))"
        ),
        # calloc — zero-initialized allocation. Same tracker
        # routing; we go through ailang_safe_calloc which is
        # safe_malloc + memset.
        "calloc": lambda a: (
            f"((int64_t)(uintptr_t)ailang_safe_calloc("
            f"(size_t)({a[0]}), (size_t)({a[1]})))"
        ),
        # as_class — cast i64 to struct pointer
        "as_class": lambda a: f"(({a[1].strip().strip(chr(34))} *)((void *)(uintptr_t)({a[0]})))",
        # Threading utilities
        "thread_id": lambda a: "ailang_thread_id()",
        "num_cpus": lambda a: "ailang_num_cpus()",
        "yield_thread": lambda a: "ailang_yield_thread()",
        "sleep_ms": lambda a: f"ailang_sleep_ms({a[0]})",
        # Split accessors
        "split_len": lambda a: f"({a[0]}).length",
        "split_get": lambda a: f"({a[0]}).data[{a[1]}]",
        # The C-side StringArray struct (emitted by the split runtime)
        # uses `data` for its char** field, not `strings`. The mismatch
        # silently emitted unbuildable C any time a program used
        # split_str_get on a split() result -- which adapt_serve does
        # via qrax_encode_seq.
        "split_str_get": lambda a: f"({a[0]}).data[{a[1]}]",
        "split_set": lambda a: f"(({a[0]}).data[{a[1]}] = {a[2]}, 0)",
        # System command
        "system": lambda a: f"ailang_system({a[0]})",
        "process_capture": lambda a: f"ailang_process_capture({a[0]})",
        **process_c_builtin_mappings(),
        "errno_get": lambda a: "ailang_errno_get()",
        "errno_clear": lambda a: "ailang_errno_clear()",
        "errno_set": lambda a: f"ailang_errno_set({a[0]})",
        # Native syscall trap. The public AILang surface is syscall(n, ...);
        # fixed-slot padding is hidden in this generated call boundary.
        "syscall": lambda a: self._emit_syscall_call(a),
        # TCP sockets (Winsock2 on Windows, BSD sockets on POSIX)
        "tcp_connect": lambda a: f"ailang_tcp_connect({a[0]}, {a[1]})",
        "tcp_listen": lambda a: f"ailang_tcp_listen({a[0]})",
        "tcp_accept": lambda a: f"ailang_tcp_accept({a[0]})",
        "tcp_recv": lambda a: f"ailang_tcp_recv({a[0]}, {a[1]})",
        "tcp_send": lambda a: f"ailang_tcp_send({a[0]}, {a[1]})",
        "tcp_close": lambda a: f"ailang_tcp_close({a[0]})",
        **win32_c_builtin_mappings(),
        # Arena functions
        "arena_create": lambda a: f"(int64_t)(uintptr_t)arena_create({a[0]})",
        "arena_alloc": lambda a: f"(int64_t)(uintptr_t)arena_alloc((void *)(uintptr_t)({a[0]}), {a[1]})",
        "arena_reset": lambda a: f"arena_reset((void *)(uintptr_t)({a[0]}))",
        "arena_destroy": lambda a: f"arena_destroy((void *)(uintptr_t)({a[0]}))",
        "arena_used": lambda a: f"arena_used((void *)(uintptr_t)({a[0]}))",
        "arena_remaining": lambda a: f"arena_remaining((void *)(uintptr_t)({a[0]}))",
        # arena_use(handle): set the active per-request arena. Pass 0 to
        # restore default malloc routing. Subsequent ailang_strcat /
        # substr / chr / int_to_str / concat / tcp_recv calls allocate
        # from the arena, so a single arena_reset reclaims everything.
        # Emit as a void-cast assignment so it works as a statement
        # under -Werror=unused-value.
        "arena_use": lambda a: (
            f"((void)(__ailang_request_arena = (void *)(uintptr_t)({a[0]})))"
        ),
        # mem_used(): live heap bytes (allocated - freed). Useful for
        # self-reporting in leak audits without needing external tooling.
        "mem_used": lambda a: (
            "((int64_t)(__ailang_total_allocated - __ailang_total_freed))"
        ),
        # Dict introspection. dict_has and dict_has_key are
        # aliases — both check key presence. dict_get_string casts
        # the int64 value back to const char * (callers have
        # already stored a heap pointer there).
        "dict_size": lambda a: f"dict_size_fn({a[0]})",
        "dict_new": lambda a: "dict_new()",
        "dict_get": lambda a: f"dict_get({a[0]}, {a[1]})",
        "dict_has": lambda a: f"dict_has_fn({a[0]}, {a[1]})",
        "dict_has_key": lambda a: f"dict_has_fn({a[0]}, {a[1]})",
        "dict_key_at": lambda a: f"dict_key_at({a[0]}, {a[1]})",
        "dict_value_at": lambda a: f"dict_value_at({a[0]}, {a[1]})",
        "dict_remove": lambda a: f"dict_remove_fn({a[0]}, {a[1]})",
        "dict_get_type": lambda a: f"dict_get_type_fn({a[0]}, {a[1]})",
        "dict_get_string": lambda a: (
            f"((const char*)(uintptr_t)dict_get({a[0]}, {a[1]}))"
        ),
        # String arrays
        "str_array_new": lambda a: f"str_array_new_fn({a[0]})",
        "str_array_len": lambda a: f"str_array_len_fn({a[0]})",
        "str_array_push": lambda a: f"str_array_push_fn({a[0]}, {a[1]})",
        "str_array_get": lambda a: f"str_array_get_fn({a[0]}, {a[1]})",
        "str_array_set": lambda a: f"str_array_set_fn({a[0]}, {a[1]}, {a[2]})",
        "str_array_pop": lambda a: f"str_array_pop_fn({a[0]})",
        "str_array_join": lambda a: f"str_array_join_fn({a[0]}, {a[1]})",
        # Container-free primitives (added 30-04-2026 / 16:35 for the
        # AILang-Pure self-host port). Without these, a function that
        # returns a record with a `str_array` / `array` field can't be
        # cleaned up at the call site — the auto-cleanup at scope exit
        # only fires for arrays held in their own local, not for arrays
        # nested inside a returned record. Callers in the in-process
        # parser->lexer wiring need explicit free for tokenize()'s
        # returned token storage. The runtime functions
        # `ailang_str_array_free`, `ailang_int_array_free`,
        # `ailang_dyn_array_free` already exist; these builtins just
        # expose them at the AILang surface. Take address-of the local
        # because the runtime helpers take pointer-to-struct so they
        # can null the data field after free, preventing double-free.
        # `str_array_new()` builds an `ailang_str_array` (borrowed
        # string elements), not a `StringArray` (owned elements). The
        # `_v2` free matches the AILang-surface str_array's semantics:
        # free the array storage, leave elements alone (caller manages
        # element lifetimes separately if needed).
        "dealloc_str_array": lambda a: f"ailang_str_array_free_v2(&{a[0]})",
        "dealloc_int_array": lambda a: f"ailang_int_array_free(&{a[0]})",
        "dealloc_array": lambda a: f"ailang_dyn_array_free(&{a[0]})",
        # Function pointers
        "fn_ptr": lambda a: self._emit_typed_fn_ptr(a),
        "fn_call": lambda a: self._emit_typed_fn_call(a, "int64_t"),
        "fn_call_str": lambda a: self._emit_fn_call(a, "const char*"),
    }

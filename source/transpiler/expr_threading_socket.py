"""Socket builtins for ExprBuiltinThreadingEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ExprGenError


def _socket_is_windows(self) -> bool:
    """True if the current LLVM target triple targets Windows."""
    return "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"


def _socket_handle_ty(self) -> ir.IntType:
    """LLVM IR type for a socket handle.

    Windows SOCKET is UINT_PTR (i64 on x64). POSIX socket fds are
    int (i32). Getting this wrong silently corrupts handles, so
    keep it isolated in one helper.
    """
    return ir.IntType(64) if self._socket_is_windows() else ir.IntType(32)


def _socket_handle_to_i64(self, handle: ir.Value) -> ir.Value:
    """Sign-extend a socket handle to i64 for storage in AILang ints.

    On Windows the handle is already i64; on POSIX it's i32 and
    sign-extension preserves the -1 error sentinel.
    """
    if self._socket_is_windows():
        return handle
    return self.builder.sext(handle, ir.IntType(64), name="sock_i64")


def _socket_invalid_const(self) -> ir.Constant:
    """Sentinel returned by socket()/accept() on failure.

    Windows: INVALID_SOCKET = (SOCKET)(~0) = 0xFFFFFFFFFFFFFFFF, which
    as a *signed* i64 is -1. POSIX: -1 as i32. Comparing as signed
    works for both shapes.
    """
    return ir.Constant(self._socket_handle_ty(), -1)


def _ensure_wsa_init(self) -> None:
    """Emit a one-time WSAStartup call (Windows only).

    Idempotent via a module-level i32 flag - first socket call on
    Windows checks the flag, runs WSAStartup if zero, then sets it.
    Subsequent calls fall straight through. POSIX path is a no-op
    because BSD sockets need no per-process initialization.

    WSACleanup is intentionally not paired here; process exit
    tears everything down. v1 trades the cleanup for simpler IR.
    """
    if not self._socket_is_windows():
        return

    flag_name = "_ailang_wsa_initialized"
    flag = self.codegen.module.globals.get(flag_name)
    if flag is None:
        flag = ir.GlobalVariable(self.codegen.module, ir.IntType(32), flag_name)
        flag.linkage = "internal"
        flag.initializer = ir.Constant(ir.IntType(32), 0)

    init_block = self.function.append_basic_block("wsa_init")
    after_block = self.function.append_basic_block("wsa_after")

    flag_val = self.builder.load(flag, name="wsa_flag")
    is_zero = self.builder.icmp_signed(
        "==", flag_val, ir.Constant(ir.IntType(32), 0), name="wsa_needs_init"
    )
    self.builder.cbranch(is_zero, init_block, after_block)

    self.builder.position_at_end(init_block)
    # WSAStartup(MAKEWORD(2,2)=0x0202, &wsadata). WSADATA is 408
    # bytes on Win64; we allocate it and discard the contents.
    wsa_data = self.builder.alloca(ir.ArrayType(ir.IntType(8), 408), name="wsa_data")
    wsa_data_ptr = self.builder.bitcast(wsa_data, ir.IntType(8).as_pointer())
    wsa_ty = ir.FunctionType(
        ir.IntType(32), [ir.IntType(16), ir.IntType(8).as_pointer()]
    )
    wsa_fn = self.codegen._declare_external("WSAStartup", wsa_ty)
    self.builder.call(wsa_fn, [ir.Constant(ir.IntType(16), 0x0202), wsa_data_ptr])
    self.builder.store(ir.Constant(ir.IntType(32), 1), flag)
    self.builder.branch(after_block)

    self.builder.position_at_end(after_block)


def _emit_htons(self, port_i64: ir.Value) -> ir.Value:
    """Convert a port (host order, i64) to network byte order (i16).

    Inlined byte-swap rather than calling htons() - avoids a libc
    dependency that's named differently across platforms (htons
    is a macro on glibc, an extern on musl, in ws2_32 on Windows).
    Both supported targets are little-endian, so the swap is
    unconditional.
    """
    port_i16 = self.builder.trunc(port_i64, ir.IntType(16), name="port_i16")
    low = self.builder.and_(port_i16, ir.Constant(ir.IntType(16), 0xFF))
    high = self.builder.lshr(port_i16, ir.Constant(ir.IntType(16), 8))
    high_masked = self.builder.and_(high, ir.Constant(ir.IntType(16), 0xFF))
    low_shifted = self.builder.shl(low, ir.Constant(ir.IntType(16), 8))
    return self.builder.or_(low_shifted, high_masked, name="port_be")


def _emit_socket_close_native(self, handle: ir.Value) -> None:
    """Emit close/closesocket for a platform-native socket handle."""
    sock_ty = self._socket_handle_ty()
    close_name = "closesocket" if self._socket_is_windows() else "close"
    close_ty = ir.FunctionType(ir.IntType(32), [sock_ty])
    close_fn = self.codegen._declare_external(close_name, close_ty)
    self.builder.call(close_fn, [handle])


def _emit_socket_nodelay(self, handle: ir.Value) -> None:
    """Best-effort TCP_NODELAY for tiny request/response protocols."""
    sock_ty = self._socket_handle_ty()
    setsockopt_ty = ir.FunctionType(
        ir.IntType(32),
        [
            sock_ty,
            ir.IntType(32),
            ir.IntType(32),
            ir.IntType(8).as_pointer(),
            ir.IntType(32),
        ],
    )
    setsockopt_fn = self.codegen._declare_external("setsockopt", setsockopt_ty)
    one_slot = self.builder.alloca(ir.IntType(32), name="tcp_nodelay_slot")
    self.builder.store(ir.Constant(ir.IntType(32), 1), one_slot)
    one_ptr = self.builder.bitcast(
        one_slot, ir.IntType(8).as_pointer(), name="tcp_nodelay_ptr"
    )
    self.builder.call(
        setsockopt_fn,
        [
            handle,
            ir.Constant(ir.IntType(32), 6),  # IPPROTO_TCP
            ir.Constant(ir.IntType(32), 1),  # TCP_NODELAY
            one_ptr,
            ir.Constant(ir.IntType(32), 4),
        ],
    )


def _emit_libc_memset(self, ptr: ir.Value, size: int) -> None:
    """Zero a raw byte buffer with libc memset."""
    memset_fn = self.codegen.module.globals.get("memset")
    if memset_fn is None:
        memset_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [
                ir.IntType(8).as_pointer(),
                ir.IntType(32),
                ir.IntType(64),
            ],
        )
        memset_fn = ir.Function(self.codegen.module, memset_ty, "memset")
    self.builder.call(
        memset_fn,
        [
            ptr,
            ir.Constant(ir.IntType(32), 0),
            ir.Constant(ir.IntType(64), size),
        ],
    )


def _build_sockaddr_in(
    self, port_i64: ir.Value, addr_i32: ir.Value | None = None
) -> ir.Value:
    """Allocate and populate a sockaddr_in on the stack.

    Returns an i8* to a 16-byte structure:
      offset 0:  sin_family   = AF_INET (2)            i16
      offset 2:  sin_port     = htons(port_i64)        i16
      offset 4:  sin_addr     = addr_i32 / INADDR_ANY  i32
      offset 8:  sin_zero[8]  = 0                      [8 x i8]

    The layout is RFC-fixed and identical on Windows / Linux / BSD,
    so we don't branch on platform here. Caller passes the i8* into
    bind() with size=16.
    """
    sockaddr_buf = self.builder.alloca(
        ir.ArrayType(ir.IntType(8), 16), name="sockaddr_buf"
    )
    sockaddr_ptr = self.builder.bitcast(
        sockaddr_buf, ir.IntType(8).as_pointer(), name="sockaddr_ptr"
    )

    # Zero the whole 16 bytes via memset, then overwrite the two
    # fields we care about. Cheaper IR than four separate stores.
    self._emit_libc_memset(sockaddr_ptr, 16)

    # sin_family at offset 0 (i16 = 2 = AF_INET)
    family_ptr = self.builder.bitcast(
        sockaddr_ptr, ir.IntType(16).as_pointer(), name="sin_family_ptr"
    )
    self.builder.store(ir.Constant(ir.IntType(16), 2), family_ptr)

    # sin_port at offset 2 (i16, network byte order)
    port_be = self._emit_htons(port_i64)
    port_byte_ptr = self.builder.gep(
        sockaddr_buf,
        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 2)],
        name="sin_port_byte_ptr",
    )
    port_i16_ptr = self.builder.bitcast(
        port_byte_ptr, ir.IntType(16).as_pointer(), name="sin_port_ptr"
    )
    self.builder.store(port_be, port_i16_ptr)

    if addr_i32 is not None:
        addr_byte_ptr = self.builder.gep(
            sockaddr_buf,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 4)],
            name="sin_addr_byte_ptr",
        )
        addr_i32_ptr = self.builder.bitcast(
            addr_byte_ptr, ir.IntType(32).as_pointer(), name="sin_addr_ptr"
        )
        self.builder.store(addr_i32, addr_i32_ptr)

    return sockaddr_ptr


def _builtin_tcp_listen(self, args) -> ir.Value:
    """tcp_listen(port) -> int handle (0 on error).

    Opens a socket, binds it to 127.0.0.1:port, and starts listening
    with backlog=16. Mirrors the C backend's ailang_tcp_listen.
    """
    if len(args) != 1:
        raise ExprGenError("tcp_listen() expects (port)")
    port_val = self.generate_expr(args[ARG_FIRST])

    self._ensure_wsa_init()

    sock_ty = self._socket_handle_ty()
    socket_ty = ir.FunctionType(
        sock_ty, [ir.IntType(32), ir.IntType(32), ir.IntType(32)]
    )
    socket_fn = self.codegen._declare_external("socket", socket_ty)

    # AF_INET=2, SOCK_STREAM=1, protocol=0
    s = self.builder.call(
        socket_fn,
        [
            ir.Constant(ir.IntType(32), 2),
            ir.Constant(ir.IntType(32), 1),
            ir.Constant(ir.IntType(32), 0),
        ],
        name="sock_fd",
    )

    is_err = self.builder.icmp_signed(
        "==", s, self._socket_invalid_const(), name="sock_err"
    )
    err_block = self.function.append_basic_block("listen_sock_err")
    ok_block = self.function.append_basic_block("listen_sock_ok")
    merge_block = self.function.append_basic_block("listen_merge")
    self.builder.cbranch(is_err, err_block, ok_block)

    self.builder.position_at_end(err_block)
    self.builder.branch(merge_block)
    err_end = self.builder.block

    self.builder.position_at_end(ok_block)
    loopback_addr = ir.Constant(ir.IntType(32), 0x0100007F)
    sockaddr_ptr = self._build_sockaddr_in(port_val, loopback_addr)

    bind_ty = ir.FunctionType(
        ir.IntType(32), [sock_ty, ir.IntType(8).as_pointer(), ir.IntType(32)]
    )
    bind_fn = self.codegen._declare_external("bind", bind_ty)
    self.builder.call(bind_fn, [s, sockaddr_ptr, ir.Constant(ir.IntType(32), 16)])

    listen_ty = ir.FunctionType(ir.IntType(32), [sock_ty, ir.IntType(32)])
    listen_fn = self.codegen._declare_external("listen", listen_ty)
    self.builder.call(listen_fn, [s, ir.Constant(ir.IntType(32), 16)])

    s_i64 = self._socket_handle_to_i64(s)
    self.builder.branch(merge_block)
    ok_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(ir.IntType(64), name="listen_handle")
    phi.add_incoming(ir.Constant(ir.IntType(64), 0), err_end)
    phi.add_incoming(s_i64, ok_end)
    return phi


def _builtin_tcp_accept(self, args) -> ir.Value:
    """tcp_accept(listener) -> int handle (0 on error)."""
    if len(args) != 1:
        raise ExprGenError("tcp_accept() expects (listener)")
    listener = self.generate_expr(args[ARG_FIRST])

    sock_ty = self._socket_handle_ty()
    # Truncate the listener back from i64 to the platform-native
    # handle width before passing to accept().
    if not self._socket_is_windows():
        listener_native = self.builder.trunc(
            listener, ir.IntType(32), name="listener_i32"
        )
    else:
        listener_native = listener  # already i64 on Windows

    accept_ty = ir.FunctionType(
        sock_ty,
        [sock_ty, ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
    )
    accept_fn = self.codegen._declare_external("accept", accept_ty)
    null_ptr = ir.Constant(ir.IntType(8).as_pointer(), None)
    c = self.builder.call(
        accept_fn, [listener_native, null_ptr, null_ptr], name="conn_fd"
    )

    is_err = self.builder.icmp_signed(
        "==", c, self._socket_invalid_const(), name="accept_err"
    )
    err_block = self.function.append_basic_block("accept_err")
    ok_block = self.function.append_basic_block("accept_ok")
    merge_block = self.function.append_basic_block("accept_merge")
    self.builder.cbranch(is_err, err_block, ok_block)

    self.builder.position_at_end(err_block)
    self.builder.branch(merge_block)
    err_end = self.builder.block

    self.builder.position_at_end(ok_block)
    c_i64 = self._socket_handle_to_i64(c)
    self.builder.branch(merge_block)
    ok_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(ir.IntType(64), name="accept_handle")
    phi.add_incoming(ir.Constant(ir.IntType(64), 0), err_end)
    phi.add_incoming(c_i64, ok_end)
    return phi


def _builtin_tcp_recv(self, args) -> ir.Value:
    """tcp_recv(conn, max_bytes) -> string (empty on close/error).

    Allocates a fresh max_bytes+1 buffer, calls recv, null-terminates
    at the byte count returned. Returns an empty string ("") if recv
    returns <= 0 (peer closed or socket error).
    """
    if len(args) != 2:
        raise ExprGenError("tcp_recv() expects (conn, max_bytes)")
    conn = self.generate_expr(args[ARG_FIRST])
    max_bytes = self.generate_expr(args[ARG_SECOND])

    sock_ty = self._socket_handle_ty()
    if not self._socket_is_windows():
        conn_native = self.builder.trunc(conn, ir.IntType(32), name="conn_i32")
    else:
        conn_native = conn

    # Allocate buffer of max_bytes + 1 (room for null terminator).
    one64 = ir.Constant(ir.IntType(64), 1)
    buf_size = self.builder.add(max_bytes, one64, name="recv_buf_size")
    buf = self.codegen.string_alloc(buf_size, "recv_buf")

    # recv() signature differs subtly: Windows uses int for length
    # and return (i32); POSIX uses size_t / ssize_t (i64). Keep it
    # i32 on Windows, i64 on POSIX so the IR matches what the
    # platform's <sys/socket.h> / <winsock2.h> declare.
    if self._socket_is_windows():
        recv_ty = ir.FunctionType(
            ir.IntType(32),
            [
                sock_ty,
                ir.IntType(8).as_pointer(),
                ir.IntType(32),
                ir.IntType(32),
            ],
        )
        len_arg = self.builder.trunc(max_bytes, ir.IntType(32), name="recv_len_i32")
    else:
        recv_ty = ir.FunctionType(
            ir.IntType(64),
            [
                sock_ty,
                ir.IntType(8).as_pointer(),
                ir.IntType(64),
                ir.IntType(32),
            ],
        )
        len_arg = max_bytes
    recv_fn = self.codegen._declare_external("recv", recv_ty)
    n = self.builder.call(
        recv_fn,
        [conn_native, buf, len_arg, ir.Constant(ir.IntType(32), 0)],
        name="recv_n",
    )

    # Normalize n to i64 for downstream use.
    if self._socket_is_windows():
        n_i64 = self.builder.sext(n, ir.IntType(64), name="recv_n_i64")
    else:
        n_i64 = n

    # if n > 0: null-terminate at offset n; else: null-terminate at 0
    zero64 = ir.Constant(ir.IntType(64), 0)
    is_err = self.builder.icmp_signed("<=", n_i64, zero64, name="recv_failed")
    write_pos = self.builder.select(is_err, zero64, n_i64, name="recv_write_pos")
    null_byte_ptr = self.builder.gep(buf, [write_pos], name="recv_null_pos")
    self.builder.store(ir.Constant(ir.IntType(8), 0), null_byte_ptr)

    return buf


def _builtin_tcp_send(self, args) -> ir.Value:
    """tcp_send(conn, data) -> int (bytes sent).

    Sends until all of data is written or send() returns <= 0.
    Returns the total bytes successfully sent (may be < strlen(data)
    on partial failure).
    """
    if len(args) != 2:
        raise ExprGenError("tcp_send() expects (conn, data)")
    conn = self.generate_expr(args[ARG_FIRST])
    data = self.generate_expr(args[ARG_SECOND])

    sock_ty = self._socket_handle_ty()
    if not self._socket_is_windows():
        conn_native = self.builder.trunc(conn, ir.IntType(32), name="conn_i32")
    else:
        conn_native = conn

    # total = strlen(data)
    total = self.builder.call(self.codegen.get_strlen(), [data], name="send_total")

    # send() signature, like recv, uses int on Windows / ssize_t on
    # POSIX. Same i32/i64 split.
    if self._socket_is_windows():
        send_ty = ir.FunctionType(
            ir.IntType(32),
            [
                sock_ty,
                ir.IntType(8).as_pointer(),
                ir.IntType(32),
                ir.IntType(32),
            ],
        )
    else:
        send_ty = ir.FunctionType(
            ir.IntType(64),
            [
                sock_ty,
                ir.IntType(8).as_pointer(),
                ir.IntType(64),
                ir.IntType(32),
            ],
        )
    send_fn = self.codegen._declare_external("send", send_ty)

    # sent = alloca i64 = 0
    sent_alloca = self.builder.alloca(ir.IntType(64), name="sent_slot")
    self.builder.store(ir.Constant(ir.IntType(64), 0), sent_alloca)

    loop_cond = self.function.append_basic_block("send_cond")
    loop_body = self.function.append_basic_block("send_body")
    loop_end = self.function.append_basic_block("send_end")
    self.builder.branch(loop_cond)

    self.builder.position_at_end(loop_cond)
    sent = self.builder.load(sent_alloca, name="sent_cur")
    more_to_send = self.builder.icmp_signed("<", sent, total, name="send_more")
    self.builder.cbranch(more_to_send, loop_body, loop_end)

    self.builder.position_at_end(loop_body)
    # remaining = total - sent
    remaining = self.builder.sub(total, sent, name="send_remaining")
    # data + sent
    chunk_ptr = self.builder.gep(data, [sent], name="send_chunk_ptr")

    if self._socket_is_windows():
        remaining_arg = self.builder.trunc(
            remaining, ir.IntType(32), name="send_rem_i32"
        )
    else:
        remaining_arg = remaining
    n = self.builder.call(
        send_fn,
        [conn_native, chunk_ptr, remaining_arg, ir.Constant(ir.IntType(32), 0)],
        name="send_n",
    )
    if self._socket_is_windows():
        n_i64 = self.builder.sext(n, ir.IntType(64), name="send_n_i64")
    else:
        n_i64 = n

    # if n <= 0: break out of the loop; else: sent += n
    zero64 = ir.Constant(ir.IntType(64), 0)
    is_err = self.builder.icmp_signed("<=", n_i64, zero64, name="send_failed")
    bail_block = self.function.append_basic_block("send_bail")
    cont_block = self.function.append_basic_block("send_continue")
    self.builder.cbranch(is_err, bail_block, cont_block)

    self.builder.position_at_end(bail_block)
    self.builder.branch(loop_end)

    self.builder.position_at_end(cont_block)
    new_sent = self.builder.add(sent, n_i64, name="sent_new")
    self.builder.store(new_sent, sent_alloca)
    self.builder.branch(loop_cond)

    self.builder.position_at_end(loop_end)
    return self.builder.load(sent_alloca, name="send_total_sent")


def _builtin_tcp_close(self, args) -> ir.Value:
    """tcp_close(handle) -> int (always 0).

    Closes a listener or connection. Different symbol on each
    platform: closesocket() on Windows, close() on POSIX. Both
    take the platform-native handle width and return int.
    """
    if len(args) != 1:
        raise ExprGenError("tcp_close() expects (handle)")
    handle = self.generate_expr(args[ARG_FIRST])

    # Skip if handle == 0 (matches the C backend's null-guard)
    zero64 = ir.Constant(ir.IntType(64), 0)
    is_zero = self.builder.icmp_signed("==", handle, zero64, name="close_is_zero")
    skip_block = self.function.append_basic_block("close_skip")
    do_block = self.function.append_basic_block("close_do")
    merge_block = self.function.append_basic_block("close_merge")
    self.builder.cbranch(is_zero, skip_block, do_block)

    self.builder.position_at_end(skip_block)
    self.builder.branch(merge_block)

    self.builder.position_at_end(do_block)
    sock_ty = self._socket_handle_ty()
    if not self._socket_is_windows():
        handle_native = self.builder.trunc(
            handle, ir.IntType(32), name="close_handle_i32"
        )
        close_name = "close"
    else:
        handle_native = handle
        close_name = "closesocket"
    close_ty = ir.FunctionType(ir.IntType(32), [sock_ty])
    close_fn = self.codegen._declare_external(close_name, close_ty)
    self.builder.call(close_fn, [handle_native])
    self.builder.branch(merge_block)

    self.builder.position_at_end(merge_block)
    return ir.Constant(ir.IntType(64), 0)


# ------------------------------------------------------------------
# Memory Allocation (RAII-compatible)
# ------------------------------------------------------------------

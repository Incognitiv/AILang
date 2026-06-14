"""LLVM tcp_connect lowering for ExprBuiltinThreadingEmitter."""

from __future__ import annotations

from typing import Any

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ExprGenError


def _addrinfo_layout(self) -> dict[str, Any]:
    """Return the x64 C ABI layout used by struct addrinfo."""
    triple = self.codegen.module.triple.lower()
    bsd_like = any(
        marker in triple
        for marker in ("freebsd", "openbsd", "netbsd", "dragonfly", "darwin")
    )
    windows = self._socket_is_windows()
    addr_after_canon = windows or bsd_like
    return {
        "size": 48,
        "family": 4,
        "socktype": 8,
        "protocol": 12,
        "addrlen": 16,
        "addr": 32 if addr_after_canon else 24,
        "next": 40,
        "addrlen_ty": ir.IntType(64) if windows else ir.IntType(32),
    }


def _emit_i32_store_at(self, base: ir.Value, offset: int, value: int) -> None:
    """Store a 32-bit integer into a raw byte buffer at a C ABI offset."""
    byte_ptr = self.builder.gep(
        base, [ir.Constant(ir.IntType(64), offset)], name="i32_field_byte"
    )
    i32_ptr = self.builder.bitcast(
        byte_ptr, ir.IntType(32).as_pointer(), name="i32_field_ptr"
    )
    self.builder.store(ir.Constant(ir.IntType(32), value), i32_ptr)


def _emit_load_at(
    self, base: ir.Value, offset: int, value_ty: ir.Type, name: str
) -> ir.Value:
    """Load a typed value from a raw byte buffer at a C ABI offset."""
    byte_ptr = self.builder.gep(
        base, [ir.Constant(ir.IntType(64), offset)], name=f"{name}_byte"
    )
    typed_ptr = self.builder.bitcast(
        byte_ptr, value_ty.as_pointer(), name=f"{name}_ptr"
    )
    return self.builder.load(typed_ptr, name=name)


def _builtin_tcp_connect(self, args) -> ir.Value:
    """tcp_connect(host, port) -> int handle (0 on error).

    Mirrors the C backend: getaddrinfo(), iterate every candidate address,
    try socket/connect, close failed sockets, freeaddrinfo() on all resolved
    paths, then return the connected handle or 0.
    """
    if len(args) != 2:
        raise ExprGenError("tcp_connect() expects (host, port)")
    host = self.generate_expr(args[ARG_FIRST])
    port_val = self.generate_expr(args[ARG_SECOND])

    self._ensure_wsa_init()

    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    sock_ty = self._socket_handle_ty()
    null_i8 = ir.Constant(i8_ptr, None)
    zero64 = ir.Constant(i64, 0)

    host_null = self.builder.icmp_unsigned(
        "==", host, null_i8, name="tcp_connect_host_null"
    )
    port_low = self.builder.icmp_signed(
        "<=", port_val, zero64, name="tcp_connect_port_low"
    )
    port_high = self.builder.icmp_signed(
        ">", port_val, ir.Constant(i64, 65535), name="tcp_connect_port_high"
    )
    bad_port = self.builder.or_(port_low, port_high, name="tcp_connect_bad_port")
    bad_input = self.builder.or_(host_null, bad_port, name="tcp_connect_bad_input")
    input_err_block = self.function.append_basic_block("tcp_connect_input_err")
    input_ok_block = self.function.append_basic_block("tcp_connect_input_ok")
    merge_block = self.function.append_basic_block("tcp_connect_merge")
    self.builder.cbranch(bad_input, input_err_block, input_ok_block)

    self.builder.position_at_end(input_err_block)
    self.builder.branch(merge_block)
    input_err_end = self.builder.block

    self.builder.position_at_end(input_ok_block)
    layout = self._addrinfo_layout()
    hints_buf = self.builder.alloca(
        ir.ArrayType(i8, int(layout["size"])), name="addrinfo_hints"
    )
    hints_ptr = self.builder.bitcast(hints_buf, i8_ptr, name="addrinfo_hints_ptr")
    self._emit_libc_memset(hints_ptr, int(layout["size"]))
    self._emit_i32_store_at(hints_ptr, int(layout["socktype"]), 1)

    port_buf = self.builder.alloca(ir.ArrayType(i8, 32), name="tcp_port_text")
    port_text = self.builder.bitcast(port_buf, i8_ptr, name="tcp_port_text_ptr")
    self.builder.call(self.codegen.get_i64_to_cstr_func(), [port_text, port_val])

    result_slot = self.builder.alloca(i8_ptr, name="addrinfo_result_slot")
    self.builder.store(null_i8, result_slot)
    getaddrinfo_ty = ir.FunctionType(i32, [i8_ptr, i8_ptr, i8_ptr, i8_ptr.as_pointer()])
    getaddrinfo_fn = self.codegen._declare_external("getaddrinfo", getaddrinfo_ty)
    gai_rc = self.builder.call(
        getaddrinfo_fn,
        [host, port_text, hints_ptr, result_slot],
        name="tcp_connect_gai",
    )
    gai_failed = self.builder.icmp_signed(
        "!=", gai_rc, ir.Constant(i32, 0), name="tcp_connect_gai_failed"
    )
    gai_err_block = self.function.append_basic_block("tcp_connect_gai_err")
    gai_ok_block = self.function.append_basic_block("tcp_connect_gai_ok")
    self.builder.cbranch(gai_failed, gai_err_block, gai_ok_block)

    self.builder.position_at_end(gai_err_block)
    self.builder.branch(merge_block)
    gai_err_end = self.builder.block

    self.builder.position_at_end(gai_ok_block)
    result = self.builder.load(result_slot, name="tcp_connect_result")
    item_slot = self.builder.alloca(i8_ptr, name="addrinfo_item_slot")
    self.builder.store(result, item_slot)
    found_slot = self.builder.alloca(i64, name="tcp_connect_found_slot")
    self.builder.store(zero64, found_slot)

    loop_cond = self.function.append_basic_block("tcp_connect_ai_cond")
    loop_body = self.function.append_basic_block("tcp_connect_ai_body")
    loop_next = self.function.append_basic_block("tcp_connect_ai_next")
    cleanup_block = self.function.append_basic_block("tcp_connect_cleanup")
    self.builder.branch(loop_cond)

    self.builder.position_at_end(loop_cond)
    item = self.builder.load(item_slot, name="tcp_connect_ai")
    has_item = self.builder.icmp_unsigned(
        "!=", item, null_i8, name="tcp_connect_ai_has_item"
    )
    self.builder.cbranch(has_item, loop_body, cleanup_block)

    self.builder.position_at_end(loop_body)
    family = self._emit_load_at(item, int(layout["family"]), i32, "ai_family")
    socktype = self._emit_load_at(item, int(layout["socktype"]), i32, "ai_socktype")
    protocol = self._emit_load_at(item, int(layout["protocol"]), i32, "ai_protocol")
    addrlen_ty = layout["addrlen_ty"]
    addrlen_raw = self._emit_load_at(
        item, int(layout["addrlen"]), addrlen_ty, "ai_addrlen"
    )
    if getattr(addrlen_raw.type, "width", 0) == 64:
        addrlen = self.builder.trunc(addrlen_raw, i32, name="ai_addrlen_i32")
    else:
        addrlen = addrlen_raw
    addr = self._emit_load_at(item, int(layout["addr"]), i8_ptr, "ai_addr")

    socket_ty = ir.FunctionType(sock_ty, [i32, i32, i32])
    socket_fn = self.codegen._declare_external("socket", socket_ty)
    sock = self.builder.call(
        socket_fn, [family, socktype, protocol], name="tcp_connect_sock"
    )
    sock_err = self.builder.icmp_signed(
        "==", sock, self._socket_invalid_const(), name="tcp_connect_sock_err"
    )
    sock_err_block = self.function.append_basic_block("tcp_connect_sock_err")
    sock_ok_block = self.function.append_basic_block("tcp_connect_sock_ok")
    self.builder.cbranch(sock_err, sock_err_block, sock_ok_block)

    self.builder.position_at_end(sock_err_block)
    self.builder.branch(loop_next)

    self.builder.position_at_end(sock_ok_block)
    connect_ty = ir.FunctionType(i32, [sock_ty, i8_ptr, i32])
    connect_fn = self.codegen._declare_external("connect", connect_ty)
    rc = self.builder.call(connect_fn, [sock, addr, addrlen])
    connect_failed = self.builder.icmp_signed(
        "!=", rc, ir.Constant(i32, 0), name="tcp_connect_failed"
    )
    connect_err_block = self.function.append_basic_block("tcp_connect_err")
    connect_ok_block = self.function.append_basic_block("tcp_connect_ok")
    self.builder.cbranch(connect_failed, connect_err_block, connect_ok_block)

    self.builder.position_at_end(connect_err_block)
    self._emit_socket_close_native(sock)
    self.builder.branch(loop_next)

    self.builder.position_at_end(connect_ok_block)
    self._emit_socket_nodelay(sock)
    sock_i64 = self._socket_handle_to_i64(sock)
    self.builder.store(sock_i64, found_slot)
    self.builder.branch(cleanup_block)

    self.builder.position_at_end(loop_next)
    next_item = self._emit_load_at(item, int(layout["next"]), i8_ptr, "ai_next")
    self.builder.store(next_item, item_slot)
    self.builder.branch(loop_cond)

    self.builder.position_at_end(cleanup_block)
    freeaddrinfo_ty = ir.FunctionType(ir.VoidType(), [i8_ptr])
    freeaddrinfo_fn = self.codegen._declare_external("freeaddrinfo", freeaddrinfo_ty)
    self.builder.call(freeaddrinfo_fn, [result])
    found = self.builder.load(found_slot, name="tcp_connect_found")
    self.builder.branch(merge_block)
    cleanup_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(i64, name="tcp_connect_handle")
    phi.add_incoming(zero64, input_err_end)
    phi.add_incoming(zero64, gai_err_end)
    phi.add_incoming(found, cleanup_end)
    return phi

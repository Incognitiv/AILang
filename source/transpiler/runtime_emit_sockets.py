"""Runtime emitter helpers for socket runtime helpers."""

from __future__ import annotations

__all__ = ["emit_runtime_sockets"]

from typing import Any


def emit_runtime_sockets(emitter: Any) -> None:
    """Emit TCP socket runtime helpers.

    Cross-platform: Winsock2 on Windows (auto-links ws2_32 via
    pragma comment), BSD sockets on POSIX. Six primitives:
    tcp_connect, tcp_listen, tcp_accept, tcp_recv, tcp_send, tcp_close.

    Strings flow as null-terminated UTF-8: tcp_send takes a const
    char *, tcp_recv returns a heap-allocated char * (caller owns;
    ailang_safe_malloc'd, same lifetime contract as read_file).

    Errors collapse to 0 or empty string rather than raising Python
    exceptions. Callers check by comparing the handle to 0 or the
    string to "".
    """
    if "sockets" not in emitter._needs.helpers:
        return
    emitter._output.append("/* TCP socket runtime helpers */")
    emitter._output.append("#ifndef AILANG_FREESTANDING")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("#include <winsock2.h>")
    emitter._output.append("#include <ws2tcpip.h>")
    # `#pragma comment(lib, ...)` is MSVC-only; gcc warns under
    # -Wunknown-pragmas. Guard so MinGW/Clang ignore it cleanly.
    emitter._output.append("#ifdef _MSC_VER")
    emitter._output.append('#pragma comment(lib, "ws2_32.lib")')
    emitter._output.append("#endif")
    emitter._output.append("typedef SOCKET ailang_socket_t;")
    emitter._output.append("typedef int ailang_socklen_t;")
    emitter._output.append("#define AILANG_INVALID_SOCK INVALID_SOCKET")
    emitter._output.append("static int _ailang_wsa_initialized = 0;")
    emitter._output.append("static void _ailang_wsa_init(void) {")
    emitter._output.append("    if (!_ailang_wsa_initialized) {")
    emitter._output.append("        WSADATA wsa;")
    emitter._output.append("        if (WSAStartup(MAKEWORD(2, 2), &wsa) == 0) {")
    emitter._output.append("            _ailang_wsa_initialized = 1;")
    emitter._output.append("        }")
    emitter._output.append("    }")
    emitter._output.append("}")
    emitter._output.append(
        "static void _ailang_sock_close(ailang_socket_t s) { closesocket(s); }"
    )
    emitter._output.append("#else")
    emitter._output.append("#include <netdb.h>")
    emitter._output.append("#include <sys/socket.h>")
    emitter._output.append("#include <netinet/in.h>")
    emitter._output.append("#include <netinet/tcp.h>")
    emitter._output.append("#include <arpa/inet.h>")
    emitter._output.append("#include <errno.h>")
    emitter._output.append("#include <fcntl.h>")
    emitter._output.append("#include <sys/select.h>")
    emitter._output.append("#include <unistd.h>")
    emitter._output.append("typedef int ailang_socket_t;")
    emitter._output.append("typedef socklen_t ailang_socklen_t;")
    emitter._output.append("#define AILANG_INVALID_SOCK (-1)")
    emitter._output.append("static void _ailang_wsa_init(void) {}")
    emitter._output.append(
        "static void _ailang_sock_close(ailang_socket_t s) { close(s); }"
    )
    emitter._output.append("#endif")
    emitter._output.append(
        "static int _ailang_sock_set_nonblocking(ailang_socket_t s, int enabled) {"
    )
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    u_long mode = enabled ? 1UL : 0UL;")
    emitter._output.append("    return ioctlsocket(s, FIONBIO, &mode) == 0 ? 0 : -1;")
    emitter._output.append("#else")
    emitter._output.append("    int flags = fcntl(s, F_GETFL, 0);")
    emitter._output.append("    if (flags < 0) return -1;")
    emitter._output.append(
        "    if (enabled) flags |= O_NONBLOCK; else flags &= ~O_NONBLOCK;"
    )
    emitter._output.append("    return fcntl(s, F_SETFL, flags);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append(
        "static int _ailang_sock_connect_timeout(ailang_socket_t s,"
        " const struct sockaddr *addr, ailang_socklen_t addrlen, int timeout_ms) {"
    )
    emitter._output.append("    if (timeout_ms <= 0) timeout_ms = 1000;")
    emitter._output.append(
        "    if (_ailang_sock_set_nonblocking(s, 1) != 0) return -1;"
    )
    emitter._output.append("    int rc = connect(s, addr, addrlen);")
    emitter._output.append("    if (rc == 0) {")
    emitter._output.append("        (void)_ailang_sock_set_nonblocking(s, 0);")
    emitter._output.append("        return 0;")
    emitter._output.append("    }")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    int pending = WSAGetLastError() == WSAEWOULDBLOCK;")
    emitter._output.append("#else")
    emitter._output.append("    int pending = errno == EINPROGRESS;")
    emitter._output.append("#endif")
    emitter._output.append("    if (!pending) {")
    emitter._output.append("        (void)_ailang_sock_set_nonblocking(s, 0);")
    emitter._output.append("        return -1;")
    emitter._output.append("    }")
    emitter._output.append("    fd_set writefds;")
    emitter._output.append("    FD_ZERO(&writefds);")
    emitter._output.append("    FD_SET(s, &writefds);")
    emitter._output.append("    struct timeval tv;")
    emitter._output.append("    tv.tv_sec = timeout_ms / 1000;")
    emitter._output.append("    tv.tv_usec = (timeout_ms % 1000) * 1000;")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    rc = select(0, NULL, &writefds, NULL, &tv);")
    emitter._output.append("#else")
    emitter._output.append("    rc = select(s + 1, NULL, &writefds, NULL, &tv);")
    emitter._output.append("#endif")
    emitter._output.append("    if (rc <= 0) {")
    emitter._output.append("        (void)_ailang_sock_set_nonblocking(s, 0);")
    emitter._output.append("        return -1;")
    emitter._output.append("    }")
    emitter._output.append("    int err = 0;")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    int err_len = (int)sizeof(err);")
    emitter._output.append(
        "    rc = getsockopt(s, SOL_SOCKET, SO_ERROR, (char *)&err, &err_len);"
    )
    emitter._output.append("#else")
    emitter._output.append("    socklen_t err_len = (socklen_t)sizeof(err);")
    emitter._output.append(
        "    rc = getsockopt(s, SOL_SOCKET, SO_ERROR, &err, &err_len);"
    )
    emitter._output.append("#endif")
    emitter._output.append("    (void)_ailang_sock_set_nonblocking(s, 0);")
    emitter._output.append("    if (rc != 0 || err != 0) return -1;")
    emitter._output.append("    return 0;")
    emitter._output.append("}")
    emitter._output.append("static void _ailang_sock_nodelay(ailang_socket_t s) {")
    emitter._output.append("    int one = 1;")
    emitter._output.append(
        "    (void)setsockopt(s, IPPROTO_TCP, TCP_NODELAY,"
        " (const char *)&one, sizeof(one));"
    )
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_tcp_connect("
        "const char *host, int64_t port) {"
    )
    emitter._output.append("    _ailang_wsa_init();")
    emitter._output.append("    if (!host || port <= 0 || port > 65535) return 0;")
    emitter._output.append("    struct addrinfo hints;")
    emitter._output.append("    struct addrinfo *result = NULL;")
    emitter._output.append("    struct addrinfo *item = NULL;")
    emitter._output.append("    char port_text[32];")
    emitter._output.append("    ailang_socket_t s = AILANG_INVALID_SOCK;")
    emitter._output.append("    memset(&hints, 0, sizeof(hints));")
    emitter._output.append("    hints.ai_family = AF_UNSPEC;")
    emitter._output.append("    hints.ai_socktype = SOCK_STREAM;")
    emitter._output.append(
        '    snprintf(port_text, sizeof(port_text), "%lld", (long long)port);'
    )
    emitter._output.append(
        "    if (getaddrinfo(host, port_text, &hints, &result) != 0) return 0;"
    )
    emitter._output.append(
        "    for (item = result; item != NULL; item = item->ai_next) {"
    )
    emitter._output.append(
        "        s = socket(item->ai_family, item->ai_socktype, item->ai_protocol);"
    )
    emitter._output.append("        if (s == AILANG_INVALID_SOCK) continue;")
    emitter._output.append(
        "        if (_ailang_sock_connect_timeout(s, item->ai_addr, "
        "(ailang_socklen_t)item->ai_addrlen, 1000) == 0) {"
    )
    emitter._output.append("            _ailang_sock_nodelay(s);")
    emitter._output.append("            break;")
    emitter._output.append("        }")
    emitter._output.append("        _ailang_sock_close(s);")
    emitter._output.append("        s = AILANG_INVALID_SOCK;")
    emitter._output.append("    }")
    emitter._output.append("    freeaddrinfo(result);")
    emitter._output.append("    if (s == AILANG_INVALID_SOCK) return 0;")
    emitter._output.append("    return (int64_t)s;")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_tcp_listen(int64_t port) {"
    )
    emitter._output.append("    _ailang_wsa_init();")
    emitter._output.append("    ailang_socket_t s = socket(AF_INET, SOCK_STREAM, 0);")
    emitter._output.append("    if (s == AILANG_INVALID_SOCK) return 0;")
    emitter._output.append("    int yes = 1;")
    emitter._output.append(
        "    setsockopt(s, SOL_SOCKET, SO_REUSEADDR,"
        " (const char *)&yes, sizeof(yes));"
    )
    emitter._output.append("    struct sockaddr_in addr;")
    emitter._output.append("    memset(&addr, 0, sizeof(addr));")
    emitter._output.append("    addr.sin_family = AF_INET;")
    emitter._output.append("    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);")
    emitter._output.append("    addr.sin_port = htons((unsigned short)port);")
    emitter._output.append(
        "    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {"
    )
    emitter._output.append("        _ailang_sock_close(s); return 0;")
    emitter._output.append("    }")
    emitter._output.append("    if (listen(s, 16) != 0) {")
    emitter._output.append("        _ailang_sock_close(s); return 0;")
    emitter._output.append("    }")
    emitter._output.append("    _ailang_sock_nodelay(s);")
    emitter._output.append("    return (int64_t)s;")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_tcp_accept(int64_t listener) {"
    )
    emitter._output.append(
        "    ailang_socket_t s = accept((ailang_socket_t)listener, NULL, NULL);"
    )
    emitter._output.append("    if (s == AILANG_INVALID_SOCK) return 0;")
    emitter._output.append("    _ailang_sock_nodelay(s);")
    emitter._output.append("    return (int64_t)s;")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static char *ailang_tcp_recv("
        "int64_t conn, int64_t max_bytes) {"
    )
    emitter._output.append("    if (max_bytes <= 0) {")
    emitter._output.append("        char *e = (char *)ailang_request_alloc(1);")
    emitter._output.append("        if (e) e[0] = '\\0';")
    emitter._output.append("        return e;")
    emitter._output.append("    }")
    emitter._output.append(
        "    char *buf = (char *)ailang_request_alloc((size_t)max_bytes + 1);"
    )
    emitter._output.append("    if (!buf) return NULL;")
    emitter._output.append(
        "    int n = recv((ailang_socket_t)conn, buf, (int)max_bytes, 0);"
    )
    emitter._output.append("    if (n <= 0) { buf[0] = '\\0'; return buf; }")
    emitter._output.append("    buf[n] = '\\0';")
    emitter._output.append("    return buf;")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_tcp_send("
        "int64_t conn, const char *data) {"
    )
    emitter._output.append("    if (!data) return 0;")
    emitter._output.append("    size_t total = strlen(data);")
    emitter._output.append("    size_t sent = 0;")
    emitter._output.append("    while (sent < total) {")
    emitter._output.append(
        "        int n = send((ailang_socket_t)conn, data + sent,"
        " (int)(total - sent), 0);"
    )
    emitter._output.append("        if (n <= 0) break;")
    emitter._output.append("        sent += (size_t)n;")
    emitter._output.append("    }")
    emitter._output.append("    return (int64_t)sent;")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static void ailang_tcp_close(int64_t handle) {"
    )
    emitter._output.append(
        "    if (handle != 0) _ailang_sock_close((ailang_socket_t)handle);"
    )
    emitter._output.append("}")
    emitter._output.append("#endif /* !AILANG_FREESTANDING */")
    emitter._output.append("")


# Keep strict static checks from reporting this module as dead code when it is only
# consumed through import wiring from ``runtime_emitter``.
_exported_emit_runtime_sockets = emit_runtime_sockets

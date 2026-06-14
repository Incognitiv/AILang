"""C runtime emitter for process builtins."""

from __future__ import annotations

from typing import Any

from transpiler.runtime_emit_process_capture_pipeline import (
    _emit_process_capture_pipeline_argv_redirs,
)
from transpiler.runtime_emit_process_io import (
    _emit_process_capture_argv_env_redirs,
    _emit_process_pipe_argv_redirs,
    _emit_process_pipeline_argv_redirs,
)
from transpiler.runtime_emit_process_lifecycle import _emit_process_lifecycle
from transpiler.runtime_emit_process_redirs import (
    _emit_process_run_argv_env_redirs,
    _emit_process_run_argv_redirs,
    _emit_redirection_helpers,
)


def emit_runtime_process(emitter: Any) -> None:
    """Emit small cross-platform process helpers."""
    o = emitter._output
    o.append("/* Process runtime helpers */")
    o.append("#if !defined(AILANG_WINDOWS) && !defined(AILANG_FREESTANDING)")
    o.append("    #include <unistd.h>")
    o.append("    #include <sys/wait.h>")
    o.append("    #include <errno.h>")
    o.append("    #include <fcntl.h>")
    o.append("    #include <sys/stat.h>")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append("    #include <errno.h>")
    o.append("    #include <process.h>")
    o.append("    #include <io.h>")
    o.append("    #include <fcntl.h>")
    o.append("    #include <sys/stat.h>")
    o.append("    #include <windows.h>")
    o.append("#endif")
    o.append("")
    o.append("AILANG_UNUSED static int64_t ailang_getpid(void) {")
    o.append("#if defined(AILANG_WINDOWS)")
    o.append("    return (int64_t)GetCurrentProcessId();")
    o.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    o.append("    extern int getpid(void);")
    o.append("    return (int64_t)getpid();")
    o.append("#else")
    o.append("    return -38;")
    o.append("#endif")
    o.append("}")
    o.append("")
    _emit_posix_identity_helper(o, "ailang_getppid", "getppid")
    _emit_posix_identity_helper(o, "ailang_getuid", "getuid")
    _emit_posix_identity_helper(o, "ailang_geteuid", "geteuid")
    _emit_posix_identity_helper(o, "ailang_getgid", "getgid")
    _emit_posix_identity_helper(o, "ailang_getegid", "getegid")
    _emit_process_umask(o)
    _emit_process_group_helpers(o)
    _emit_process_exec_errno_helpers(o)
    _emit_windows_argv_helpers(o)
    _emit_redirection_helpers(o)
    _emit_process_run_argv(o)
    _emit_process_run_argv_redirs(o)
    _emit_process_run_argv_env_redirs(o)
    _emit_signal_helpers(o)
    _emit_process_lifecycle(o)
    _emit_process_capture_argv_env_redirs(o)
    _emit_process_pipe_argv_redirs(o)
    _emit_process_pipeline_argv_redirs(o)
    _emit_process_capture_pipeline_argv_redirs(o)


def _emit_windows_argv_helpers(output: list[str]) -> None:
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("static int ailang_windows_arg_needs_quote(const char *s) {")
    output.append("    if (!s || !*s) return 1;")
    output.append("    for (const char *p = s; *p; ++p) {")
    output.append("        if (*p == ' ' || *p == '\\t' || *p == '\"') return 1;")
    output.append("    }")
    output.append("    return 0;")
    output.append("}")
    output.append("")
    output.append("static char *ailang_windows_quote_arg(const char *s) {")
    output.append('    if (!s) s = "";')
    output.append("    size_t n = strlen(s);")
    output.append("    size_t cap = n * 2 + 3;")
    output.append("    char *out = (char *)ailang_safe_malloc(cap);")
    output.append(
        "    if (!ailang_windows_arg_needs_quote(s)) { memcpy(out, s, n + 1); return out; }"
    )
    output.append("    size_t j = 0;")
    output.append("    size_t slash_count = 0;")
    output.append("    out[j++] = '\"';")
    output.append("    for (size_t i = 0; i < n; i++) {")
    output.append("        char c = s[i];")
    output.append("        if (c == '\\\\') { slash_count++; continue; }")
    output.append("        if (c == '\"') {")
    output.append(
        "            while (slash_count > 0) { out[j++] = '\\\\'; out[j++] = '\\\\'; slash_count--; }"
    )
    output.append("            out[j++] = '\\\\';")
    output.append("            out[j++] = c;")
    output.append("            continue;")
    output.append("        }")
    output.append(
        "        while (slash_count > 0) { out[j++] = '\\\\'; slash_count--; }"
    )
    output.append("        out[j++] = c;")
    output.append("    }")
    output.append(
        "    while (slash_count > 0) { out[j++] = '\\\\'; out[j++] = '\\\\'; slash_count--; }"
    )
    output.append("    out[j++] = '\"';")
    output.append("    out[j] = '\\0';")
    output.append("    return out;")
    output.append("}")
    output.append("")
    output.append(
        "static const char **ailang_windows_spawn_argv_from_str_array(ailang_str_array args) {"
    )
    output.append(
        "    const char **argv = (const char **)ailang_safe_malloc((size_t)(args.length + 1) * sizeof(const char *));"
    )
    output.append(
        "    for (int64_t i = 0; i < args.length; i++) argv[i] = ailang_windows_quote_arg(args.data[i]);"
    )
    output.append("    argv[args.length] = NULL;")
    output.append("    return argv;")
    output.append("}")
    output.append("")
    output.append(
        "static void ailang_windows_spawn_argv_free(const char **argv, int64_t length) {"
    )
    output.append("    if (!argv) return;")
    output.append(
        "    for (int64_t i = 0; i < length; i++) if (argv[i]) ailang_safe_free((void *)argv[i]);"
    )
    output.append("    ailang_safe_free((void *)argv);")
    output.append("}")
    output.append("#endif")
    output.append("")


def _emit_posix_identity_helper(
    output: list[str], helper_name: str, symbol: str
) -> None:
    output.append(f"AILANG_UNUSED static int64_t {helper_name}(void) {{")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(f"    return (int64_t){symbol}();")
    output.append("#else")
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_umask(output: list[str]) -> None:
    output.append("AILANG_UNUSED static int64_t ailang_process_umask(int64_t mask) {")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    return (int64_t)_umask((int)mask);")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    return (int64_t)umask((mode_t)mask);")
    output.append("#else")
    output.append("    (void)mask; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_group_helpers(output: list[str]) -> None:
    output.append("AILANG_UNUSED static int64_t ailang_process_get_pgrp(int64_t pid) {")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)pid; return -38;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    pid_t target = (pid_t)pid;")
    output.append("#if defined(_POSIX_VERSION)")
    output.append("    pid_t pgid = getpgid(target);")
    output.append("    if (pgid < 0) return -(int64_t)errno;")
    output.append("    return (int64_t)pgid;")
    output.append("#else")
    output.append("    if (target != 0 && target != getpid()) return -38;")
    output.append("    return (int64_t)getpgrp();")
    output.append("#endif")
    output.append("#else")
    output.append("    (void)pid; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_set_pgrp(int64_t pid, int64_t pgid) {"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)pid; (void)pgid; return -38;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    if (setpgid((pid_t)pid, (pid_t)pgid) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#else")
    output.append("    (void)pid; (void)pgid; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_kill_pgrp(int64_t pgid, int64_t signo) {"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)pgid; (void)signo; return -38;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    if (pgid <= 0) return 22;")
    output.append("    if (kill((pid_t)(0 - pgid), (int)signo) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#else")
    output.append("    (void)pgid; (void)signo; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_terminal_get_pgrp(int64_t fd) {")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)fd; return -38;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    pid_t pgid = tcgetpgrp((int)fd);")
    output.append("    if (pgid < 0) return -(int64_t)errno;")
    output.append("    return (int64_t)pgid;")
    output.append("#else")
    output.append("    (void)fd; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_terminal_set_pgrp(int64_t fd, int64_t pgid) {"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)fd; (void)pgid; return -38;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    if (tcsetpgrp((int)fd, (pid_t)pgid) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#else")
    output.append("    (void)fd; (void)pgid; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_exec_errno_helpers(output: list[str]) -> None:
    output.append("static int64_t ailang_process_last_exec_errno_value = 0;")
    output.append("static int64_t ailang_process_last_capture_status_value = 0;")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_last_exec_errno(void) {")
    output.append("    return ailang_process_last_exec_errno_value;")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_last_capture_status(void) {"
    )
    output.append("    return ailang_process_last_capture_status_value;")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static void ailang_process_set_last_capture_status(int64_t status) {"
    )
    output.append("    ailang_process_last_capture_status_value = status;")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_errno_enoexec(void) {")
    output.append("#ifdef ENOEXEC")
    output.append("    return (int64_t)ENOEXEC;")
    output.append("#else")
    output.append("    return 8;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_errno_enoent(void) {")
    output.append("#ifdef ENOENT")
    output.append("    return (int64_t)ENOENT;")
    output.append("#else")
    output.append("    return 2;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_errno_eacces(void) {")
    output.append("#ifdef EACCES")
    output.append("    return (int64_t)EACCES;")
    output.append("#else")
    output.append("    return 13;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_errno_eperm(void) {")
    output.append("#ifdef EPERM")
    output.append("    return (int64_t)EPERM;")
    output.append("#else")
    output.append("    return 1;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("static void ailang_process_clear_exec_errno(void) {")
    output.append("    ailang_process_last_exec_errno_value = 0;")
    output.append("}")
    output.append("")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("static int ailang_process_make_exec_errno_pipe(int fds[2]) {")
    output.append("    if (pipe(fds) < 0) return -1;")
    output.append("    (void)fcntl(fds[1], F_SETFD, FD_CLOEXEC);")
    output.append("    return 0;")
    output.append("}")
    output.append("")
    output.append("static void ailang_process_child_report_exec_errno(int fd) {")
    output.append("    if (fd < 0) return;")
    output.append("    int err = errno;")
    output.append("    (void)write(fd, &err, sizeof(err));")
    output.append("    close(fd);")
    output.append("}")
    output.append("")
    output.append("static void ailang_process_parent_read_exec_errno(int fd) {")
    output.append("    ailang_process_last_exec_errno_value = 0;")
    output.append("    if (fd < 0) return;")
    output.append("    int err = 0;")
    output.append("    ssize_t n = read(fd, &err, sizeof(err));")
    output.append(
        "    if (n == (ssize_t)sizeof(err)) ailang_process_last_exec_errno_value = (int64_t)err;"
    )
    output.append("    close(fd);")
    output.append("}")
    output.append("")
    output.append("static int ailang_process_argv0_has_path_sep(const char *name) {")
    output.append("    if (!name) return 0;")
    output.append("    for (const char *p = name; *p; ++p) {")
    output.append("        if (*p == '/' || *p == '\\\\') return 1;")
    output.append("    }")
    output.append("    return 0;")
    output.append("}")
    output.append("")
    output.append("static void ailang_process_exec_argv(char * const argv[]) {")
    output.append(
        "    if (argv && argv[0] && ailang_process_argv0_has_path_sep(argv[0])) {"
    )
    output.append("        execv(argv[0], argv);")
    output.append("    } else {")
    output.append("        execvp(argv[0], argv);")
    output.append("    }")
    output.append("}")
    output.append("#endif")
    output.append("")


def _emit_signal_helpers(output: list[str]) -> None:
    output.append("#ifndef AILANG_SIGNAL_MAX")
    output.append("#define AILANG_SIGNAL_MAX 128")
    output.append("#endif")
    output.append(
        "static volatile sig_atomic_t ailang_signal_pending_flags[AILANG_SIGNAL_MAX];"
    )
    output.append("static void ailang_signal_pending_handler(int signo) {")
    output.append("    if (signo > 0 && signo < AILANG_SIGNAL_MAX) {")
    output.append("        ailang_signal_pending_flags[signo] = 1;")
    output.append("    }")
    output.append("}")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        "static BOOL WINAPI ailang_signal_console_handler(DWORD event_type) {"
    )
    output.append("    switch (event_type) {")
    output.append("        case CTRL_C_EVENT:")
    output.append("        case CTRL_BREAK_EVENT:")
    output.append("#ifdef SIGINT")
    output.append("            ailang_signal_pending_handler(SIGINT);")
    output.append("#endif")
    output.append("            return TRUE;")
    output.append("        case CTRL_CLOSE_EVENT:")
    output.append("        case CTRL_LOGOFF_EVENT:")
    output.append("        case CTRL_SHUTDOWN_EVENT:")
    output.append("#ifdef SIGTERM")
    output.append("            ailang_signal_pending_handler(SIGTERM);")
    output.append("#endif")
    output.append("            return TRUE;")
    output.append("        default:")
    output.append("            return FALSE;")
    output.append("    }")
    output.append("}")
    output.append("#endif")
    output.append("AILANG_UNUSED static int64_t ailang_signal_install(int64_t signo) {")
    output.append("    if (signo <= 0 || signo >= AILANG_SIGNAL_MAX) return 22;")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    struct sigaction sa;")
    output.append("    memset(&sa, 0, sizeof(sa));")
    output.append("    sa.sa_handler = ailang_signal_pending_handler;")
    output.append("    sigemptyset(&sa.sa_mask);")
    output.append("    sa.sa_flags = 0;")
    output.append("#ifdef SA_RESTART")
    output.append("    sa.sa_flags |= SA_RESTART;")
    output.append("#endif")
    output.append("    if (sigaction((int)signo, &sa, NULL) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#elif defined(AILANG_WINDOWS)")
    output.append("    if (signo == SIGINT || signo == SIGTERM) {")
    output.append(
        "        if (signal((int)signo, ailang_signal_pending_handler) == SIG_ERR) return (int64_t)errno;"
    )
    output.append("        SetConsoleCtrlHandler(ailang_signal_console_handler, TRUE);")
    output.append("        return 0;")
    output.append("    }")
    output.append("    return 38;")
    output.append("#else")
    output.append("    (void)signo; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("AILANG_UNUSED static int64_t ailang_signal_ignore(int64_t signo) {")
    output.append("    if (signo <= 0 || signo >= AILANG_SIGNAL_MAX) return 22;")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    struct sigaction sa;")
    output.append("    memset(&sa, 0, sizeof(sa));")
    output.append("    sa.sa_handler = SIG_IGN;")
    output.append("    sigemptyset(&sa.sa_mask);")
    output.append("    if (sigaction((int)signo, &sa, NULL) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#elif defined(AILANG_WINDOWS)")
    output.append("    if (signo == SIGINT || signo == SIGTERM) {")
    output.append(
        "        if (signal((int)signo, SIG_IGN) == SIG_ERR) return (int64_t)errno;"
    )
    output.append("        return 0;")
    output.append("    }")
    output.append("    return 38;")
    output.append("#else")
    output.append("    (void)signo; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("AILANG_UNUSED static int64_t ailang_signal_default(int64_t signo) {")
    output.append("    if (signo <= 0 || signo >= AILANG_SIGNAL_MAX) return 22;")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    struct sigaction sa;")
    output.append("    memset(&sa, 0, sizeof(sa));")
    output.append("    sa.sa_handler = SIG_DFL;")
    output.append("    sigemptyset(&sa.sa_mask);")
    output.append("    if (sigaction((int)signo, &sa, NULL) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#elif defined(AILANG_WINDOWS)")
    output.append("    if (signo == SIGINT || signo == SIGTERM) {")
    output.append(
        "        if (signal((int)signo, SIG_DFL) == SIG_ERR) return (int64_t)errno;"
    )
    output.append("        return 0;")
    output.append("    }")
    output.append("    return 38;")
    output.append("#else")
    output.append("    (void)signo; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("AILANG_UNUSED static int64_t ailang_signal_pending(void) {")
    output.append("    for (int i = 1; i < AILANG_SIGNAL_MAX; i++) {")
    output.append("        if (ailang_signal_pending_flags[i]) return (int64_t)i;")
    output.append("    }")
    output.append("    return 0;")
    output.append("}")
    output.append("AILANG_UNUSED static int64_t ailang_signal_clear(int64_t signo) {")
    output.append("    if (signo <= 0 || signo >= AILANG_SIGNAL_MAX) return 22;")
    output.append("    ailang_signal_pending_flags[signo] = 0;")
    output.append("    return 0;")
    output.append("}")
    output.append("AILANG_UNUSED static int64_t ailang_signal_drain(void) {")
    output.append("    for (int i = 1; i < AILANG_SIGNAL_MAX; i++) {")
    output.append("        if (ailang_signal_pending_flags[i]) {")
    output.append("            ailang_signal_pending_flags[i] = 0;")
    output.append("            return (int64_t)i;")
    output.append("        }")
    output.append("    }")
    output.append("    return 0;")
    output.append("}")
    output.append("AILANG_UNUSED static int64_t ailang_signal_raise(int64_t signo) {")
    output.append("    if (signo <= 0 || signo >= AILANG_SIGNAL_MAX) return 22;")
    output.append("#ifndef AILANG_FREESTANDING")
    output.append("    if (raise((int)signo) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#else")
    output.append("    (void)signo; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_run_argv(output: list[str]) -> None:
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_run_argv(ailang_str_array args) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append(
        "    if (args.length <= 0 || !args.data || !args.data[0]) return 127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        "    const char **argv = ailang_windows_spawn_argv_from_str_array(args);"
    )
    output.append("    intptr_t rc = _spawnvp(_P_WAIT, args.data[0], argv);")
    output.append("    ailang_windows_spawn_argv_free(argv, args.length);")
    output.append(
        "    if (rc < 0) { ailang_process_last_exec_errno_value = (int64_t)errno; return 127; }"
    )
    output.append("    return (int64_t)rc;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    char **argv = (char **)ailang_safe_malloc("
        "(size_t)(args.length + 1) * sizeof(char *));"
    )
    output.append(
        "    for (int64_t i = 0; i < args.length; i++) argv[i] = (char *)args.data[i];"
    )
    output.append("    argv[args.length] = NULL;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    pid_t pid = fork();")
    output.append("    if (pid < 0) {")
    output.append("        if (execerr[0] >= 0) close(execerr[0]);")
    output.append("        if (execerr[1] >= 0) close(execerr[1]);")
    output.append("        ailang_safe_free(argv);")
    output.append("        return 126;")
    output.append("    }")
    output.append("    if (pid == 0) {")
    output.append("        if (execerr[0] >= 0) close(execerr[0]);")
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    int status = 0;")
    output.append("    for (;;) {")
    output.append("        if (waitpid(pid, &status, 0) >= 0) break;")
    output.append("        if (errno == EINTR) continue;")
    output.append("        ailang_safe_free(argv);")
    output.append("        return 126;")
    output.append("    }")
    output.append("    ailang_safe_free(argv);")
    output.append("    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);")
    output.append(
        "    if (WIFSIGNALED(status)) return (int64_t)(128 + WTERMSIG(status));"
    )
    output.append("    return 126;")
    output.append("#else")
    output.append("    (void)args;")
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")

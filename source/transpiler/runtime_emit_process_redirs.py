"""C runtime emitter helpers for process redirections."""

from __future__ import annotations


def _emit_redirection_helpers(output: list[str]) -> None:
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("static void ailang_process_sync_std_handle(int fd) {")
    output.append("    intptr_t raw = _get_osfhandle(fd);")
    output.append("    if (raw == -1) return;")
    output.append("    HANDLE handle = (HANDLE)raw;")
    output.append("    if (fd == 0) SetStdHandle(STD_INPUT_HANDLE, handle);")
    output.append("    else if (fd == 1) SetStdHandle(STD_OUTPUT_HANDLE, handle);")
    output.append("    else if (fd == 2) SetStdHandle(STD_ERROR_HANDLE, handle);")
    output.append("}")
    output.append("#else")
    output.append("static void ailang_process_sync_std_handle(int fd) { (void)fd; }")
    output.append("#endif")
    output.append("")
    output.append("static int ailang_redir_fd(const char *op, int fallback) {")
    output.append("    int i = 0;")
    output.append("    int fd = 0;")
    output.append("    while (op && op[i] >= '0' && op[i] <= '9') {")
    output.append("        fd = fd * 10 + (op[i] - '0');")
    output.append("        i++;")
    output.append("    }")
    output.append("    return i > 0 ? fd : fallback;")
    output.append("}")
    output.append("")
    output.append("static const char *ailang_redir_core(const char *op) {")
    output.append("    while (op && *op >= '0' && *op <= '9') op++;")
    output.append('    return op ? op : "";')
    output.append("}")
    output.append("")
    output.append(
        "static int ailang_redir_split_core_offset(const char *core, char *base, size_t base_cap, int64_t *offset) {"
    )
    output.append("    if (!core || !base || base_cap == 0 || !offset) return -1;")
    output.append("    *offset = 0;")
    output.append("    const char *at = strchr(core, '@');")
    output.append("    size_t n = at ? (size_t)(at - core) : strlen(core);")
    output.append("    if (n == 0 || n >= base_cap) return -1;")
    output.append("    memcpy(base, core, n);")
    output.append("    base[n] = '\\0';")
    output.append("    if (!at) return 0;")
    output.append("    if (base[0] != '<') return -1;")
    output.append("    int64_t value = 0;")
    output.append("    const char *p = at + 1;")
    output.append("    if (!*p) return -1;")
    output.append("    while (*p) {")
    output.append("        if (*p < '0' || *p > '9') return -1;")
    output.append("        if (value > (INT64_MAX - 9) / 10) return -1;")
    output.append("        value = value * 10 + (int64_t)(*p - '0');")
    output.append("        p++;")
    output.append("    }")
    output.append("    *offset = value;")
    output.append("    return 0;")
    output.append("}")
    output.append("")
    output.append("static int ailang_redir_parse_fd(const char *text, int *out_fd) {")
    output.append("    if (!text || !*text) return -1;")
    output.append("    int fd = 0;")
    output.append("    for (int i = 0; text[i]; i++) {")
    output.append("        if (text[i] < '0' || text[i] > '9') return -1;")
    output.append("        fd = fd * 10 + (text[i] - '0');")
    output.append("    }")
    output.append("    *out_fd = fd;")
    output.append("    return 0;")
    output.append("}")
    output.append("")
    output.append(
        "static int ailang_apply_redirs(ailang_str_array ops, ailang_str_array targets) {"
    )
    output.append(
        "    int64_t n = ops.length < targets.length ? ops.length : targets.length;"
    )
    output.append("    for (int64_t i = 0; i < n; i++) {")
    output.append('        const char *op = ops.data[i] ? ops.data[i] : "";')
    output.append(
        '        const char *target = targets.data[i] ? targets.data[i] : "";'
    )
    output.append("        const char *core = ailang_redir_core(op);")
    output.append("        char core_base[4];")
    output.append("        int64_t seek_offset = 0;")
    output.append(
        "        if (ailang_redir_split_core_offset(core, core_base, sizeof(core_base), &seek_offset) != 0) return -1;"
    )
    output.append("        core = core_base;")
    output.append(
        "        int target_fd = ailang_redir_fd(op, core[0] == '<' ? 0 : 1);"
    )
    output.append("        int fd = -1;")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        '        if (strcmp(core, "<") == 0) fd = _open(target, _O_RDONLY | _O_BINARY);'
    )
    output.append(
        '        else if (strcmp(core, "<<") == 0 || strcmp(core, "<<-") == 0) {'
    )
    output.append("            int pfd[2];")
    output.append("            if (_pipe(pfd, 65536, _O_BINARY) != 0) return -1;")
    output.append("            for (const char *p = target; *p; p++) {")
    output.append("                char ch = (*p == '\\r') ? '\\n' : *p;")
    output.append(
        "                if (_write(pfd[1], &ch, 1) != 1) { _close(pfd[0]); _close(pfd[1]); return -1; }"
    )
    output.append("            }")
    output.append("            _close(pfd[1]);")
    output.append("            fd = pfd[0];")
    output.append("        }")
    output.append(
        '        else if (strcmp(core, ">") == 0 || strcmp(core, ">|") == 0) fd = _open(target, _O_WRONLY | _O_CREAT | _O_TRUNC | _O_BINARY, _S_IREAD | _S_IWRITE);'
    )
    output.append(
        '        else if (strcmp(core, ">>") == 0) { fd = _open(target, _O_WRONLY | _O_CREAT | _O_APPEND | _O_BINARY, _S_IREAD | _S_IWRITE); if (fd >= 0) _lseek(fd, 0, SEEK_END); }'
    )
    output.append(
        '        else if (strcmp(core, "<>") == 0) fd = _open(target, _O_RDWR | _O_CREAT | _O_BINARY, _S_IREAD | _S_IWRITE);'
    )
    output.append(
        '        else if (strcmp(core, "<&") == 0 || strcmp(core, ">&") == 0) {'
    )
    output.append(
        '            if (strcmp(target, "-") == 0) { _close(target_fd); continue; }'
    )
    output.append("            int source_fd = -1;")
    output.append(
        "            if (ailang_redir_parse_fd(target, &source_fd) != 0) return -1;"
    )
    output.append("            if (_dup2(source_fd, target_fd) != 0) return -1;")
    output.append("            ailang_process_sync_std_handle(target_fd);")
    output.append("            continue;")
    output.append("        } else return -1;")
    output.append("        if (fd < 0) return -1;")
    output.append(
        '        if (seek_offset > 0 && !(strcmp(core, "<") == 0 || strcmp(core, "<>") == 0)) { _close(fd); return -1; }'
    )
    output.append(
        "        if (seek_offset > 0 && _lseeki64(fd, seek_offset, SEEK_SET) < 0) { _close(fd); return -1; }"
    )
    output.append(
        "        if (fd != target_fd) { if (_dup2(fd, target_fd) != 0) { _close(fd); return -1; } ailang_process_sync_std_handle(target_fd); _close(fd); } else { ailang_process_sync_std_handle(target_fd); }"
    )
    output.append("#else")
    output.append('        if (strcmp(core, "<") == 0) fd = open(target, O_RDONLY);')
    output.append(
        '        else if (strcmp(core, "<<") == 0 || strcmp(core, "<<-") == 0) {'
    )
    output.append("            FILE *tmp = tmpfile();")
    output.append("            if (!tmp) return -1;")
    output.append(
        "            for (const char *p = target; *p; p++) fputc(*p == '\\r' ? '\\n' : *p, tmp);"
    )
    output.append("            fflush(tmp);")
    output.append("            rewind(tmp);")
    output.append("            fd = dup(fileno(tmp));")
    output.append("            fclose(tmp);")
    output.append("        }")
    output.append(
        '        else if (strcmp(core, ">") == 0 || strcmp(core, ">|") == 0) fd = open(target, O_WRONLY | O_CREAT | O_TRUNC, 0666);'
    )
    output.append(
        '        else if (strcmp(core, ">>") == 0) fd = open(target, O_WRONLY | O_CREAT | O_APPEND, 0666);'
    )
    output.append(
        '        else if (strcmp(core, "<>") == 0) fd = open(target, O_RDWR | O_CREAT, 0666);'
    )
    output.append(
        '        else if (strcmp(core, "<&") == 0 || strcmp(core, ">&") == 0) {'
    )
    output.append(
        '            if (strcmp(target, "-") == 0) { close(target_fd); continue; }'
    )
    output.append("            int source_fd = -1;")
    output.append(
        "            if (ailang_redir_parse_fd(target, &source_fd) != 0) return -1;"
    )
    output.append("            if (dup2(source_fd, target_fd) < 0) return -1;")
    output.append("            ailang_process_sync_std_handle(target_fd);")
    output.append("            continue;")
    output.append("        } else return -1;")
    output.append("        if (fd < 0) return -1;")
    output.append(
        '        if (seek_offset > 0 && !(strcmp(core, "<") == 0 || strcmp(core, "<>") == 0)) { close(fd); return -1; }'
    )
    output.append(
        "        if (seek_offset > 0 && lseek(fd, (off_t)seek_offset, SEEK_SET) < 0) { close(fd); return -1; }"
    )
    output.append(
        "        if (fd != target_fd) { if (dup2(fd, target_fd) < 0) { close(fd); return -1; } ailang_process_sync_std_handle(target_fd); close(fd); } else { ailang_process_sync_std_handle(target_fd); }"
    )
    output.append("#endif")
    output.append("    }")
    output.append("    return 0;")
    output.append("}")
    output.append("")


def _emit_process_run_argv_redirs(output: list[str]) -> None:
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_run_argv_redirs("
        "ailang_str_array args, ailang_str_array ops, ailang_str_array targets) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append(
        "    if (args.length <= 0 || !args.data || !args.data[0]) return 127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    int saved[3];")
    output.append("    for (int i = 0; i < 3; i++) saved[i] = _dup(i);")
    output.append("    if (ailang_apply_redirs(ops, targets) != 0) {")
    output.append(
        "        for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append("        return 126;")
    output.append("    }")
    output.append(
        "    const char **argv = ailang_windows_spawn_argv_from_str_array(args);"
    )
    output.append("    intptr_t rc = _spawnvp(_P_WAIT, args.data[0], argv);")
    output.append("    ailang_windows_spawn_argv_free(argv, args.length);")
    output.append(
        "    for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
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
    output.append(
        "    if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); ailang_safe_free(argv); return 126; }"
    )
    output.append("    if (pid == 0) {")
    output.append("        if (execerr[0] >= 0) close(execerr[0]);")
    output.append(
        "        if (ailang_apply_redirs(ops, targets) != 0) { if (execerr[1] >= 0) close(execerr[1]); _exit(126); }"
    )
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    int status = 0;")
    output.append(
        "    for (;;) { if (waitpid(pid, &status, 0) >= 0) break; if (errno != EINTR) { ailang_safe_free(argv); return 126; } }"
    )
    output.append("    ailang_safe_free(argv);")
    output.append("    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);")
    output.append(
        "    if (WIFSIGNALED(status)) return (int64_t)(128 + WTERMSIG(status));"
    )
    output.append("    return 126;")
    output.append("#else")
    output.append("    (void)args; (void)ops; (void)targets;")
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_run_argv_env_redirs(output: list[str]) -> None:
    output.append("static char *ailang_env_name_copy(const char *pair) {")
    output.append("    const char *eq = pair ? strchr(pair, '=') : NULL;")
    output.append("    if (!eq || eq == pair) return NULL;")
    output.append("    size_t n = (size_t)(eq - pair);")
    output.append("    char *name = (char *)ailang_safe_malloc(n + 1);")
    output.append("    memcpy(name, pair, n);")
    output.append("    name[n] = '\\0';")
    output.append("    return name;")
    output.append("}")
    output.append("")
    output.append("static char *ailang_env_value_copy(const char *value) {")
    output.append("    size_t n = value ? strlen(value) : 0;")
    output.append("    char *out = (char *)ailang_safe_malloc(n + 1);")
    output.append("    if (n > 0) memcpy(out, value, n);")
    output.append("    out[n] = '\\0';")
    output.append("    return out;")
    output.append("}")
    output.append("")

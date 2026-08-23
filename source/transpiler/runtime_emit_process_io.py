"""Process capture and pipe runtime C emitters."""

from __future__ import annotations


def _emit_process_capture_argv_env_redirs(output: list[str]) -> None:
    output.append("static char *ailang_process_capture_empty(void) {")
    output.append("    char *out = (char *)ailang_safe_malloc(1);")
    output.append("    out[0] = '\\0';")
    output.append("    return out;")
    output.append("}")
    output.append("")
    output.append(
        "static void ailang_process_capture_append(char **out, size_t *len, size_t *cap, const char *buf, size_t got) {"
    )
    output.append(
        '    if (got > AILANG_MAX_ALLOC_SIZE - *len - 1) __ailang_safety_trap("process_capture_argv_env_redirs: output exceeds allocation cap");'
    )
    output.append("    size_t need = *len + got + 1;")
    output.append("    if (need > *cap) {")
    output.append("        while (*cap < need) *cap *= 2;")
    output.append("        *out = (char *)ailang_safe_realloc(*out, *cap);")
    output.append("    }")
    output.append("    memcpy(*out + *len, buf, got);")
    output.append("    *len += got;")
    output.append("    (*out)[*len] = '\\0';")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static char *ailang_process_capture_argv_env_redirs(ailang_str_array args, ailang_str_array env, ailang_str_array ops, ailang_str_array targets) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append("    ailang_process_last_capture_status_value = 0;")
    output.append(
        "    if (args.length <= 0 || !args.data || !args.data[0]) { ailang_process_last_capture_status_value = 127; return ailang_process_capture_empty(); }"
    )
    output.append("    size_t cap = 256;")
    output.append("    size_t len = 0;")
    output.append("    char *out = (char *)ailang_safe_malloc(cap);")
    output.append("    out[0] = '\\0';")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    int pfd[2];")
    output.append("    if (_pipe(pfd, 65536, _O_BINARY) != 0) return out;")
    output.append(
        "    char **names = (char **)ailang_safe_malloc((size_t)env.length * sizeof(char *));"
    )
    output.append(
        "    char **old_values = (char **)ailang_safe_malloc((size_t)env.length * sizeof(char *));"
    )
    output.append(
        "    int *had_old = (int *)ailang_safe_malloc((size_t)env.length * sizeof(int));"
    )
    output.append(
        "    for (int64_t i = 0; i < env.length; i++) { names[i] = NULL; old_values[i] = NULL; had_old[i] = 0; }"
    )
    output.append("    for (int64_t i = 0; i < env.length; i++) {")
    output.append('        const char *pair = env.data[i] ? env.data[i] : "";')
    output.append("        const char *eq = strchr(pair, '=');")
    output.append("        names[i] = ailang_env_name_copy(pair);")
    output.append("        if (!eq || !names[i]) continue;")
    output.append("        const char *old = getenv(names[i]);")
    output.append(
        "        if (old) { had_old[i] = 1; old_values[i] = ailang_env_value_copy(old); }"
    )
    output.append("        _putenv_s(names[i], eq + 1);")
    output.append("    }")
    output.append("    int saved[3];")
    output.append("    for (int i = 0; i < 3; i++) saved[i] = _dup(i);")
    output.append("    int setup_ok = (_dup2(pfd[1], 1) == 0);")
    output.append("    if (setup_ok) ailang_process_sync_std_handle(1);")
    output.append(
        "    if (setup_ok && ailang_apply_redirs(ops, targets) != 0) setup_ok = 0;"
    )
    output.append("    intptr_t pid = -1;")
    output.append("    if (setup_ok) {")
    output.append(
        "        const char **argv = ailang_windows_spawn_argv_from_str_array(args);"
    )
    output.append("        pid = _spawnvp(_P_NOWAIT, args.data[0], argv);")
    output.append(
        "        if (pid < 0) { ailang_process_last_exec_errno_value = (int64_t)errno; ailang_process_last_capture_status_value = 127; }"
    )
    output.append("        ailang_windows_spawn_argv_free(argv, args.length);")
    output.append("    }")
    output.append("    else { ailang_process_last_capture_status_value = 126; }")
    output.append(
        "    for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append("    _close(pfd[1]);")
    output.append("    char buf[4096];")
    output.append("    int got = 0;")
    output.append(
        "    while ((got = _read(pfd[0], buf, (unsigned)sizeof(buf))) > 0) ailang_process_capture_append(&out, &len, &cap, buf, (size_t)got);"
    )
    output.append("    _close(pfd[0]);")
    output.append(
        "    if (pid >= 0) { int child_status = 0; if (_cwait(&child_status, pid, 0) >= 0) ailang_process_last_capture_status_value = (int64_t)child_status; else ailang_process_last_capture_status_value = 126; }"
    )
    output.append("    for (int64_t i = 0; i < env.length; i++) {")
    output.append(
        '        if (names[i]) { if (had_old[i]) _putenv_s(names[i], old_values[i] ? old_values[i] : ""); else _putenv_s(names[i], ""); }'
    )
    output.append("        if (names[i]) ailang_safe_free(names[i]);")
    output.append("        if (old_values[i]) ailang_safe_free(old_values[i]);")
    output.append("    }")
    output.append(
        "    ailang_safe_free(names); ailang_safe_free(old_values); ailang_safe_free(had_old);"
    )
    output.append("    return out;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    int pfd[2];")
    output.append(
        "    if (pipe(pfd) < 0) { ailang_process_last_capture_status_value = 126; return out; }"
    )
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    pid_t pid = fork();")
    output.append(
        "    if (pid < 0) { close(pfd[0]); close(pfd[1]); if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); ailang_process_last_capture_status_value = 126; return out; }"
    )
    output.append("    if (pid == 0) {")
    output.append("        close(pfd[0]);")
    output.append("        if (execerr[0] >= 0) close(execerr[0]);")
    output.append("        if (dup2(pfd[1], 1) < 0) _exit(126);")
    output.append("        close(pfd[1]);")
    output.append(
        "        if (ailang_apply_redirs(ops, targets) != 0) { if (execerr[1] >= 0) close(execerr[1]); _exit(126); }"
    )
    output.append("        for (int64_t i = 0; i < env.length; i++) {")
    output.append('            const char *pair = env.data[i] ? env.data[i] : "";')
    output.append("            const char *eq = strchr(pair, '=');")
    output.append("            char *name = ailang_env_name_copy(pair);")
    output.append("            if (eq && name) setenv(name, eq + 1, 1);")
    output.append("            if (name) ailang_safe_free(name);")
    output.append("        }")
    output.append(
        "        char **argv = (char **)ailang_safe_malloc((size_t)(args.length + 1) * sizeof(char *));"
    )
    output.append(
        "        for (int64_t i = 0; i < args.length; i++) argv[i] = (char *)args.data[i];"
    )
    output.append("        argv[args.length] = NULL;")
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    close(pfd[1]);")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    char buf[4096];")
    output.append("    ssize_t got = 0;")
    output.append(
        "    while ((got = read(pfd[0], buf, sizeof(buf))) > 0) ailang_process_capture_append(&out, &len, &cap, buf, (size_t)got);"
    )
    output.append("    close(pfd[0]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    int status = 0;")
    output.append("    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}")
    output.append(
        "    if (WIFEXITED(status)) ailang_process_last_capture_status_value = (int64_t)WEXITSTATUS(status);"
    )
    output.append(
        "    else if (WIFSIGNALED(status)) ailang_process_last_capture_status_value = (int64_t)(128 + WTERMSIG(status));"
    )
    output.append("    else ailang_process_last_capture_status_value = 126;")
    output.append("    return out;")
    output.append("#else")
    output.append("    (void)env; (void)ops; (void)targets;")
    output.append("    return out;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_pipe_argv_redirs(output: list[str]) -> None:
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_pipe_argv_redirs("
        "ailang_str_array left_args, ailang_str_array left_ops, ailang_str_array left_targets, "
        "ailang_str_array right_args, ailang_str_array right_ops, ailang_str_array right_targets) {"
    )
    output.append(
        "    if (left_args.length <= 0 || right_args.length <= 0) return 127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    int pfd[2];")
    output.append("    if (_pipe(pfd, 65536, _O_BINARY) != 0) return 126;")
    output.append(
        "    const char **left_argv = ailang_windows_spawn_argv_from_str_array(left_args);"
    )
    output.append(
        "    const char **right_argv = ailang_windows_spawn_argv_from_str_array(right_args);"
    )
    output.append("    int saved[3];")
    output.append("    for (int i = 0; i < 3; i++) saved[i] = _dup(i);")
    output.append("    if (_dup2(pfd[1], 1) != 0) {")
    output.append(
        "        for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append(
        "        _close(pfd[0]); _close(pfd[1]); ailang_windows_spawn_argv_free(left_argv, left_args.length); ailang_windows_spawn_argv_free(right_argv, right_args.length); return 126;"
    )
    output.append("    }")
    output.append("    ailang_process_sync_std_handle(1);")
    output.append("    if (ailang_apply_redirs(left_ops, left_targets) != 0) {")
    output.append(
        "        for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append(
        "        _close(pfd[0]); _close(pfd[1]); ailang_windows_spawn_argv_free(left_argv, left_args.length); ailang_windows_spawn_argv_free(right_argv, right_args.length); return 126;"
    )
    output.append("    }")
    output.append(
        "    intptr_t left_pid = _spawnvp(_P_NOWAIT, left_args.data[0], left_argv);"
    )
    output.append(
        "    for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append("    _close(pfd[1]);")
    output.append(
        "    if (left_pid < 0) { _close(pfd[0]); ailang_windows_spawn_argv_free(left_argv, left_args.length); ailang_windows_spawn_argv_free(right_argv, right_args.length); return 127; }"
    )
    output.append("    for (int i = 0; i < 3; i++) saved[i] = _dup(i);")
    output.append("    if (_dup2(pfd[0], 0) != 0) {")
    output.append(
        "        for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append(
        "        _close(pfd[0]); ailang_windows_spawn_argv_free(left_argv, left_args.length); ailang_windows_spawn_argv_free(right_argv, right_args.length); return 126;"
    )
    output.append("    }")
    output.append("    ailang_process_sync_std_handle(0);")
    output.append("    if (ailang_apply_redirs(right_ops, right_targets) != 0) {")
    output.append(
        "        for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append(
        "        _close(pfd[0]); ailang_windows_spawn_argv_free(left_argv, left_args.length); ailang_windows_spawn_argv_free(right_argv, right_args.length); return 126;"
    )
    output.append("    }")
    output.append(
        "    intptr_t right_pid = _spawnvp(_P_NOWAIT, right_args.data[0], right_argv);"
    )
    output.append(
        "    for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append("    _close(pfd[0]);")
    output.append("    ailang_windows_spawn_argv_free(left_argv, left_args.length);")
    output.append("    ailang_windows_spawn_argv_free(right_argv, right_args.length);")
    output.append("    if (right_pid < 0) return 127;")
    output.append("    int left_status = 0;")
    output.append("    int right_status = 0;")
    output.append("    _cwait(&left_status, left_pid, 0);")
    output.append("    _cwait(&right_status, right_pid, 0);")
    output.append(
        "    return (int64_t)(right_status > 255 ? (right_status / 256) : right_status);"
    )
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    int pfd[2];")
    output.append("    if (pipe(pfd) < 0) return 126;")
    output.append("    pid_t left_pid = fork();")
    output.append("    if (left_pid < 0) { close(pfd[0]); close(pfd[1]); return 126; }")
    output.append("    if (left_pid == 0) {")
    output.append("        close(pfd[0]);")
    output.append("        if (dup2(pfd[1], 1) < 0) _exit(126);")
    output.append("        close(pfd[1]);")
    output.append(
        "        if (ailang_apply_redirs(left_ops, left_targets) != 0) _exit(126);"
    )
    output.append(
        "        char **argv = (char **)ailang_safe_malloc((size_t)(left_args.length + 1) * sizeof(char *));"
    )
    output.append(
        "        for (int64_t i = 0; i < left_args.length; i++) argv[i] = (char *)left_args.data[i];"
    )
    output.append("        argv[left_args.length] = NULL;")
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    pid_t right_pid = fork();")
    output.append(
        "    if (right_pid < 0) { close(pfd[0]); close(pfd[1]); return 126; }"
    )
    output.append("    if (right_pid == 0) {")
    output.append("        close(pfd[1]);")
    output.append("        if (dup2(pfd[0], 0) < 0) _exit(126);")
    output.append("        close(pfd[0]);")
    output.append(
        "        if (ailang_apply_redirs(right_ops, right_targets) != 0) _exit(126);"
    )
    output.append(
        "        char **argv = (char **)ailang_safe_malloc((size_t)(right_args.length + 1) * sizeof(char *));"
    )
    output.append(
        "        for (int64_t i = 0; i < right_args.length; i++) argv[i] = (char *)right_args.data[i];"
    )
    output.append("        argv[right_args.length] = NULL;")
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    close(pfd[0]); close(pfd[1]);")
    output.append("    int left_status = 0;")
    output.append("    int right_status = 0;")
    output.append(
        "    while (waitpid(left_pid, &left_status, 0) < 0 && errno == EINTR) {}"
    )
    output.append(
        "    while (waitpid(right_pid, &right_status, 0) < 0 && errno == EINTR) {}"
    )
    output.append(
        "    if (WIFEXITED(right_status)) return (int64_t)WEXITSTATUS(right_status);"
    )
    output.append(
        "    if (WIFSIGNALED(right_status)) return (int64_t)(128 + WTERMSIG(right_status));"
    )
    output.append("    return 126;")
    output.append("#else")
    output.append("    (void)left_args; (void)left_ops; (void)left_targets;")
    output.append("    (void)right_args; (void)right_ops; (void)right_targets;")
    output.append("    return 126;")
    output.append("#endif")
    output.append("}")
    output.append("")


def _emit_process_pipeline_argv_redirs(output: list[str]) -> None:
    output.append(
        "static const char **ailang_process_argv_slice("
        "ailang_str_array args, int64_t start, int64_t count) {"
    )
    output.append(
        "    if (count <= 0 || start < 0 || start + count > args.length) return NULL;"
    )
    output.append(
        "    const char **argv = (const char **)ailang_safe_malloc((size_t)(count + 1) * sizeof(const char *));"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        "    for (int64_t i = 0; i < count; i++) argv[i] = ailang_windows_quote_arg(args.data[start + i]);"
    )
    output.append("#else")
    output.append(
        "    for (int64_t i = 0; i < count; i++) argv[i] = args.data[start + i];"
    )
    output.append("#endif")
    output.append("    argv[count] = NULL;")
    output.append("    return argv;")
    output.append("}")
    output.append("")
    output.append(
        "static void ailang_process_argv_slice_free(const char **argv, int64_t count) {"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    ailang_windows_spawn_argv_free(argv, count);")
    output.append("#else")
    output.append("    (void)count; ailang_safe_free((void *)argv);")
    output.append("#endif")
    output.append("}")
    output.append("")

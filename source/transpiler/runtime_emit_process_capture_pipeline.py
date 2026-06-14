"""C runtime emitter for argv pipeline stdout capture."""

from __future__ import annotations


def _emit_process_capture_pipeline_argv_redirs(output: list[str]) -> None:
    output.append(
        "AILANG_UNUSED static char *ailang_process_capture_pipeline_argv_env_redirs("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array env, ailang_str_array ops, "
        "ailang_str_array targets, ailang_dyn_array redir_counts) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append("    ailang_process_last_capture_status_value = 0;")
    output.append("    size_t cap = 256;")
    output.append("    size_t len = 0;")
    output.append("    char *out = (char *)ailang_safe_malloc(cap);")
    output.append("    out[0] = '\\0';")
    output.append("    int64_t cmd_count = arg_counts.length;")
    output.append(
        "    if (cmd_count <= 0 || redir_counts.length < cmd_count) { ailang_process_last_capture_status_value = 127; return out; }"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        "    intptr_t *pids = (intptr_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(intptr_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int capture_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append(
        "        const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append(
        "        if (!argv) { ailang_process_last_capture_status_value = 127; ailang_safe_free(pids); return out; }"
    )
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (_pipe(pfd, 65536, _O_BINARY) != 0) { ailang_process_last_capture_status_value = 126; ailang_process_argv_slice_free(argv, argc); ailang_safe_free(pids); return out; }"
    )
    output.append("        int saved[3];")
    output.append("        for (int j = 0; j < 3; j++) saved[j] = _dup(j);")
    output.append("        int setup_ok = 1;")
    output.append(
        "        if (prev_read >= 0 && _dup2(prev_read, 0) != 0) setup_ok = 0;"
    )
    output.append("        if (_dup2(pfd[1], 1) != 0) setup_ok = 0;")
    output.append(
        "        if (setup_ok && prev_read >= 0) ailang_process_sync_std_handle(0);"
    )
    output.append("        if (setup_ok) ailang_process_sync_std_handle(1);")
    output.append(
        "        ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "        ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "        if (setup_ok && ailang_apply_redirs(op_slice, target_slice) != 0) setup_ok = 0;"
    )
    output.append("        if (!setup_ok) {")
    output.append(
        "            for (int j = 0; j < 3; j++) if (saved[j] >= 0) { _dup2(saved[j], j); ailang_process_sync_std_handle(j); _close(saved[j]); }"
    )
    output.append("            if (prev_read >= 0) _close(prev_read);")
    output.append("            if (pfd[0] >= 0) _close(pfd[0]);")
    output.append("            if (pfd[1] >= 0) _close(pfd[1]);")
    output.append(
        "            ailang_process_last_capture_status_value = 126; ailang_process_argv_slice_free(argv, argc); ailang_safe_free(pids); return out;"
    )
    output.append("        }")
    output.append(
        "        char **env_names = NULL; char **env_old_values = NULL; int *env_had_old = NULL;"
    )
    output.append(
        "        ailang_process_env_apply_windows(env, &env_names, &env_old_values, &env_had_old);"
    )
    output.append(
        "        intptr_t pid = _spawnvp(_P_NOWAIT, args.data[arg_off], argv);"
    )
    output.append(
        "        ailang_process_env_restore_windows(env, env_names, env_old_values, env_had_old);"
    )
    output.append(
        "        for (int j = 0; j < 3; j++) if (saved[j] >= 0) { _dup2(saved[j], j); ailang_process_sync_std_handle(j); _close(saved[j]); }"
    )
    output.append("        if (prev_read >= 0) _close(prev_read);")
    output.append("        _close(pfd[1]);")
    output.append(
        "        if (i + 1 == cmd_count) capture_read = pfd[0]; else prev_read = pfd[0];"
    )
    output.append("        ailang_process_argv_slice_free(argv, argc);")
    output.append(
        "        if (pid < 0) { if (capture_read >= 0) _close(capture_read); if (prev_read >= 0) _close(prev_read); ailang_process_last_exec_errno_value = (int64_t)errno; ailang_process_last_capture_status_value = 127; ailang_safe_free(pids); return out; }"
    )
    output.append("        pids[spawned++] = pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    char buf[4096];")
    output.append("    int got = 0;")
    output.append(
        "    if (capture_read >= 0) { while ((got = _read(capture_read, buf, (unsigned)sizeof(buf))) > 0) ailang_process_capture_append(&out, &len, &cap, buf, (size_t)got); _close(capture_read); }"
    )
    output.append("    int status = 0;")
    output.append("    int last_status = 0;")
    output.append(
        "    for (int64_t i = 0; i < spawned; i++) { if (_cwait(&status, pids[i], 0) >= 0) last_status = status; }"
    )
    output.append(
        "    ailang_process_last_capture_status_value = (int64_t)(last_status > 255 ? (last_status / 256) : last_status);"
    )
    output.append("    ailang_safe_free(pids);")
    output.append("    return out;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    pid_t *pids = (pid_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(pid_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int capture_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (pipe(pfd) < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); ailang_process_last_capture_status_value = 126; ailang_safe_free(pids); return out; }"
    )
    output.append("        pid_t pid = fork();")
    output.append(
        "        if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); close(pfd[0]); close(pfd[1]); ailang_process_last_capture_status_value = 126; ailang_safe_free(pids); return out; }"
    )
    output.append("        if (pid == 0) {")
    output.append("            if (execerr[0] >= 0) close(execerr[0]);")
    output.append(
        "            if (prev_read >= 0) { if (dup2(prev_read, 0) < 0) _exit(126); close(prev_read); }"
    )
    output.append("            if (dup2(pfd[1], 1) < 0) _exit(126);")
    output.append("            close(pfd[0]); close(pfd[1]);")
    output.append(
        "            ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            if (ailang_apply_redirs(op_slice, target_slice) != 0) _exit(126);"
    )
    output.append(
        "            const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append("            if (!argv) _exit(127);")
    output.append("            ailang_process_child_apply_env(env);")
    output.append("            ailang_process_exec_argv((char * const *)argv);")
    output.append("            ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("            _exit(127);")
    output.append("        }")
    output.append("        if (prev_read >= 0) close(prev_read);")
    output.append("        close(pfd[1]);")
    output.append(
        "        if (i + 1 == cmd_count) capture_read = pfd[0]; else prev_read = pfd[0];"
    )
    output.append("        pids[spawned++] = pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    char buf[4096];")
    output.append("    ssize_t got = 0;")
    output.append(
        "    if (capture_read >= 0) { while ((got = read(capture_read, buf, sizeof(buf))) > 0) ailang_process_capture_append(&out, &len, &cap, buf, (size_t)got); close(capture_read); }"
    )
    output.append("    int status = 0;")
    output.append("    int last_status = 0;")
    output.append(
        "    for (int64_t i = 0; i < spawned; i++) { while (waitpid(pids[i], &status, 0) < 0 && errno == EINTR) {} if (i + 1 == spawned) last_status = status; }"
    )
    output.append("    ailang_safe_free(pids);")
    output.append(
        "    if (WIFEXITED(last_status)) ailang_process_last_capture_status_value = (int64_t)WEXITSTATUS(last_status);"
    )
    output.append(
        "    else if (WIFSIGNALED(last_status)) ailang_process_last_capture_status_value = (int64_t)(128 + WTERMSIG(last_status));"
    )
    output.append("    else ailang_process_last_capture_status_value = 126;")
    output.append("    return out;")
    output.append("#else")
    output.append(
        "    (void)args; (void)arg_counts; (void)env; (void)ops; (void)targets; (void)redir_counts;"
    )
    output.append("    ailang_process_last_capture_status_value = 126;")
    output.append("    return out;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static char *ailang_process_capture_pipeline_argv_redirs("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array ops, ailang_str_array targets, ailang_dyn_array redir_counts) {"
    )
    output.append("    ailang_str_array env = { NULL, 0, 0 };")
    output.append(
        "    return ailang_process_capture_pipeline_argv_env_redirs("
        "args, arg_counts, env, ops, targets, redir_counts);"
    )
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_pipeline_argv_env_redirs("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array env, ailang_str_array ops, "
        "ailang_str_array targets, ailang_dyn_array redir_counts) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append("    int64_t cmd_count = arg_counts.length;")
    output.append(
        "    if (cmd_count <= 0 || redir_counts.length < cmd_count) return 127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        "    intptr_t *pids = (intptr_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(intptr_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append(
        "        const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append("        if (!argv) { ailang_safe_free(pids); return 127; }")
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (i + 1 < cmd_count && _pipe(pfd, 65536, _O_BINARY) != 0) { ailang_process_argv_slice_free(argv, argc); ailang_safe_free(pids); return 126; }"
    )
    output.append("        int saved[3];")
    output.append("        for (int j = 0; j < 3; j++) saved[j] = _dup(j);")
    output.append("        int setup_ok = 1;")
    output.append(
        "        if (prev_read >= 0 && _dup2(prev_read, 0) != 0) setup_ok = 0;"
    )
    output.append("        if (pfd[1] >= 0 && _dup2(pfd[1], 1) != 0) setup_ok = 0;")
    output.append(
        "        if (setup_ok && prev_read >= 0) ailang_process_sync_std_handle(0);"
    )
    output.append(
        "        if (setup_ok && pfd[1] >= 0) ailang_process_sync_std_handle(1);"
    )
    output.append(
        "        ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "        ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "        if (setup_ok && ailang_apply_redirs(op_slice, target_slice) != 0) setup_ok = 0;"
    )
    output.append("        if (!setup_ok) {")
    output.append(
        "            for (int j = 0; j < 3; j++) if (saved[j] >= 0) { _dup2(saved[j], j); ailang_process_sync_std_handle(j); _close(saved[j]); }"
    )
    output.append("            if (prev_read >= 0) _close(prev_read);")
    output.append("            if (pfd[0] >= 0) _close(pfd[0]);")
    output.append("            if (pfd[1] >= 0) _close(pfd[1]);")
    output.append(
        "            ailang_process_argv_slice_free(argv, argc); ailang_safe_free(pids); return 126;"
    )
    output.append("        }")
    output.append(
        "        char **env_names = NULL; char **env_old_values = NULL; int *env_had_old = NULL;"
    )
    output.append(
        "        ailang_process_env_apply_windows(env, &env_names, &env_old_values, &env_had_old);"
    )
    output.append(
        "        intptr_t pid = _spawnvp(_P_NOWAIT, args.data[arg_off], argv);"
    )
    output.append(
        "        ailang_process_env_restore_windows(env, env_names, env_old_values, env_had_old);"
    )
    output.append(
        "        for (int j = 0; j < 3; j++) if (saved[j] >= 0) { _dup2(saved[j], j); ailang_process_sync_std_handle(j); _close(saved[j]); }"
    )
    output.append("        if (prev_read >= 0) _close(prev_read);")
    output.append("        if (pfd[1] >= 0) _close(pfd[1]);")
    output.append("        prev_read = pfd[0];")
    output.append("        ailang_process_argv_slice_free(argv, argc);")
    output.append(
        "        if (pid < 0) { if (prev_read >= 0) _close(prev_read); ailang_process_last_exec_errno_value = (int64_t)errno; ailang_safe_free(pids); return 127; }"
    )
    output.append("        pids[spawned++] = pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    if (prev_read >= 0) _close(prev_read);")
    output.append("    int status = 0;")
    output.append("    int last_status = 0;")
    output.append(
        "    for (int64_t i = 0; i < spawned; i++) { if (_cwait(&status, pids[i], 0) >= 0) last_status = status; }"
    )
    output.append("    ailang_safe_free(pids);")
    output.append(
        "    return (int64_t)(last_status > 255 ? (last_status / 256) : last_status);"
    )
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    pid_t *pids = (pid_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(pid_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (i + 1 < cmd_count && pipe(pfd) < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); ailang_safe_free(pids); return 126; }"
    )
    output.append("        pid_t pid = fork();")
    output.append(
        "        if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); if (pfd[0] >= 0) close(pfd[0]); if (pfd[1] >= 0) close(pfd[1]); ailang_safe_free(pids); return 126; }"
    )
    output.append("        if (pid == 0) {")
    output.append("            if (execerr[0] >= 0) close(execerr[0]);")
    output.append(
        "            if (prev_read >= 0) { if (dup2(prev_read, 0) < 0) _exit(126); close(prev_read); }"
    )
    output.append(
        "            if (pfd[1] >= 0) { if (dup2(pfd[1], 1) < 0) _exit(126); close(pfd[1]); }"
    )
    output.append("            if (pfd[0] >= 0) close(pfd[0]);")
    output.append(
        "            ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            if (ailang_apply_redirs(op_slice, target_slice) != 0) _exit(126);"
    )
    output.append(
        "            const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append("            if (!argv) _exit(127);")
    output.append("            ailang_process_child_apply_env(env);")
    output.append("            ailang_process_exec_argv((char * const *)argv);")
    output.append("            ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("            _exit(127);")
    output.append("        }")
    output.append("        if (prev_read >= 0) close(prev_read);")
    output.append("        if (pfd[1] >= 0) close(pfd[1]);")
    output.append("        prev_read = pfd[0];")
    output.append("        pids[spawned++] = pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    if (prev_read >= 0) close(prev_read);")
    output.append("    int status = 0;")
    output.append("    int last_status = 0;")
    output.append("    for (int64_t i = 0; i < spawned; i++) {")
    output.append(
        "        while (waitpid(pids[i], &status, 0) < 0 && errno == EINTR) {}"
    )
    output.append("        if (i + 1 == spawned) last_status = status;")
    output.append("    }")
    output.append("    ailang_safe_free(pids);")
    output.append(
        "    if (WIFEXITED(last_status)) return (int64_t)WEXITSTATUS(last_status);"
    )
    output.append(
        "    if (WIFSIGNALED(last_status)) return (int64_t)(128 + WTERMSIG(last_status));"
    )
    output.append("    return 126;")
    output.append("#else")
    output.append(
        "    (void)args; (void)arg_counts; (void)env; (void)ops; (void)targets; (void)redir_counts;"
    )
    output.append("    return 126;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_spawn_pipeline_argv_env_redirs("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array env, ailang_str_array ops, "
        "ailang_str_array targets, ailang_dyn_array redir_counts) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append("    int64_t cmd_count = arg_counts.length;")
    output.append(
        "    if (cmd_count <= 0 || redir_counts.length < cmd_count) return -127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append(
        "    int64_t *pids = (int64_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(int64_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append(
        "        const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append("        if (!argv) { ailang_safe_free(pids); return -127; }")
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (i + 1 < cmd_count && _pipe(pfd, 65536, _O_BINARY) != 0) { ailang_process_argv_slice_free(argv, argc); ailang_safe_free(pids); return -126; }"
    )
    output.append("        int saved[3];")
    output.append("        for (int j = 0; j < 3; j++) saved[j] = _dup(j);")
    output.append("        int setup_ok = 1;")
    output.append(
        "        if (prev_read >= 0 && _dup2(prev_read, 0) != 0) setup_ok = 0;"
    )
    output.append("        if (pfd[1] >= 0 && _dup2(pfd[1], 1) != 0) setup_ok = 0;")
    output.append(
        "        if (setup_ok && prev_read >= 0) ailang_process_sync_std_handle(0);"
    )
    output.append(
        "        if (setup_ok && pfd[1] >= 0) ailang_process_sync_std_handle(1);"
    )
    output.append(
        "        ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "        ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "        if (setup_ok && ailang_apply_redirs(op_slice, target_slice) != 0) setup_ok = 0;"
    )
    output.append("        if (!setup_ok) {")
    output.append(
        "            for (int j = 0; j < 3; j++) if (saved[j] >= 0) { _dup2(saved[j], j); ailang_process_sync_std_handle(j); _close(saved[j]); }"
    )
    output.append("            if (prev_read >= 0) _close(prev_read);")
    output.append("            if (pfd[0] >= 0) _close(pfd[0]);")
    output.append("            if (pfd[1] >= 0) _close(pfd[1]);")
    output.append(
        "            ailang_process_argv_slice_free(argv, argc); ailang_safe_free(pids); return -126;"
    )
    output.append("        }")
    output.append(
        "        char **env_names = NULL; char **env_old_values = NULL; int *env_had_old = NULL;"
    )
    output.append(
        "        ailang_process_env_apply_windows(env, &env_names, &env_old_values, &env_had_old);"
    )
    output.append(
        "        intptr_t handle = _spawnvp(_P_NOWAIT, args.data[arg_off], argv);"
    )
    output.append(
        "        ailang_process_env_restore_windows(env, env_names, env_old_values, env_had_old);"
    )
    output.append(
        "        for (int j = 0; j < 3; j++) if (saved[j] >= 0) { _dup2(saved[j], j); ailang_process_sync_std_handle(j); _close(saved[j]); }"
    )
    output.append("        if (prev_read >= 0) _close(prev_read);")
    output.append("        if (pfd[1] >= 0) _close(pfd[1]);")
    output.append("        prev_read = pfd[0];")
    output.append("        ailang_process_argv_slice_free(argv, argc);")
    output.append("        if (handle < 0) {")
    output.append("            if (prev_read >= 0) _close(prev_read);")
    output.append("            ailang_process_last_exec_errno_value = (int64_t)errno;")
    output.append("            ailang_safe_free(pids);")
    output.append("            return -127;")
    output.append("        }")
    output.append("        DWORD child_pid = GetProcessId((HANDLE)handle);")
    output.append("        if (!child_pid) {")
    output.append("            if (prev_read >= 0) _close(prev_read);")
    output.append("            CloseHandle((HANDLE)handle);")
    output.append("            ailang_safe_free(pids);")
    output.append("            return -127;")
    output.append("        }")
    output.append("        ailang_process_track_windows(child_pid, (HANDLE)handle);")
    output.append("        pids[spawned++] = (int64_t)child_pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    if (prev_read >= 0) _close(prev_read);")
    output.append("    int64_t last_pid = spawned > 0 ? pids[spawned - 1] : -127;")
    output.append("    ailang_safe_free(pids);")
    output.append("    return last_pid;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    pid_t *pids = (pid_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(pid_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (i + 1 < cmd_count && pipe(pfd) < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); ailang_safe_free(pids); return -126; }"
    )
    output.append("        pid_t pid = fork();")
    output.append(
        "        if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); if (pfd[0] >= 0) close(pfd[0]); if (pfd[1] >= 0) close(pfd[1]); ailang_safe_free(pids); return -126; }"
    )
    output.append("        if (pid == 0) {")
    output.append("            if (execerr[0] >= 0) close(execerr[0]);")
    output.append(
        "            if (prev_read >= 0) { if (dup2(prev_read, 0) < 0) _exit(126); close(prev_read); }"
    )
    output.append(
        "            if (pfd[1] >= 0) { if (dup2(pfd[1], 1) < 0) _exit(126); close(pfd[1]); }"
    )
    output.append("            if (pfd[0] >= 0) close(pfd[0]);")
    output.append(
        "            ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            if (ailang_apply_redirs(op_slice, target_slice) != 0) _exit(126);"
    )
    output.append(
        "            const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append("            if (!argv) _exit(127);")
    output.append("            ailang_process_child_apply_env(env);")
    output.append("            ailang_process_exec_argv((char * const *)argv);")
    output.append("            ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("            _exit(127);")
    output.append("        }")
    output.append("        if (prev_read >= 0) close(prev_read);")
    output.append("        if (pfd[1] >= 0) close(pfd[1]);")
    output.append("        prev_read = pfd[0];")
    output.append("        pids[spawned++] = pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    if (prev_read >= 0) close(prev_read);")
    output.append(
        "    if (ailang_process_last_exec_errno_value != 0) { ailang_safe_free(pids); return -127; }"
    )
    output.append(
        "    int64_t last_pid = spawned > 0 ? (int64_t)pids[spawned - 1] : -127;"
    )
    output.append("    ailang_safe_free(pids);")
    output.append("    return last_pid;")
    output.append("#else")
    output.append(
        "    (void)args; (void)arg_counts; (void)env; (void)ops; (void)targets; (void)redir_counts;"
    )
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_spawn_pipeline_argv_env_redirs_pgrp("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array env, ailang_str_array ops, "
        "ailang_str_array targets, ailang_dyn_array redir_counts, int64_t pgid) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append("    int64_t cmd_count = arg_counts.length;")
    output.append(
        "    if (cmd_count <= 0 || redir_counts.length < cmd_count) return -127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)pgid;")
    output.append(
        "    return ailang_process_spawn_pipeline_argv_env_redirs("
        "args, arg_counts, env, ops, targets, redir_counts);"
    )
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    pid_t *pids = (pid_t *)ailang_safe_malloc((size_t)cmd_count * sizeof(pid_t));"
    )
    output.append("    int prev_read = -1;")
    output.append("    int64_t arg_off = 0;")
    output.append("    int64_t redir_off = 0;")
    output.append("    int64_t spawned = 0;")
    output.append("    pid_t target_pgrp = pgid > 0 ? (pid_t)pgid : 0;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    for (int64_t i = 0; i < cmd_count; i++) {")
    output.append("        int64_t argc = arg_counts.data[i];")
    output.append("        int64_t rcount = redir_counts.data[i];")
    output.append("        int pfd[2] = { -1, -1 };")
    output.append(
        "        if (i + 1 < cmd_count && pipe(pfd) < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); ailang_safe_free(pids); return -126; }"
    )
    output.append("        pid_t pid = fork();")
    output.append(
        "        if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); if (prev_read >= 0) close(prev_read); if (pfd[0] >= 0) close(pfd[0]); if (pfd[1] >= 0) close(pfd[1]); ailang_safe_free(pids); return -126; }"
    )
    output.append("        if (pid == 0) {")
    output.append("            if (execerr[0] >= 0) close(execerr[0]);")
    output.append("            if (pgid >= 0 && setpgid(0, target_pgrp) != 0) {")
    output.append("                ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("                _exit(126);")
    output.append("            }")
    output.append(
        "            if (prev_read >= 0) { if (dup2(prev_read, 0) < 0) _exit(126); close(prev_read); }"
    )
    output.append(
        "            if (pfd[1] >= 0) { if (dup2(pfd[1], 1) < 0) _exit(126); close(pfd[1]); }"
    )
    output.append("            if (pfd[0] >= 0) close(pfd[0]);")
    output.append(
        "            ailang_str_array op_slice = { ops.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            ailang_str_array target_slice = { targets.data + redir_off, rcount, rcount };"
    )
    output.append(
        "            if (ailang_apply_redirs(op_slice, target_slice) != 0) _exit(126);"
    )
    output.append(
        "            const char **argv = ailang_process_argv_slice(args, arg_off, argc);"
    )
    output.append("            if (!argv) _exit(127);")
    output.append("            ailang_process_child_apply_env(env);")
    output.append("            ailang_process_exec_argv((char * const *)argv);")
    output.append("            ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("            _exit(127);")
    output.append("        }")
    output.append("        if (pgid >= 0) {")
    output.append("            if (target_pgrp == 0) target_pgrp = pid;")
    output.append("            (void)setpgid(pid, target_pgrp);")
    output.append("        }")
    output.append("        if (prev_read >= 0) close(prev_read);")
    output.append("        if (pfd[1] >= 0) close(pfd[1]);")
    output.append("        prev_read = pfd[0];")
    output.append("        pids[spawned++] = pid;")
    output.append("        arg_off += argc;")
    output.append("        redir_off += rcount;")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    if (prev_read >= 0) close(prev_read);")
    output.append("    if (ailang_process_last_exec_errno_value != 0) {")
    output.append(
        "        for (int64_t i = 0; i < spawned; i++) { int status = 0; while (waitpid(pids[i], &status, 0) < 0 && errno == EINTR) {} }"
    )
    output.append("        ailang_safe_free(pids);")
    output.append("        return -127;")
    output.append("    }")
    output.append(
        "    int64_t last_pid = spawned > 0 ? (int64_t)pids[spawned - 1] : -127;"
    )
    output.append("    ailang_safe_free(pids);")
    output.append("    return last_pid;")
    output.append("#else")
    output.append(
        "    (void)args; (void)arg_counts; (void)env; (void)ops; (void)targets; (void)redir_counts; (void)pgid;"
    )
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_spawn_pipeline_argv_redirs("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array ops, ailang_str_array targets, ailang_dyn_array redir_counts) {"
    )
    output.append("    ailang_str_array env = { NULL, 0, 0 };")
    output.append(
        "    return ailang_process_spawn_pipeline_argv_env_redirs("
        "args, arg_counts, env, ops, targets, redir_counts);"
    )
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_pipeline_argv_redirs("
        "ailang_str_array args, ailang_dyn_array arg_counts, "
        "ailang_str_array ops, ailang_str_array targets, ailang_dyn_array redir_counts) {"
    )
    output.append("    ailang_str_array env = { NULL, 0, 0 };")
    output.append(
        "    return ailang_process_pipeline_argv_env_redirs("
        "args, arg_counts, env, ops, targets, redir_counts);"
    )
    output.append("}")
    output.append("")

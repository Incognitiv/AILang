"""Process lifecycle runtime C emitter."""

from __future__ import annotations


def _emit_process_lifecycle(output: list[str]) -> None:
    output.append(
        "static int64_t ailang_process_run_argv_env_redirs("
        "ailang_str_array args, ailang_str_array env, "
        "ailang_str_array ops, ailang_str_array targets);"
    )
    output.append("")
    output.append("static int64_t ailang_process_status_from_wait_status(int status) {")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);")
    output.append(
        "    if (WIFSIGNALED(status)) return (int64_t)(128 + WTERMSIG(status));"
    )
    output.append("    return 126;")
    output.append("#else")
    output.append("    return (int64_t)(status > 255 ? (status / 256) : status);")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("static int64_t ailang_process_event_from_wait_status(int status) {")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("#ifdef WIFSTOPPED")
    output.append(
        "    if (WIFSTOPPED(status)) return (int64_t)(-1000 - WSTOPSIG(status));"
    )
    output.append("#endif")
    output.append("#ifdef WIFCONTINUED")
    output.append("    if (WIFCONTINUED(status)) return -1;")
    output.append("#endif")
    output.append("#endif")
    output.append("    return ailang_process_status_from_wait_status(status);")
    output.append("}")
    output.append("")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("#ifndef AILANG_PROCESS_TABLE_MAX")
    output.append("#define AILANG_PROCESS_TABLE_MAX 512")
    output.append("#endif")
    output.append("static DWORD ailang_process_table_pids[AILANG_PROCESS_TABLE_MAX];")
    output.append(
        "static HANDLE ailang_process_table_handles[AILANG_PROCESS_TABLE_MAX];"
    )
    output.append(
        "static void ailang_process_track_windows(DWORD pid, HANDLE handle) {"
    )
    output.append("    if (!pid || !handle) return;")
    output.append("    for (int i = 0; i < AILANG_PROCESS_TABLE_MAX; i++) {")
    output.append("        if (ailang_process_table_pids[i] == pid) {")
    output.append(
        "            if (ailang_process_table_handles[i]) CloseHandle(ailang_process_table_handles[i]);"
    )
    output.append("            ailang_process_table_handles[i] = handle;")
    output.append("            return;")
    output.append("        }")
    output.append("    }")
    output.append("    for (int i = 0; i < AILANG_PROCESS_TABLE_MAX; i++) {")
    output.append("        if (ailang_process_table_pids[i] == 0) {")
    output.append("            ailang_process_table_pids[i] = pid;")
    output.append("            ailang_process_table_handles[i] = handle;")
    output.append("            return;")
    output.append("        }")
    output.append("    }")
    output.append("    CloseHandle(handle);")
    output.append("}")
    output.append("static HANDLE ailang_process_peek_windows(DWORD pid) {")
    output.append("    if (!pid) return NULL;")
    output.append("    for (int i = 0; i < AILANG_PROCESS_TABLE_MAX; i++) {")
    output.append(
        "        if (ailang_process_table_pids[i] == pid) return ailang_process_table_handles[i];"
    )
    output.append("    }")
    output.append("    return NULL;")
    output.append("}")
    output.append("static HANDLE ailang_process_take_windows(DWORD pid) {")
    output.append("    if (!pid) return NULL;")
    output.append("    for (int i = 0; i < AILANG_PROCESS_TABLE_MAX; i++) {")
    output.append("        if (ailang_process_table_pids[i] == pid) {")
    output.append("            HANDLE handle = ailang_process_table_handles[i];")
    output.append("            ailang_process_table_pids[i] = 0;")
    output.append("            ailang_process_table_handles[i] = NULL;")
    output.append("            return handle;")
    output.append("        }")
    output.append("    }")
    output.append("    return NULL;")
    output.append("}")
    output.append("#endif")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_spawn_argv_env_redirs("
        "ailang_str_array args, ailang_str_array env, ailang_str_array ops, ailang_str_array targets) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append(
        "    if (args.length <= 0 || !args.data || !args.data[0]) return -127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
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
    output.append("    int setup_ok = ailang_apply_redirs(ops, targets) == 0;")
    output.append("    intptr_t process_handle = -1;")
    output.append("    DWORD child_pid = 0;")
    output.append("    if (setup_ok) {")
    output.append(
        "        const char **argv = ailang_windows_spawn_argv_from_str_array(args);"
    )
    output.append("        process_handle = _spawnvp(_P_NOWAIT, args.data[0], argv);")
    output.append(
        "        if (process_handle < 0) ailang_process_last_exec_errno_value = (int64_t)errno;"
    )
    output.append("        else {")
    output.append("            child_pid = GetProcessId((HANDLE)process_handle);")
    output.append(
        "            if (child_pid) ailang_process_track_windows(child_pid, (HANDLE)process_handle);"
    )
    output.append("            else CloseHandle((HANDLE)process_handle);")
    output.append("        }")
    output.append("        ailang_windows_spawn_argv_free(argv, args.length);")
    output.append("    }")
    output.append(
        "    for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
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
    output.append("    if (!setup_ok) return -126;")
    output.append("    return process_handle < 0 ? -127 : (int64_t)child_pid;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    char **argv = (char **)ailang_safe_malloc((size_t)(args.length + 1) * sizeof(char *));"
    )
    output.append(
        "    for (int64_t i = 0; i < args.length; i++) argv[i] = (char *)args.data[i];"
    )
    output.append("    argv[args.length] = NULL;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    pid_t pid = fork();")
    output.append(
        "    if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); ailang_safe_free(argv); return -126; }"
    )
    output.append("    if (pid == 0) {")
    output.append("        if (execerr[0] >= 0) close(execerr[0]);")
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
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    ailang_safe_free(argv);")
    output.append("    if (ailang_process_last_exec_errno_value != 0) {")
    output.append("        int status = 0;")
    output.append("        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}")
    output.append("        return -127;")
    output.append("    }")
    output.append("    return (int64_t)pid;")
    output.append("#else")
    output.append("    (void)args; (void)env; (void)ops; (void)targets;")
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_spawn_argv_env_redirs_pgrp("
        "ailang_str_array args, ailang_str_array env, ailang_str_array ops, "
        "ailang_str_array targets, int64_t pgid) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append(
        "    if (args.length <= 0 || !args.data || !args.data[0]) return -127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    (void)pgid;")
    output.append(
        "    return ailang_process_spawn_argv_env_redirs(args, env, ops, targets);"
    )
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    char **argv = (char **)ailang_safe_malloc((size_t)(args.length + 1) * sizeof(char *));"
    )
    output.append(
        "    for (int64_t i = 0; i < args.length; i++) argv[i] = (char *)args.data[i];"
    )
    output.append("    argv[args.length] = NULL;")
    output.append("    int execerr[2] = { -1, -1 };")
    output.append("    (void)ailang_process_make_exec_errno_pipe(execerr);")
    output.append("    pid_t pid = fork();")
    output.append(
        "    if (pid < 0) { if (execerr[0] >= 0) close(execerr[0]); if (execerr[1] >= 0) close(execerr[1]); ailang_safe_free(argv); return -126; }"
    )
    output.append("    if (pid == 0) {")
    output.append("        if (execerr[0] >= 0) close(execerr[0]);")
    output.append("        if (pgid >= 0 && setpgid(0, (pid_t)pgid) != 0) {")
    output.append("            ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("            _exit(126);")
    output.append("        }")
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
    output.append("        ailang_process_exec_argv((char * const *)argv);")
    output.append("        ailang_process_child_report_exec_errno(execerr[1]);")
    output.append("        _exit(127);")
    output.append("    }")
    output.append("    if (execerr[1] >= 0) close(execerr[1]);")
    output.append("    if (pgid >= 0) {")
    output.append("        pid_t target_pgrp = (pgid == 0) ? pid : (pid_t)pgid;")
    output.append("        (void)setpgid(pid, target_pgrp);")
    output.append("    }")
    output.append("    ailang_process_parent_read_exec_errno(execerr[0]);")
    output.append("    ailang_safe_free(argv);")
    output.append("    if (ailang_process_last_exec_errno_value != 0) {")
    output.append("        int status = 0;")
    output.append("        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}")
    output.append("        return -127;")
    output.append("    }")
    output.append("    return (int64_t)pid;")
    output.append("#else")
    output.append("    (void)args; (void)env; (void)ops; (void)targets; (void)pgid;")
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_wait_pid(int64_t pid) {")
    output.append("    if (pid <= 0) return 127;")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    HANDLE handle = ailang_process_take_windows((DWORD)pid);")
    output.append("    int tracked = 1;")
    output.append("    if (!handle) {")
    output.append("        tracked = 0;")
    output.append(
        "        handle = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, (DWORD)pid);"
    )
    output.append("    }")
    output.append(
        "    if (!handle) { ailang_process_last_exec_errno_value = (int64_t)GetLastError(); return 127; }"
    )
    output.append("    DWORD wait_rc = WaitForSingleObject(handle, INFINITE);")
    output.append(
        "    if (wait_rc != WAIT_OBJECT_0) { CloseHandle(handle); return 127; }"
    )
    output.append("    DWORD exit_code = 126;")
    output.append("    if (!GetExitCodeProcess(handle, &exit_code)) exit_code = 127;")
    output.append("    CloseHandle(handle);")
    output.append("    return (int64_t)exit_code;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    int status = 0;")
    output.append("    for (;;) {")
    output.append(
        "        if (waitpid((pid_t)pid, &status, 0) >= 0) return ailang_process_status_from_wait_status(status);"
    )
    output.append("        if (errno == EINTR) continue;")
    output.append("        ailang_process_last_exec_errno_value = (int64_t)errno;")
    output.append("        return 127;")
    output.append("    }")
    output.append("#else")
    output.append("    (void)pid; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_wait_pid_event(int64_t pid) {"
    )
    output.append("    if (pid <= 0) return 127;")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    return ailang_process_wait_pid(pid);")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    int status = 0;")
    output.append("    int options = 0;")
    output.append("#ifdef WUNTRACED")
    output.append("    options |= WUNTRACED;")
    output.append("#endif")
    output.append("    for (;;) {")
    output.append(
        "        if (waitpid((pid_t)pid, &status, options) >= 0) return ailang_process_event_from_wait_status(status);"
    )
    output.append("        if (errno == EINTR) continue;")
    output.append("        ailang_process_last_exec_errno_value = (int64_t)errno;")
    output.append("        return 127;")
    output.append("    }")
    output.append("#else")
    output.append("    (void)pid; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("AILANG_UNUSED static int64_t ailang_process_poll_pid(int64_t pid) {")
    output.append("    if (pid <= 0) return 127;")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    HANDLE handle = ailang_process_peek_windows((DWORD)pid);")
    output.append("    int tracked = 1;")
    output.append("    if (!handle) {")
    output.append("        tracked = 0;")
    output.append(
        "        handle = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, (DWORD)pid);"
    )
    output.append("    }")
    output.append("    if (!handle) return 127;")
    output.append("    DWORD wait_rc = WaitForSingleObject(handle, 0);")
    output.append(
        "    if (wait_rc == WAIT_TIMEOUT) { if (!tracked) CloseHandle(handle); return -1; }"
    )
    output.append(
        "    if (wait_rc != WAIT_OBJECT_0) { if (!tracked) CloseHandle(handle); return 127; }"
    )
    output.append("    DWORD exit_code = 126;")
    output.append("    if (!GetExitCodeProcess(handle, &exit_code)) exit_code = 127;")
    output.append(
        "    if (tracked) { handle = ailang_process_take_windows((DWORD)pid); }"
    )
    output.append("    if (handle) CloseHandle(handle);")
    output.append("    return (int64_t)exit_code;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    int status = 0;")
    output.append("    int options = WNOHANG;")
    output.append("#ifdef WUNTRACED")
    output.append("    options |= WUNTRACED;")
    output.append("#endif")
    output.append("#ifdef WCONTINUED")
    output.append("    options |= WCONTINUED;")
    output.append("#endif")
    output.append("    pid_t rc = waitpid((pid_t)pid, &status, options);")
    output.append("    if (rc == 0) return -1;")
    output.append(
        "    if (rc < 0) { ailang_process_last_exec_errno_value = (int64_t)errno; return 127; }"
    )
    output.append("#ifdef WIFCONTINUED")
    output.append("    if (WIFCONTINUED(status)) return -1;")
    output.append("#endif")
    output.append("#ifdef WIFSTOPPED")
    output.append(
        "    if (WIFSTOPPED(status)) return (int64_t)(-1000 - WSTOPSIG(status));"
    )
    output.append("#endif")
    output.append("    return ailang_process_status_from_wait_status(status);")
    output.append("#else")
    output.append("    (void)pid; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_kill_pid(int64_t pid, int64_t signo) {"
    )
    output.append("    if (pid <= 0) return 22;")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("    if (signo == 0) {")
    output.append(
        "        HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, (DWORD)pid);"
    )
    output.append("        if (h) { CloseHandle(h); return 0; }")
    output.append("        DWORD open_error = GetLastError();")
    output.append("        HANDLE raw = (HANDLE)(intptr_t)pid;")
    output.append("        if (!raw) return (int64_t)open_error;")
    output.append("        DWORD wait_rc = WaitForSingleObject(raw, 0);")
    output.append(
        "        if (wait_rc == WAIT_OBJECT_0 || wait_rc == WAIT_TIMEOUT) return 0;"
    )
    output.append(
        "        return open_error ? (int64_t)open_error : (int64_t)GetLastError();"
    )
    output.append("    }")
    output.append("    int opened = 1;")
    output.append("    HANDLE h = OpenProcess(PROCESS_TERMINATE, FALSE, (DWORD)pid);")
    output.append("    if (!h) { h = (HANDLE)(intptr_t)pid; opened = 0; }")
    output.append("    if (!h) return (int64_t)GetLastError();")
    output.append("    int ok = TerminateProcess(h, (UINT)(128 + signo));")
    output.append("    if (opened) CloseHandle(h);")
    output.append("    return ok ? 0 : (int64_t)GetLastError();")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    if (kill((pid_t)pid, (int)signo) == 0) return 0;")
    output.append("    return (int64_t)errno;")
    output.append("#else")
    output.append("    (void)pid; (void)signo; return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_exec_replace_argv_env_redirs("
        "ailang_str_array args, ailang_str_array env, ailang_str_array ops, ailang_str_array targets) {"
    )
    output.append("    if (args.length <= 0 || !args.data || !args.data[0]) return 0;")
    output.append(
        "#if defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append("    if (ailang_apply_redirs(ops, targets) != 0) return 126;")
    output.append("    for (int64_t i = 0; i < env.length; i++) {")
    output.append('        const char *pair = env.data[i] ? env.data[i] : "";')
    output.append("        const char *eq = strchr(pair, '=');")
    output.append("        char *name = ailang_env_name_copy(pair);")
    output.append("        if (eq && name) setenv(name, eq + 1, 1);")
    output.append("        if (name) ailang_safe_free(name);")
    output.append("    }")
    output.append(
        "    char **argv = (char **)ailang_safe_malloc((size_t)(args.length + 1) * sizeof(char *));"
    )
    output.append(
        "    for (int64_t i = 0; i < args.length; i++) argv[i] = (char *)args.data[i];"
    )
    output.append("    argv[args.length] = NULL;")
    output.append("    ailang_process_exec_argv((char * const *)argv);")
    output.append("    ailang_process_last_exec_errno_value = (int64_t)errno;")
    output.append("    ailang_safe_free(argv);")
    output.append("    return errno == ENOENT ? 127 : 126;")
    output.append("#else")
    output.append(
        "    return ailang_process_run_argv_env_redirs(args, env, ops, targets);"
    )
    output.append("#endif")
    output.append("}")
    output.append("")
    output.append("#if defined(AILANG_WINDOWS)")
    output.append("static void ailang_process_env_apply_windows(")
    output.append(
        "    ailang_str_array env, char ***names_out, char ***old_values_out, int **had_old_out) {"
    )
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
    output.append("    *names_out = names;")
    output.append("    *old_values_out = old_values;")
    output.append("    *had_old_out = had_old;")
    output.append("}")
    output.append("")
    output.append("static void ailang_process_env_restore_windows(")
    output.append(
        "    ailang_str_array env, char **names, char **old_values, int *had_old) {"
    )
    output.append("    for (int64_t i = 0; i < env.length; i++) {")
    output.append("        if (names[i]) {")
    output.append(
        '            if (had_old[i]) _putenv_s(names[i], old_values[i] ? old_values[i] : "");'
    )
    output.append('            else _putenv_s(names[i], "");')
    output.append("        }")
    output.append("        if (names[i]) ailang_safe_free(names[i]);")
    output.append("        if (old_values[i]) ailang_safe_free(old_values[i]);")
    output.append("    }")
    output.append("    ailang_safe_free(names);")
    output.append("    ailang_safe_free(old_values);")
    output.append("    ailang_safe_free(had_old);")
    output.append("}")
    output.append("#else")
    output.append("static void ailang_process_child_apply_env(ailang_str_array env) {")
    output.append("    for (int64_t i = 0; i < env.length; i++) {")
    output.append('        const char *pair = env.data[i] ? env.data[i] : "";')
    output.append("        const char *eq = strchr(pair, '=');")
    output.append("        char *name = ailang_env_name_copy(pair);")
    output.append("        if (eq && name) setenv(name, eq + 1, 1);")
    output.append("        if (name) ailang_safe_free(name);")
    output.append("    }")
    output.append("}")
    output.append("#endif")
    output.append("")
    output.append(
        "AILANG_UNUSED static int64_t ailang_process_run_argv_env_redirs("
        "ailang_str_array args, ailang_str_array env, "
        "ailang_str_array ops, ailang_str_array targets) {"
    )
    output.append("    ailang_process_clear_exec_errno();")
    output.append(
        "    if (args.length <= 0 || !args.data || !args.data[0]) return 127;"
    )
    output.append("#if defined(AILANG_WINDOWS)")
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
    output.append("    int setup_ok = ailang_apply_redirs(ops, targets) == 0;")
    output.append("    intptr_t rc = -1;")
    output.append("    if (setup_ok) {")
    output.append(
        "        const char **argv = ailang_windows_spawn_argv_from_str_array(args);"
    )
    output.append("        rc = _spawnvp(_P_WAIT, args.data[0], argv);")
    output.append("        ailang_windows_spawn_argv_free(argv, args.length);")
    output.append("    }")
    output.append(
        "    for (int i = 0; i < 3; i++) if (saved[i] >= 0) { _dup2(saved[i], i); ailang_process_sync_std_handle(i); _close(saved[i]); }"
    )
    output.append("    for (int64_t i = 0; i < env.length; i++) {")
    output.append("        if (names[i]) {")
    output.append(
        '            if (had_old[i]) _putenv_s(names[i], old_values[i] ? old_values[i] : "");'
    )
    output.append('            else _putenv_s(names[i], "");')
    output.append("        }")
    output.append("        if (names[i]) ailang_safe_free(names[i]);")
    output.append("        if (old_values[i]) ailang_safe_free(old_values[i]);")
    output.append("    }")
    output.append(
        "    ailang_safe_free(names); ailang_safe_free(old_values); ailang_safe_free(had_old);"
    )
    output.append("    if (!setup_ok) return 126;")
    output.append(
        "    if (rc < 0) { ailang_process_last_exec_errno_value = (int64_t)errno; return 127; }"
    )
    output.append("    return (int64_t)rc;")
    output.append(
        "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
    )
    output.append(
        "    char **argv = (char **)ailang_safe_malloc((size_t)(args.length + 1) * sizeof(char *));"
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
    output.append("        for (int64_t i = 0; i < env.length; i++) {")
    output.append('            const char *pair = env.data[i] ? env.data[i] : "";')
    output.append("            const char *eq = strchr(pair, '=');")
    output.append("            char *name = ailang_env_name_copy(pair);")
    output.append("            if (eq && name) setenv(name, eq + 1, 1);")
    output.append("            if (name) ailang_safe_free(name);")
    output.append("        }")
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
    output.append("    (void)args; (void)env; (void)ops; (void)targets;")
    output.append("    return -38;")
    output.append("#endif")
    output.append("}")
    output.append("")

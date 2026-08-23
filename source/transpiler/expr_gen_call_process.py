"""C expression lowering for process identity builtins."""

from __future__ import annotations


def process_c_builtin_mappings():
    return {
        "getpid": lambda a: "ailang_getpid()",
        "getppid": lambda a: "ailang_getppid()",
        "getuid": lambda a: "ailang_getuid()",
        "geteuid": lambda a: "ailang_geteuid()",
        "getgid": lambda a: "ailang_getgid()",
        "getegid": lambda a: "ailang_getegid()",
        "getgeid": lambda a: "ailang_getegid()",
        "process_umask": lambda a: f"ailang_process_umask({a[0]})",
        "process_get_pgrp": lambda a: f"ailang_process_get_pgrp({a[0]})",
        "process_set_pgrp": lambda a: f"ailang_process_set_pgrp({a[0]}, {a[1]})",
        "process_kill_pgrp": lambda a: f"ailang_process_kill_pgrp({a[0]}, {a[1]})",
        "terminal_get_pgrp": lambda a: f"ailang_terminal_get_pgrp({a[0]})",
        "terminal_set_pgrp": lambda a: f"ailang_terminal_set_pgrp({a[0]}, {a[1]})",
        "process_run_argv": lambda a: f"ailang_process_run_argv({a[0]})",
        "process_run_argv_redirs": lambda a: (
            f"ailang_process_run_argv_redirs({a[0]}, {a[1]}, {a[2]})"
        ),
        "process_run_argv_env_redirs": lambda a: (
            f"ailang_process_run_argv_env_redirs({a[0]}, {a[1]}, {a[2]}, {a[3]})"
        ),
        "process_spawn_argv_env_redirs": lambda a: (
            f"ailang_process_spawn_argv_env_redirs({a[0]}, {a[1]}, {a[2]}, {a[3]})"
        ),
        "process_spawn_argv_env_redirs_pgrp": lambda a: (
            "ailang_process_spawn_argv_env_redirs_pgrp("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]})"
        ),
        "process_wait_pid": lambda a: f"ailang_process_wait_pid({a[0]})",
        "process_wait_pid_event": lambda a: f"ailang_process_wait_pid_event({a[0]})",
        "process_poll_pid": lambda a: f"ailang_process_poll_pid({a[0]})",
        "process_kill_pid": lambda a: f"ailang_process_kill_pid({a[0]}, {a[1]})",
        "process_exec_replace_argv_env_redirs": lambda a: (
            f"ailang_process_exec_replace_argv_env_redirs({a[0]}, {a[1]}, {a[2]}, {a[3]})"
        ),
        "signal_install": lambda a: f"ailang_signal_install({a[0]})",
        "signal_ignore": lambda a: f"ailang_signal_ignore({a[0]})",
        "signal_default": lambda a: f"ailang_signal_default({a[0]})",
        "signal_pending": lambda a: "ailang_signal_pending()",
        "signal_clear": lambda a: f"ailang_signal_clear({a[0]})",
        "signal_drain": lambda a: "ailang_signal_drain()",
        "signal_raise": lambda a: f"ailang_signal_raise({a[0]})",
        "process_pipe_argv_redirs": lambda a: (
            "ailang_process_pipe_argv_redirs("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]}, {a[5]})"
        ),
        "process_pipeline_argv_redirs": lambda a: (
            "ailang_process_pipeline_argv_redirs("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]})"
        ),
        "process_pipeline_argv_env_redirs": lambda a: (
            "ailang_process_pipeline_argv_env_redirs("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]}, {a[5]})"
        ),
        "process_spawn_pipeline_argv_env_redirs": lambda a: (
            "ailang_process_spawn_pipeline_argv_env_redirs("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]}, {a[5]})"
        ),
        "process_spawn_pipeline_argv_env_redirs_pgrp": lambda a: (
            "ailang_process_spawn_pipeline_argv_env_redirs_pgrp("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]}, {a[5]}, {a[6]})"
        ),
        "process_capture_pipeline_argv_redirs": lambda a: (
            "ailang_process_capture_pipeline_argv_redirs("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]})"
        ),
        "process_capture_pipeline_argv_env_redirs": lambda a: (
            "ailang_process_capture_pipeline_argv_env_redirs("
            f"{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]}, {a[5]})"
        ),
        "process_capture_argv_env_redirs": lambda a: (
            f"ailang_process_capture_argv_env_redirs({a[0]}, {a[1]}, {a[2]}, {a[3]})"
        ),
        "process_last_capture_status": lambda a: "ailang_process_last_capture_status()",
        "process_set_last_capture_status": lambda a: (
            f"ailang_process_set_last_capture_status({a[0]})"
        ),
        "process_last_exec_errno": lambda a: "ailang_process_last_exec_errno()",
        "process_errno_enoexec": lambda a: "ailang_process_errno_enoexec()",
        "process_errno_enoent": lambda a: "ailang_process_errno_enoent()",
        "process_errno_eacces": lambda a: "ailang_process_errno_eacces()",
        "process_errno_eperm": lambda a: "ailang_process_errno_eperm()",
    }

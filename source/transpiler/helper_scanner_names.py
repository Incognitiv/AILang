"""Helper name catalogs for runtime helper scanning."""

from __future__ import annotations

from transpiler.helper_scanner_win32 import WIN32_HELPER_NAMES

_WIN32_HELPER_NAMES = WIN32_HELPER_NAMES
_FD_HELPER_NAMES = (
    "fd_open",
    "fd_read",
    "fd_write",
    "fd_close",
    "fd_dup",
    "fd_dup2",
    "fd_tell",
    "fd_seek",
    "fd_flush",
)
_PROCESS_HELPER_NAMES = tuple(
    (
        "getpid getppid getuid geteuid getgid getegid getgeid "
        "process_umask "
        "process_run_argv process_run_argv_redirs process_run_argv_env_redirs "
        "process_spawn_argv_env_redirs process_spawn_argv_env_redirs_pgrp "
        "process_wait_pid process_wait_pid_event process_poll_pid "
        "process_kill_pid process_get_pgrp process_set_pgrp process_kill_pgrp "
        "terminal_get_pgrp terminal_set_pgrp process_exec_replace_argv_env_redirs "
        "process_pipe_argv_redirs "
        "process_pipeline_argv_redirs process_pipeline_argv_env_redirs "
        "process_spawn_pipeline_argv_env_redirs "
        "process_spawn_pipeline_argv_env_redirs_pgrp "
        "process_capture_pipeline_argv_redirs process_capture_pipeline_argv_env_redirs "
        "process_capture_argv_env_redirs "
        "process_last_capture_status process_set_last_capture_status "
        "process_last_exec_errno process_errno_enoexec "
        "process_errno_enoent process_errno_eacces process_errno_eperm "
        "signal_install signal_ignore signal_default signal_pending "
        "signal_clear signal_drain signal_raise"
    ).split()
)

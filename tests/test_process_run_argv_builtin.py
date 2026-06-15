from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"

def _compile_run(src: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "process_run_argv_probe.ail"
    out_stem = tmp_path / "process_run_argv_probe"
    source.write_text(src, encoding="utf-8")
    compile_proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(source),
            "--backend=c",
            "-o",
            str(out_stem),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert compile_proc.returncode == 0, compile_proc.stderr
    exe = out_stem.with_suffix(".exe") if os.name == "nt" else out_stem
    env = os.environ.copy()
    env["AILANG_LEAK_REPORT"] = "1"
    return subprocess.run(
        [str(exe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

def test_process_run_argv_returns_child_exit_status(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "exit")\n    args = str_array_push(args, "7")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "exit 7")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(4){setup}
    rc = process_run_argv(args)
    dealloc_str_array(args)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 7, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_run_argv_missing_command_is_127(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    args = str_array_new(1)
    args = str_array_push(args, "__ailang_missing_command_for_argv_test__")
    rc = process_run_argv(args)
    dealloc_str_array(args)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 127, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_last_exec_errno_reports_missing_command(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    args = str_array_new(1)
    args = str_array_push(args, "__ailang_missing_command_for_errno_test__")
    rc = process_run_argv(args)
    err = process_last_exec_errno()
    enoexec = process_errno_enoexec()
    dealloc_str_array(args)
    if rc == 127 and err > 0 and enoexec > 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_capture_argv_preserves_space_arguments(tmp_path: Path) -> None:
    python_exe = str(Path(sys.executable)).replace("\\", "/")
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(4)
    args = str_array_push(args, "{python_exe}")
    args = str_array_push(args, "-c")
    args = str_array_push(args, "import sys; print(sys.argv[1])")
    args = str_array_push(args, "two words")
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    out = process_capture_argv_env_redirs(args, env, ops, targets)
    rc = process_last_capture_status()
    ok = startswith(out, "two words")
    dealloc(out)
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    if ok == 1 and rc == 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_errno_class_helpers_are_available(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    enoent = process_errno_enoent()
    eacces = process_errno_eacces()
    eperm = process_errno_eperm()
    if enoent > 0 and eacces > 0 and eperm > 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_last_exec_errno_reports_enoexec_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    script = tmp_path / "plain_script"
    script.write_text("exit 0\n", encoding="utf-8")
    script.chmod(0o755)
    proc = _compile_run(
        """\
def main(): int
    args = str_array_new(1)
    args = str_array_push(args, "./plain_script")
    rc = process_run_argv(args)
    err = process_last_exec_errno()
    enoexec = process_errno_enoexec()
    dealloc_str_array(args)
    if rc == 127 and err == enoexec then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_pipeline_reports_tail_permission_errno_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    noexec = tmp_path / "noexec"
    noexec.write_bytes(b"\x00\x01not-a-script\n")
    noexec.chmod(0o644)
    proc = _compile_run(
        """\
def main(): int
    args = str_array_new(2)
    args = str_array_push(args, "true")
    args = str_array_push(args, "./noexec")
    counts = array_new(2)
    counts = array_push(counts, 1)
    counts = array_push(counts, 1)
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(2)
    redirs = array_push(redirs, 0)
    redirs = array_push(redirs, 0)
    rc = process_pipeline_argv_redirs(args, counts, ops, targets, redirs)
    err = process_last_exec_errno()
    eacces = process_errno_eacces()
    eperm = process_errno_eperm()
    dealloc_str_array(args)
    dealloc_array(counts)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redirs)
    if rc == 127 and (err == eacces or err == eperm) then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_run_argv_redirs_writes_file(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo redir")'
        setup2 = '\n    args2 = str_array_push(args2, "cmd.exe")\n    args2 = str_array_push(args2, "/C")\n    args2 = str_array_push(args2, "echo tail")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "printf redir")'
        setup2 = '\n    args2 = str_array_push(args2, "sh")\n    args2 = str_array_push(args2, "-c")\n    args2 = str_array_push(args2, "printf tail")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(3){setup}
    ops = str_array_new(1)
    ops = str_array_push(ops, ">")
    targets = str_array_new(1)
    targets = str_array_push(targets, "redir_probe.txt")
    rc = process_run_argv_redirs(args, ops, targets)
    dealloc_str_array(args)
    dealloc_str_array(ops)
    if rc != 0 then
        dealloc_str_array(targets)
        return rc
    end
    args2 = str_array_new(3){setup2}
    append_ops = str_array_new(1)
    append_ops = str_array_push(append_ops, ">>")
    rc2 = process_run_argv_redirs(args2, append_ops, targets)
    dealloc_str_array(args2)
    dealloc_str_array(append_ops)
    dealloc_str_array(targets)
    if rc2 != 0 then
        return rc2
    end
    text = read_file("redir_probe.txt")
    ok = startswith(text, "redir")
    tail = index_of(text, "tail")
    dealloc(text)
    if ok == 1 and tail != -1 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_run_argv_redirs_feeds_heredoc_stdin(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "findstr hello")'
    else:
        setup = '\n    args = str_array_push(args, "grep")\n    args = str_array_push(args, "hello")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(3){setup}
    ops = str_array_new(1)
    ops = str_array_push(ops, "<<")
    targets = str_array_new(1)
    targets = str_array_push(targets, "hello\\r")
    rc = process_run_argv_redirs(args, ops, targets)
    dealloc_str_array(args)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_capture_argv_env_redirs_captures_stdout(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo capture")'
    else:
        setup = '\n    args = str_array_push(args, "printf")\n    args = str_array_push(args, "capture")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(3){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    out = process_capture_argv_env_redirs(args, env, ops, targets)
    rc = process_last_capture_status()
    ok = startswith(out, "capture")
    dealloc(out)
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    if ok == 1 and rc == 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_capture_argv_env_redirs_reports_child_status(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo nope && exit 7")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "printf nope; exit 7")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(3){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    out = process_capture_argv_env_redirs(args, env, ops, targets)
    rc = process_last_capture_status()
    ok = startswith(out, "nope")
    dealloc(out)
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    if ok == 1 and rc == 7 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_capture_argv_env_redirs_supports_input_offset(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "more")'
    else:
        setup = '\n    args = str_array_push(args, "cat")'
    proc = _compile_run(
        f"""\
def main(): int
    write_file("input.txt", "ABC")
    args = str_array_new(3){setup}
    env = str_array_new(0)
    ops = str_array_new(1)
    ops = str_array_push(ops, "<@1")
    targets = str_array_new(1)
    targets = str_array_push(targets, "input.txt")
    out = process_capture_argv_env_redirs(args, env, ops, targets)
    rc = process_last_capture_status()
    ok = index_of(out, "BC")
    dealloc(out)
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    if ok >= 0 and rc == 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_set_last_capture_status_updates_runtime_status(tmp_path: Path) -> None:
    proc = _compile_run(
        """\
def main(): int
    process_set_last_capture_status(126)
    rc = process_last_capture_status()
    if rc == 126 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_capture_pipeline_argv_redirs_captures_last_stdout(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo pipe")\n    args = str_array_push(args, "findstr")\n    args = str_array_push(args, "pipe")'
        counts = [3, 2]
    else:
        setup = '\n    args = str_array_push(args, "printf")\n    args = str_array_push(args, "pipe")\n    args = str_array_push(args, "grep")\n    args = str_array_push(args, "pipe")'
        counts = [2, 2]
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(5){setup}
    counts = array_new(2)
    counts = array_push(counts, {counts[0]})
    counts = array_push(counts, {counts[1]})
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(2)
    redirs = array_push(redirs, 0)
    redirs = array_push(redirs, 0)
    out = process_capture_pipeline_argv_redirs(args, counts, ops, targets, redirs)
    rc = process_last_capture_status()
    ok = startswith(out, "pipe")
    dealloc(out)
    dealloc_str_array(args)
    dealloc_array(counts)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redirs)
    if ok == 1 and rc == 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_capture_pipeline_argv_env_redirs_passes_env(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo %AILANG_PIPE_ENV_UNIT%")\n    args = str_array_push(args, "findstr")\n    args = str_array_push(args, "env-unit")'
        counts = [3, 2]
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "printf \\"%s\\\\n\\" \\"$AILANG_PIPE_ENV_UNIT\\"")\n    args = str_array_push(args, "grep")\n    args = str_array_push(args, "env-unit")'
        counts = [3, 2]
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(5){setup}
    counts = array_new(2)
    counts = array_push(counts, {counts[0]})
    counts = array_push(counts, {counts[1]})
    env = str_array_new(1)
    env = str_array_push(env, "AILANG_PIPE_ENV_UNIT=env-unit")
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(2)
    redirs = array_push(redirs, 0)
    redirs = array_push(redirs, 0)
    out = process_capture_pipeline_argv_env_redirs(args, counts, env, ops, targets, redirs)
    rc = process_last_capture_status()
    ok = startswith(out, "env-unit")
    dealloc(out)
    dealloc_str_array(args)
    dealloc_array(counts)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redirs)
    if ok == 1 and rc == 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_pipe_argv_redirs_returns_right_status(tmp_path: Path) -> None:
    if os.name == "nt":
        left_setup = '\n    left = str_array_push(left, "cmd.exe")\n    left = str_array_push(left, "/C")\n    left = str_array_push(left, "echo hello")'
        right_setup = '\n    right = str_array_push(right, "findstr")\n    right = str_array_push(right, "hello")'
    else:
        left_setup = '\n    left = str_array_push(left, "printf")\n    left = str_array_push(left, "hello")'
        right_setup = '\n    right = str_array_push(right, "grep")\n    right = str_array_push(right, "hello")'
    proc = _compile_run(
        f"""\
def main(): int
    left = str_array_new(3){left_setup}
    right = str_array_new(2){right_setup}
    empty_ops = str_array_new(0)
    empty_targets = str_array_new(0)
    rc = process_pipe_argv_redirs(left, empty_ops, empty_targets, right, empty_ops, empty_targets)
    dealloc_str_array(left)
    dealloc_str_array(right)
    dealloc_str_array(empty_ops)
    dealloc_str_array(empty_targets)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_pipeline_argv_redirs_runs_three_stage_pipeline(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo hello")\n    args = str_array_push(args, "findstr")\n    args = str_array_push(args, "hello")\n    args = str_array_push(args, "findstr")\n    args = str_array_push(args, "hello")'
        counts = [3, 2, 2]
    else:
        setup = '\n    args = str_array_push(args, "printf")\n    args = str_array_push(args, "hello")\n    args = str_array_push(args, "grep")\n    args = str_array_push(args, "hello")\n    args = str_array_push(args, "grep")\n    args = str_array_push(args, "hello")'
        counts = [2, 2, 2]
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(8){setup}
    arg_counts = array_new(3)
    arg_counts = array_push(arg_counts, {counts[0]})
    arg_counts = array_push(arg_counts, {counts[1]})
    arg_counts = array_push(arg_counts, {counts[2]})
    ops = str_array_new(0)
    targets = str_array_new(0)
    redir_counts = array_new(3)
    redir_counts = array_push(redir_counts, 0)
    redir_counts = array_push(redir_counts, 0)
    redir_counts = array_push(redir_counts, 0)
    rc = process_pipeline_argv_redirs(args, arg_counts, ops, targets, redir_counts)
    dealloc_str_array(args)
    dealloc_array(arg_counts)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redir_counts)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_pipeline_argv_env_redirs_passes_env(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo %AILANG_PIPE_ENV_UNIT%")\n    args = str_array_push(args, "findstr")\n    args = str_array_push(args, "env-unit")'
        counts = [3, 2]
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "printf \\"%s\\\\n\\" \\"$AILANG_PIPE_ENV_UNIT\\"")\n    args = str_array_push(args, "grep")\n    args = str_array_push(args, "env-unit")'
        counts = [3, 2]
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(5){setup}
    counts = array_new(2)
    counts = array_push(counts, {counts[0]})
    counts = array_push(counts, {counts[1]})
    env = str_array_new(1)
    env = str_array_push(env, "AILANG_PIPE_ENV_UNIT=env-unit")
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(2)
    redirs = array_push(redirs, 0)
    redirs = array_push(redirs, 0)
    rc = process_pipeline_argv_env_redirs(args, counts, env, ops, targets, redirs)
    dealloc_str_array(args)
    dealloc_array(counts)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redirs)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_spawn_pipeline_argv_env_redirs_waits_tail(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "echo hello")\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "exit 5")'
        counts = [3, 3]
    else:
        setup = '\n    args = str_array_push(args, "printf")\n    args = str_array_push(args, "hello")\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "exit 5")'
        counts = [2, 3]
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(6){setup}
    counts = array_new(2)
    counts = array_push(counts, {counts[0]})
    counts = array_push(counts, {counts[1]})
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(2)
    redirs = array_push(redirs, 0)
    redirs = array_push(redirs, 0)
    pid = process_spawn_pipeline_argv_env_redirs(args, counts, env, ops, targets, redirs)
    status = 1
    if pid > 0 then
        status = process_wait_pid(pid)
    end
    dealloc_str_array(args)
    dealloc_array(counts)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redirs)
    return status
end
""",
        tmp_path,
    )
    assert proc.returncode == 5, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_spawn_wait_returns_child_exit_status(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "exit")\n    args = str_array_push(args, "7")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "exit 7")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(4){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    pid = process_spawn_argv_env_redirs(args, env, ops, targets)
    status = 1
    if pid > 0 then
        status = process_wait_pid(pid)
    end
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    return status
end
""",
        tmp_path,
    )
    assert proc.returncode == 7, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_poll_observes_completed_child(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "exit")\n    args = str_array_push(args, "3")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "exit 3")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(4){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    pid = process_spawn_argv_env_redirs(args, env, ops, targets)
    status = -1
    tries = 0
    while pid > 0 and status == -1 and tries < 200 then
        sleep_ms(10)
        status = process_poll_pid(pid)
        tries++
    end
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    return status
end
""",
        tmp_path,
    )
    assert proc.returncode == 3, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_kill_terminates_spawned_child(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "ping -n 5 127.0.0.1 > NUL")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "sleep 5")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(3){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    pid = process_spawn_argv_env_redirs(args, env, ops, targets)
    if pid <= 0 then
        dealloc_str_array(args)
        dealloc_str_array(env)
        dealloc_str_array(ops)
        dealloc_str_array(targets)
        return 1
    end
    sleep_ms(100)
    kill_rc = process_kill_pid(pid, 15)
    status = process_wait_pid(pid)
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    if kill_rc == 0 and status != 0 then
        return 0
    end
    return 1
end
""",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

def test_process_exec_replace_runs_target(tmp_path: Path) -> None:
    if os.name == "nt":
        setup = '\n    args = str_array_push(args, "cmd.exe")\n    args = str_array_push(args, "/C")\n    args = str_array_push(args, "exit")\n    args = str_array_push(args, "9")'
    else:
        setup = '\n    args = str_array_push(args, "sh")\n    args = str_array_push(args, "-c")\n    args = str_array_push(args, "exit 9")'
    proc = _compile_run(
        f"""\
def main(): int
    args = str_array_new(4){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    rc = process_exec_replace_argv_env_redirs(args, env, ops, targets)
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    return rc
end
""",
        tmp_path,
    )
    assert proc.returncode == 9, proc.stderr
    assert "POSSIBLE LEAK" not in proc.stderr

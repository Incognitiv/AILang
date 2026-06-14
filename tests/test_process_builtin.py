from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.core import CTranspiler  # noqa: E402

AILANG = REPO_ROOT / "ailang.py"


def _to_c_with_needs(src: str) -> tuple[str, object]:
    tokens = tokenize(src)
    parser = Parser(tokens)
    ast = parser.parse_program()
    transpiler = CTranspiler()
    c_code = transpiler.transpile(ast, "<inline>")
    return c_code, transpiler.runtime_needs


def test_getpid_c_backend_emits_process_runtime_helper() -> None:
    src = """
def main(): int
    return getpid() > 0
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "process" in needs.helpers
    assert "ailang_getpid" in c_code
    assert "ailang_getppid" in c_code


def test_process_identity_c_backend_emits_all_helpers() -> None:
    src = """
def main(): int
    return getppid() + getuid() + geteuid() + getgid() + getegid() + getgeid()
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "process" in needs.helpers
    assert "ailang_getppid" in c_code
    assert "ailang_getuid" in c_code
    assert "ailang_geteuid" in c_code
    assert "ailang_getgid" in c_code
    assert "ailang_getegid" in c_code


def test_process_umask_c_backend_emits_process_runtime_helper() -> None:
    src = """
def main(): int
    return process_umask(18)
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "process" in needs.helpers
    assert "ailang_process_umask" in c_code


def test_process_group_c_backend_emits_posix_job_control_helpers() -> None:
    src = """
def main(): int
    pid = getpid()
    pgid = process_get_pgrp(pid)
    rc = process_set_pgrp(0, pgid)
    fg = terminal_get_pgrp(0)
    set_rc = terminal_set_pgrp(0, fg)
    kill_rc = process_kill_pgrp(pgid, 0)
    args = str_array_new(0)
    counts = array_new(0)
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(0)
    spawn_rc = process_spawn_argv_env_redirs_pgrp(args, env, ops, targets, -1)
    pipeline_rc = process_spawn_pipeline_argv_env_redirs_pgrp(args, counts, env, ops, targets, redirs, -1)
    event_rc = process_wait_pid_event(0)
    return pgid + rc + fg + set_rc + kill_rc + spawn_rc + pipeline_rc + event_rc
end
"""
    c_code, needs = _to_c_with_needs(src)
    assert "process" in needs.helpers
    assert "ailang_process_get_pgrp" in c_code
    assert "ailang_process_set_pgrp" in c_code
    assert "ailang_process_kill_pgrp" in c_code
    assert "ailang_terminal_get_pgrp" in c_code
    assert "ailang_terminal_set_pgrp" in c_code
    assert "ailang_process_spawn_argv_env_redirs_pgrp" in c_code
    assert "ailang_process_spawn_pipeline_argv_env_redirs_pgrp" in c_code
    assert "ailang_process_wait_pid_event" in c_code


def test_process_identity_llvm_lowering_uses_target_process_api() -> None:
    src = """
def main(): int
    return getpid() + getppid() + getuid() + geteuid() + getgid() + getegid() + getgeid()
end
"""
    ir_text = compile_to_ir_fast(src, source_file="process_identity_probe.ail")
    if sys.platform == "win32":
        assert '@"GetCurrentProcessId"' in ir_text
        assert '@"getppid"' not in ir_text
    else:
        assert '@"getpid"' in ir_text
        assert '@"getppid"' in ir_text
        assert '@"getuid"' in ir_text
        assert '@"geteuid"' in ir_text
        assert '@"getgid"' in ir_text
        assert '@"getegid"' in ir_text


def test_process_umask_llvm_lowering_uses_target_process_api() -> None:
    src = """
def main(): int
    return process_umask(18)
end
"""
    ir_text = compile_to_ir_fast(src, source_file="process_umask_probe.ail")
    if sys.platform == "win32":
        assert '@"umask"' not in ir_text
    else:
        assert '@"umask"' in ir_text


def test_getpid_c_backend_native_smoke_compiles_and_runs() -> None:
    src = """
def main(): int
    if getpid() <= 0 then
        return 1
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "getpid_smoke.ail"
        out_stem = Path(td) / "getpid_smoke"
        src_path.write_text(src, encoding="utf-8")
        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src_path),
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
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr


def test_process_group_c_backend_native_smoke_compiles_and_runs() -> None:
    src = """
def main(): int
    pid = getpid()
    pgid = process_get_pgrp(pid)
    if pgid == -38 then
        return 0
    end
    if pgid <= 0 then
        return 1
    end
    rc = process_set_pgrp(0, pgid)
    if rc != 0 then
        return 2
    end
    kill_rc = process_kill_pgrp(pgid, 0)
    if kill_rc != 0 then
        return 3
    end
    tty_pgrp = terminal_get_pgrp(0)
    if tty_pgrp < 0 then
        return 0
    end
    set_rc = terminal_set_pgrp(0, tty_pgrp)
    if set_rc != 0 then
        return 4
    end
    return 0
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "process_group_smoke.ail"
        out_stem = Path(td) / "process_group_smoke"
        src_path.write_text(src, encoding="utf-8")
        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src_path),
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
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr


def test_process_group_spawn_event_native_smoke_compiles_and_runs() -> None:
    if os.name == "nt":
        setup = '''
    args = str_array_push(args, "cmd.exe")
    args = str_array_push(args, "/C")
    args = str_array_push(args, "exit 0")'''
        body = """
    pid = process_spawn_argv_env_redirs_pgrp(args, env, ops, targets, -1)
    status = 1
    if pid > 0 then
        status = process_wait_pid_event(pid)
    end
"""
    else:
        setup = '''
    args = str_array_push(args, "sh")
    args = str_array_push(args, "-c")
    args = str_array_push(args, "kill -STOP $$")'''
        body = """
    pid = process_spawn_argv_env_redirs_pgrp(args, env, ops, targets, 0)
    status = 1
    if pid > 0 then
        pgid = process_get_pgrp(pid)
        if pgid != pid then
            status = 2
        else
            stopped = process_wait_pid_event(pid)
            if stopped >= -1000 then
                status = 3
            else
                kill_rc = process_kill_pgrp(pgid, 9)
                done = process_wait_pid_event(pid)
                if kill_rc == 0 and done == 137 then
                    status = 0
                else
                    status = 4
                end
            end
        end
    end
"""
    src = f"""
def main(): int
    args = str_array_new(4){setup}
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
{body}
    dealloc_str_array(args)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    return status
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "process_group_spawn_event_smoke.ail"
        out_stem = Path(td) / "process_group_spawn_event_smoke"
        src_path.write_text(src, encoding="utf-8")
        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src_path),
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
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr


def test_process_group_pipeline_spawn_native_smoke_compiles_and_runs() -> None:
    if os.name == "nt":
        setup = '''
    args = str_array_push(args, "cmd.exe")
    args = str_array_push(args, "/C")
    args = str_array_push(args, "exit 0")
    args = str_array_push(args, "cmd.exe")
    args = str_array_push(args, "/C")
    args = str_array_push(args, "exit 0")'''
        body = """
    pid = process_spawn_pipeline_argv_env_redirs_pgrp(args, counts, env, ops, targets, redirs, -1)
    status = 1
    if pid > 0 then
        status = process_wait_pid_event(pid)
    end
"""
    else:
        setup = '''
    args = str_array_push(args, "sh")
    args = str_array_push(args, "-c")
    args = str_array_push(args, "sleep 0.2")
    args = str_array_push(args, "sh")
    args = str_array_push(args, "-c")
    args = str_array_push(args, "sleep 0.2")'''
        body = """
    pid = process_spawn_pipeline_argv_env_redirs_pgrp(args, counts, env, ops, targets, redirs, 0)
    status = 1
    if pid > 0 then
        pgid = process_get_pgrp(pid)
        shell_pgid = process_get_pgrp(0)
        if pgid <= 0 or pgid == shell_pgid then
            status = 2
        else
            status = process_wait_pid_event(pid)
        end
    end
"""
    src = f"""
def main(): int
    args = str_array_new(8){setup}
    counts = array_new(2)
    counts = array_push(counts, 3)
    counts = array_push(counts, 3)
    env = str_array_new(0)
    ops = str_array_new(0)
    targets = str_array_new(0)
    redirs = array_new(2)
    redirs = array_push(redirs, 0)
    redirs = array_push(redirs, 0)
{body}
    dealloc_str_array(args)
    dealloc_array(counts)
    dealloc_str_array(env)
    dealloc_str_array(ops)
    dealloc_str_array(targets)
    dealloc_array(redirs)
    return status
end
"""
    with tempfile.TemporaryDirectory() as td:
        src_path = Path(td) / "process_group_pipeline_spawn_smoke.ail"
        out_stem = Path(td) / "process_group_pipeline_spawn_smoke"
        src_path.write_text(src, encoding="utf-8")
        compile_proc = subprocess.run(
            [
                sys.executable,
                str(AILANG),
                str(src_path),
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
        run_proc = subprocess.run(
            [str(exe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run_proc.returncode == 0, run_proc.stderr

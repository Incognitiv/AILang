from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser import ast as A  # noqa: E402
from parser.parser import Parser  # noqa: E402

from diagnostics.effect_policy import collect_effect_policy_violations  # noqa: E402
from lexer.scan import tokenize  # noqa: E402
from runtime.modes import CompilationMode  # noqa: E402


def _parse_program(src: str) -> list[A.ASTNode]:
    tokens = tokenize(src)
    parser = Parser(tokens)
    return parser.parse_program()


def _first_function(nodes: list[A.ASTNode]) -> A.Function:
    for node in nodes:
        if isinstance(node, A.Function):
            return node
    raise AssertionError("no function node found")


def test_effect_decorator_arguments_are_parsed() -> None:
    src = """
@effect(fs, sqlite)
def load_data(): int
    return 0
end
"""
    program = _parse_program(src)
    fn = _first_function(program)
    assert "effect(fs,sqlite)" in fn.decorators


def test_effect_decorator_accepts_syscall_and_sqlite() -> None:
    src = """
@effect(syscall, sqlite)
def load_pid_config(): int
    syscall(39)
    return sql_open_readonly("file:test?mode=memory&cache=private")
end
"""
    program = _parse_program(src)
    fn = _first_function(program)
    assert "effect(syscall,sqlite)" in fn.decorators
    violations = collect_effect_policy_violations(program, CompilationMode.HOSTED)
    assert not violations


def test_missing_effect_is_reported() -> None:
    src = """
def load_data(): int
    read_file("x.txt")
    return 0
end
"""
    program = _parse_program(src)
    violations = collect_effect_policy_violations(program, CompilationMode.HOSTED)
    assert any(
        v.kind == "missing_effect" and v.required_effect == "fs" for v in violations
    )


def test_freestanding_blocks_hosted_file_ops() -> None:
    src = """
@effect(fs)
def load_data(): int
    read_file("x.txt")
    return 0
end
"""
    program = _parse_program(src)
    violations = collect_effect_policy_violations(
        program,
        CompilationMode.FREESTANDING,
    )
    assert any(v.kind == "mode_block" and v.required_effect == "fs" for v in violations)


def test_fd_ops_require_fs_effect_and_are_blocked_in_freestanding() -> None:
    missing_src = """
def fd_probe(): int
    return fd_open("x", 1, 0)
end
"""
    missing_program = _parse_program(missing_src)
    missing = collect_effect_policy_violations(missing_program, CompilationMode.HOSTED)
    assert any(v.kind == "missing_effect" and v.required_effect == "fs" for v in missing)

    blocked_src = """
@effect(fs)
def fd_probe(): int
    return fd_open("x", 1, 0)
end
"""
    blocked_program = _parse_program(blocked_src)
    blocked = collect_effect_policy_violations(
        blocked_program,
        CompilationMode.FREESTANDING,
    )
    assert any(v.kind == "mode_block" and v.required_effect == "fs" for v in blocked)


def test_missing_effect_for_mmio_call_is_reported() -> None:
    src = """
def poke_port(): int
    outb(100, 1)
    return 0
end
"""
    program = _parse_program(src)
    violations = collect_effect_policy_violations(program, CompilationMode.HOSTED)
    assert any(
        v.kind == "missing_effect" and v.required_effect == "mmio" for v in violations
    )


def test_missing_effect_for_syscall_is_reported() -> None:
    src = """
def get_pid(): int
    return syscall(39)
end
"""
    program = _parse_program(src)
    violations = collect_effect_policy_violations(program, CompilationMode.HOSTED)
    assert any(
        v.kind == "missing_effect" and v.required_effect == "syscall"
        for v in violations
    )


def test_freestanding_blocks_syscall_builtin() -> None:
    src = """
@effect(syscall)
def get_pid(): int
    return syscall(39)
end
"""
    program = _parse_program(src)
    violations = collect_effect_policy_violations(
        program,
        CompilationMode.FREESTANDING,
    )
    assert any(
        v.kind == "mode_block" and v.required_effect == "syscall"
        for v in violations
    )


def test_effect_policy_report_fails_when_violations_exist(tmp_path, capsys) -> None:
    from cli.reports import report_effect_policy

    src = tmp_path / "missing_effect.ail"
    src.write_text(
        """
def get_pid(): int
    return syscall(39)
end
""",
        encoding="utf-8",
    )

    assert report_effect_policy(str(src)) is False
    captured = capsys.readouterr()
    assert "violation_count=1" in captured.out


def test_check_accepts_multi_effect_decorator_names(tmp_path, capsys) -> None:
    from cli.diagnostics import run_diagnostics

    src = tmp_path / "effect_names.ail"
    src.write_text(
        """
@effect(syscall, sqlite)
def get_pid_config(): int
    syscall(39)
    return sql_open_readonly("file:test?mode=memory&cache=private")
end
""",
        encoding="utf-8",
    )

    assert run_diagnostics(str(src)) == 0
    captured = capsys.readouterr()
    assert "Unknown identifier" not in captured.out

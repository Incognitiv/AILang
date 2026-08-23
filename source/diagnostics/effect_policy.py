"""Effect/capability policy checks for hosted/freestanding modes."""

from __future__ import annotations

from dataclasses import dataclass
from parser import ast as A
from typing import Iterable

from runtime.modes import CompilationMode

from .diagnostics_models import Diagnostic

# Calls that require explicit effect declarations.
CALL_EFFECTS: dict[str, str] = {
    # Hosted file/runtime I/O.
    "input": "input",
    "read_stdin": "input",
    "read_file": "fs",
    "write_file": "fs",
    "file_size": "fs",
    "read_bytes": "fs",
    "write_bytes": "fs",
    "current_dir": "fs",
    "change_dir": "fs",
    "list_dir": "fs",
    "file_exists": "fs",
    "file_can_execute": "fs",
    "file_is_regular": "fs",
    "file_is_symlink": "fs",
    "file_is_block": "fs",
    "file_is_char": "fs",
    "file_is_fifo": "fs",
    "file_is_socket": "fs",
    "file_is_setuid": "fs",
    "file_is_setgid": "fs",
    "file_mtime": "fs",
    "file_same": "fs",
    "fd_is_tty": "fs",
    "access": "fs",
    "make_dir": "fs",
    "mkdir": "fs",
    "delete_file": "fs",
    "unlink": "fs",
    "move_file": "fs",
    "rename": "fs",
    "fd_open": "fs",
    "fd_read": "fs",
    "fd_write": "fs",
    "fd_close": "fs",
    "fd_dup": "fs",
    "fd_dup2": "fs",
    "fd_tell": "fs",
    "fd_seek": "fs",
    "fd_flush": "fs",
    "process_capture": "process",
    "process_run_argv": "process",
    "process_run_argv_redirs": "process",
    "process_run_argv_env_redirs": "process",
    "process_spawn_argv_env_redirs": "process",
    "process_spawn_argv_env_redirs_pgrp": "process",
    "process_wait_pid": "process",
    "process_wait_pid_event": "process",
    "process_poll_pid": "process",
    "process_kill_pid": "process",
    "process_get_pgrp": "process",
    "process_set_pgrp": "process",
    "process_kill_pgrp": "process",
    "terminal_get_pgrp": "process",
    "terminal_set_pgrp": "process",
    "process_exec_replace_argv_env_redirs": "process",
    "process_pipe_argv_redirs": "process",
    "process_pipeline_argv_redirs": "process",
    "process_pipeline_argv_env_redirs": "process",
    "process_spawn_pipeline_argv_env_redirs": "process",
    "process_spawn_pipeline_argv_env_redirs_pgrp": "process",
    "process_capture_pipeline_argv_redirs": "process",
    "process_capture_pipeline_argv_env_redirs": "process",
    "process_capture_argv_env_redirs": "process",
    "process_last_capture_status": "process",
    "process_set_last_capture_status": "process",
    "process_last_exec_errno": "process",
    "process_errno_enoexec": "process",
    "process_errno_enoent": "process",
    "process_errno_eacces": "process",
    "process_errno_eperm": "process",
    "signal_install": "process",
    "signal_ignore": "process",
    "signal_default": "process",
    "signal_pending": "process",
    "signal_clear": "process",
    "signal_drain": "process",
    "signal_raise": "process",
    # SQLite helpers.
    "sql_open": "sqlite",
    "sql_open_readonly": "sqlite",
    "sql_last_open_status": "sqlite",
    "sql_exec": "sqlite",
    "sql_close": "sqlite",
    "sql_prepare": "sqlite",
    "sql_step": "sqlite",
    "sql_bind_int": "sqlite",
    "sql_bind_text": "sqlite",
    "sql_bind_text_i64": "sqlite",
    "sql_bind_text_i64_parts": "sqlite",
    "sql_bind_null": "sqlite",
    "sql_clear_bindings": "sqlite",
    "sql_column_int": "sqlite",
    "sql_column_text": "sqlite",
    "sql_finalize": "sqlite",
    # Native target syscall boundary.
    "syscall": "syscall",
    # Low-level hardware/asm.
    "outb": "mmio",
    "inb": "mmio",
}

# Mode policy baseline.
MODE_BLOCKED_EFFECTS: dict[CompilationMode, set[str]] = {
    CompilationMode.HOSTED: set(),
    CompilationMode.FREESTANDING: {"fs", "sqlite", "input", "process", "syscall"},
}


@dataclass(frozen=True)
class EffectViolation:
    """Structured violation record used by CLI reporting."""

    kind: str
    function: str
    operation: str
    required_effect: str
    line: int
    column: int
    message: str
    suggestion: str


def parse_effect_decorators(decorators: Iterable[str] | None) -> set[str]:
    """Extract ``@effect(...)`` names from a decorator list."""
    if not decorators:
        return set()
    effects: set[str] = set()
    for raw in decorators:
        text = str(raw or "").strip().lower()
        if not text.startswith("effect(") or not text.endswith(")"):
            continue
        inner = text[len("effect(") : -1].strip()
        if not inner:
            continue
        for part in inner.split(","):
            item = part.strip().lower()
            if item:
                effects.add(item)
    return effects


def collect_effect_policy_violations(
    program: list[A.ASTNode],
    mode: CompilationMode,
) -> list[EffectViolation]:
    """Collect capability/effect violations for all functions."""
    violations: list[EffectViolation] = []

    for node in program:
        if isinstance(node, A.Function):
            violations.extend(_collect_fn_violations(node, mode))
        elif isinstance(node, A.GenericFunction):
            violations.extend(_collect_generic_fn_violations(node, mode))
        elif isinstance(node, A.ClassDef):
            for method in getattr(node, "methods", []):
                if isinstance(method, A.Function):
                    fn_name = f"{node.name}.{method.name}"
                    violations.extend(_collect_fn_violations(method, mode, fn_name))

    return violations


def violations_to_diagnostics(
    violations: Iterable[EffectViolation],
) -> list[Diagnostic]:
    """Convert violations to regular diagnostic objects."""
    diagnostics: list[Diagnostic] = []
    for row in violations:
        severity = "warning" if row.kind == "missing_effect" else "error"
        diagnostics.append(
            Diagnostic(
                line=max(1, int(row.line or 1)),
                column=max(1, int(row.column or 1)),
                message=row.message,
                suggestion=row.suggestion,
                severity=severity,
            )
        )
    return diagnostics


def _collect_generic_fn_violations(
    node: A.GenericFunction,
    mode: CompilationMode,
) -> list[EffectViolation]:
    name = getattr(node, "name", "<anonymous>")
    decorators = getattr(node, "decorators", []) or []
    body = getattr(node, "body", []) or []
    return _scan_function_body(name, decorators, body, mode)


def _collect_fn_violations(
    node: A.Function,
    mode: CompilationMode,
    display_name: str | None = None,
) -> list[EffectViolation]:
    name = display_name or node.name
    return _scan_function_body(name, node.decorators, node.body, mode)


def _scan_function_body(
    function_name: str,
    decorators: Iterable[str] | None,
    body: list[A.ASTNode],
    mode: CompilationMode,
) -> list[EffectViolation]:
    declared_effects = parse_effect_decorators(decorators)
    rows: list[EffectViolation] = []
    for stmt in body:
        for node in _walk_nodes(stmt):
            op_name: str | None = None
            required_effect: str | None = None
            line = int(getattr(node, "line", 0) or 0)
            col = int(getattr(node, "col", 0) or 0)

            if isinstance(node, A.Call):
                op_name = node.name
                required_effect = CALL_EFFECTS.get(node.name)
            elif isinstance(node, A.InlineAsm):
                op_name = "asm"
                required_effect = "mmio"

            if op_name is None or required_effect is None:
                continue

            if required_effect not in declared_effects:
                rows.append(
                    EffectViolation(
                        kind="missing_effect",
                        function=function_name,
                        operation=op_name,
                        required_effect=required_effect,
                        line=line,
                        column=col,
                        message=(
                            f"Function '{function_name}' uses '{op_name}' "
                            f"but is missing @effect({required_effect})."
                        ),
                        suggestion=(
                            f"Add @effect({required_effect}) to '{function_name}' "
                            "or move this operation to a capability wrapper."
                        ),
                    )
                )

            blocked = MODE_BLOCKED_EFFECTS.get(mode, set())
            if required_effect in blocked:
                mode_name = (
                    "hosted" if mode == CompilationMode.HOSTED else "freestanding"
                )
                if required_effect == "mmio":
                    hint = "Use --mode=freestanding for hardware/asm operations."
                else:
                    hint = "Use --mode=hosted or remove hosted-only operations."
                rows.append(
                    EffectViolation(
                        kind="mode_block",
                        function=function_name,
                        operation=op_name,
                        required_effect=required_effect,
                        line=line,
                        column=col,
                        message=(
                            f"Operation '{op_name}' is not allowed in "
                            f"{mode_name} mode (requires effect '{required_effect}')."
                        ),
                        suggestion=hint,
                    )
                )
    return rows


def _walk_nodes(node: A.ASTNode) -> Iterable[A.ASTNode]:
    """Yield a node and all AST descendants (best-effort reflective walk)."""
    if not isinstance(node, A.ASTNode):
        return
    yield node
    for value in vars(node).values():
        if isinstance(value, A.ASTNode):
            yield from _walk_nodes(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, A.ASTNode):
                    yield from _walk_nodes(item)
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, A.ASTNode):
                            yield from _walk_nodes(sub)
                        elif isinstance(sub, list):
                            for nested in sub:
                                if isinstance(nested, A.ASTNode):
                                    yield from _walk_nodes(nested)

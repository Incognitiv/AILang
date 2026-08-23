"""Models and enums for AILang static analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NullState(Enum):
    """Tracks whether a variable is null, non-null, or unknown."""

    NULL = "null"
    NOT_NULL = "not_null"
    MAYBE_NULL = "maybe_null"


class AccessType(Enum):
    """Type of variable access for race detection."""

    READ = "read"
    WRITE = "write"
    ATOMIC_READ = "atomic_read"
    ATOMIC_WRITE = "atomic_write"
    CHANNEL_SEND = "channel_send"
    CHANNEL_RECV = "channel_recv"


@dataclass
class VariableAccess:
    """Records a single access to a variable."""

    var_name: str
    access_type: AccessType
    function_name: str
    is_concurrent: bool
    line: int = 0


@dataclass
class AnalysisWarning:
    """A warning from static analysis."""

    line: int
    column: int
    category: str
    message: str
    suggestion: str
    severity: str = "warning"

    def __str__(self) -> str:
        prefix = "[!]" if self.severity == "error" else "[?]"
        return (
            f"{prefix} [{self.category.upper()}] Line {self.line}: {self.message}\n"
            f"       Suggestion: {self.suggestion}"
        )


@dataclass
class FunctionContext:
    """Analysis context for a single function."""

    name: str
    is_spawned: bool = False
    is_parallel: bool = False
    is_async: bool = False
    null_states: dict[str, NullState] = field(default_factory=dict)
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    read_lines: dict[str, int] = field(default_factory=dict)
    write_lines: dict[str, int] = field(default_factory=dict)
    locals: set[str] = field(default_factory=set)
    params: set[str] = field(default_factory=set)
    atomic_reads: set[str] = field(default_factory=set)
    atomic_writes: set[str] = field(default_factory=set)
    channel_vars: set[str] = field(default_factory=set)

    def set_null_state(self, var_name: str, state: NullState) -> None:
        self.null_states[var_name] = state

    def get_null_state(self, var_name: str) -> NullState:
        return self.null_states.get(var_name, NullState.MAYBE_NULL)

    def is_concurrent(self) -> bool:
        return self.is_spawned or self.is_parallel or self.is_async

    def is_local_var(self, var_name: str) -> bool:
        return var_name in self.locals or var_name in self.params

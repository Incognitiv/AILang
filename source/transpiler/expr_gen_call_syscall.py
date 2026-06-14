"""C expression lowering for syscall builtins."""

from __future__ import annotations


def _emit_syscall_call(self, call_args: list[str]) -> str:
    """Lower syscall(number, ...) to the padded C runtime helper."""
    if not 1 <= len(call_args) <= 7:
        raise ValueError("syscall() expects a syscall number and up to 6 arguments")
    padded = [
        f"(int64_t)(intptr_t)({arg})" if index > 0 else f"(int64_t)({arg})"
        for index, arg in enumerate(call_args)
    ] + ["0"] * (7 - len(call_args))
    return (
        "ailang_syscall_native("
        f"{padded[0]}, {padded[1]}, {padded[2]}, {padded[3]}, "
        f"{padded[4]}, {padded[5]}, {padded[6]})"
    )
